"""The verdict receipt (kit v21, page 1): everything needed to re-derive a verdict offline.

receipt.json holds every answer the run got from Zetaris (query, SQL, rows, hashes), what
the Extractor read in the reports with its exact quotes, the verdict per claim part with
the rule that decided it, every change the flip tried, and the model calls (for
information only: verify.py never needs them). python verify.py receipt.json rebuilds the
evidence from the rows, applies app/rules.py and replays the flip: no model, no
database, no network.

It checks integrity and re-derivation. It is not a signature: the seal the app printed at
run time (verdict_sha256) is what a receipt is compared against.

package_zip() bundles it with the evidence file, the trace, the findings table, the plot
file and the supplier letters: the audit package an analyst files.
"""
import dataclasses
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.canonical import sha256_of
from app.explain import exports
from app.explain.flip import compute_flip
from app.rules import RULES_VERSION, UNVERIFIABLE, Evidence, decide
from app.tracing import LOG_DIR

RECEIPT_VERSION = "1"
RULES_FILE = Path(__file__).resolve().parent / "rules.py"
EVIDENCE_QUERIES = ("Q0", "Q2", "Q3", "Q4", "Q6")  # what investigator.evidence_from_answers reads
ZIP_DATE = (1980, 1, 1, 0, 0, 0)  # fixed entry dates: the same files give the same archive


def rules_sha256() -> str:
    return sha256_of(RULES_FILE.read_text(encoding="utf-8"))


def decision_for(evidence: Evidence, unreviewed: list[str]) -> dict[str, Any]:
    """The rules' decision; a card still unreviewed after the last round leaves the claim UNVERIFIABLE (kit)."""
    decision = decide(evidence)
    return {**decision, "verdict": UNVERIFIABLE} if unreviewed else decision


def verdict_seal(decision: dict[str, Any]) -> str:
    """verdict_sha256: the verdict, the parts, and which findings rest on which refs. Nothing time-dependent."""
    return sha256_of({"verdict": decision["verdict"], "parts": decision["parts"],
                      "findings": [(f["code"], f["refs"]) for f in decision["findings"]]})


def evidence_answers(sources: list[dict]) -> dict[str, list[dict]]:
    """The Investigator's first answer to each evidence query, as evidence_from_answers takes them."""
    answers: dict[str, list[dict]] = {}
    for source in sources:
        if source["agent"] == "Investigator" and source["query_id"] in EVIDENCE_QUERIES:
            answers.setdefault(source["query_id"], source["rows"])
    return answers


def build_receipt(run: Any) -> dict[str, Any]:
    """The receipt of a finished run (an orchestrator.Investigation)."""
    ctx, out = run.ctx, run.output
    sources = [{"index": index, "query_id": a.query_id, "agent": a.agent, "sql": a.sql, "rows": a.rows,
                "rows_sha256": a.rows_sha256, "row_sha256": [sha256_of(row) for row in a.rows]}
               for index, a in enumerate(ctx.gate.answers)]
    extracted = dataclasses.asdict(run.evidence.extracted)
    unreviewed = [c["id"] for c in run.cards if c["id"] not in run.reviews]
    decision = decision_for(run.evidence, unreviewed)
    flip = compute_flip(run.evidence)
    return {
        "receipt_version": RECEIPT_VERSION,
        "run_id": ctx.run_id,
        "claim": (run.request.get("input") or run.request).get("claim"),
        "company_id": ctx.company_id,
        "mode": out["mode"],
        "degraded": out["degraded"],
        "input_sha256": out["fingerprints"]["input_sha256"],
        "data_sha256": sha256_of([{"query_id": s["query_id"], "rows_sha256": s["rows_sha256"]} for s in sources]),
        "rules_version": RULES_VERSION,
        "rules_sha256": rules_sha256(),
        "sources": sources,
        "documents": _documents(sources, extracted),
        "extracted": extracted,
        "what_if": list(run.changes),
        "verdict": {
            "overall": decision["verdict"],
            "parts": decision["parts"],
            "findings": [{"code": f["code"], "rule": f["rule"], "verdict": f["verdict"], "refs": list(f["refs"]),
                          "derived_from": _derived_from(f["refs"], sources)} for f in decision["findings"]],
            "unreviewed_cards": unreviewed,
        },
        "flip": {"tried": flip["tried"], "first_flip": flip["first_flip"], "sentence": flip["sentence"]},
        "model_calls": list(ctx.model.calls) if ctx.model is not None else [],
        "verdict_sha256": verdict_seal(decision),
    }


def _row_ref(source: dict, row: int) -> str:
    return f"{source['query_id']}#{source['index']} row {row}"


def _documents(sources: list[dict], extracted: dict) -> list[dict]:
    """The report paragraphs the Extractor's items cite, with the answer and row they come from."""
    cited = []
    for item in extracted["admissions"] + extracted["mentions"]:
        key = (item["doc"], item.get("section", "main"), item["paragraph_no"])
        for source in (s for s in sources if s["query_id"] == "Q1"):
            for number, row in enumerate(source["rows"]):
                if (row["doc_id"], row["section"], row["paragraph_no"]) == key:
                    entry = {"doc_id": key[0], "section": key[1], "paragraph_no": key[2],
                             "text": row["paragraph_text"], "source": _row_ref(source, number)}
                    if entry not in cited:
                        cited.append(entry)
    return cited


def _derived_from(refs: tuple[str, ...], sources: list[dict]) -> list[str]:
    """Where a finding comes from: the answer and row behind each of its refs."""
    match = {
        "alert": ("Q4", lambda row, key: row["alert_id"] == key),
        "plot": ("Q3", lambda row, key: row["plot_id"] == key),
        "supplier": ("Q2", lambda row, key: row["supplier_id"] == key),
        "company": ("Q2", lambda row, key: True),
        "delivery": ("Q6", lambda row, key: f"{row['plot_id']}:{row['year']}" == key),
        "doc": ("Q1", lambda row, key: f"{row['doc_id']}:{row['section']}:{row['paragraph_no']}" == key),
    }
    found = []
    for ref in refs:
        kind, _, key = ref.partition(":")
        query_id, same = match.get(kind, (None, None))
        source = next((s for s in sources if s["query_id"] == query_id
                       and (s["agent"] == "Investigator" or query_id == "Q1")), None)
        rows = [n for n, row in enumerate(source["rows"]) if same(row, key)] if source else []
        found += [_row_ref(source, n) for n in rows[:1 if kind == "company" else None]]
        if not rows:
            found.append(f"{ref}: no row" if query_id else f"{ref}: no answer")
    return found


# ---- the audit package ---------------------------------------------------------------------------

def package_zip(run: Any, log_dir: Optional[Path] = None) -> bytes:
    """One zip: evidence file, trace, findings table, plot file, supplier letters, receipt, manifest."""
    out = run.output
    trace_file = (log_dir or LOG_DIR) / f"{run.ctx.run_id}.jsonl"
    letters = "\n---\n\n".join(letter["letter"] for letter in out["supplier_letters"])
    files = {
        "evidence.md": exports.evidence_markdown(out).encode("utf-8"),
        "trace.jsonl": trace_file.read_bytes() if trace_file.exists() else b"",
        "findings.csv": exports.findings_csv(out).encode("utf-8-sig"),  # the BOM lets Excel read UTF-8
        "plots_no_issue_found.geojson": _json(out["eudr_export"]["plots_no_issue_found"]),
        "supplier_letters.md": (letters or "No supplier letter: no finding concerns a supplier.\n").encode("utf-8"),
        "receipt.json": _json(build_receipt(run)),
    }
    manifest = {
        "run_id": run.ctx.run_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "verdict": out["verdict"],
        "verdict_sha256": out["verdict_sha256"],
        "human_review": out["human_review"],
        "verify": "python verify.py receipt.json",
        "files": [{"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                  for name, data in files.items()],
    }
    files["manifest.json"] = _json(manifest)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(zipfile.ZipInfo(name, date_time=ZIP_DATE), data, compress_type=zipfile.ZIP_DEFLATED)
    return buffer.getvalue()


def _json(value: Any) -> bytes:
    return (json.dumps(value, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
