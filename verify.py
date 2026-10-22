"""Re-derive a GreenTrace verdict from its receipt, offline.

    python verify.py receipt.json

Uses only GreenTrace's own code and dependencies: no model, no database, no network. It
recomputes every hash, finds every quote word for word in the paragraph it cites,
rebuilds the evidence from the rows, applies app/rules.py and replays the flip.

  VALID      exit 0: the verdict follows from the rows in the receipt
  TAMPERED   exit 1: what differs, and the verdict the rules give on the data as found
  UNREADABLE exit 2: not a GreenTrace receipt

It checks integrity and re-derivation, it is not a signature: compare the seal it prints
with the verdict_sha256 the app printed when the run finished.
"""
import json
import sys
from pathlib import Path
from typing import Any

from app.agents.investigator import evidence_from_answers
from app.canonical import canonical_json, sha256_of
from app.explain.flip import compute_flip
from app.orchestrator import apply_change
from app.receipt import RECEIPT_VERSION, decision_for, evidence_answers, rules_sha256, verdict_seal
from app.rules import Extracted

LABELS = ("alert_id", "plot_id", "delivery_id", "supplier_id", "doc_id", "rule")


def check(receipt: dict[str, Any]) -> dict[str, Any]:
    """{"status": "VALID" or "TAMPERED", "problems": [...], "verdict": recomputed, "seal": recomputed}."""
    problems = _integrity(receipt) + _quotes(receipt)
    if receipt["rules_sha256"] != rules_sha256():
        problems.append("rules: this checkout's app/rules.py is not the one the receipt was made with")

    extracted = receipt["extracted"]
    evidence = evidence_from_answers(
        evidence_answers(receipt["sources"]),
        Extracted(scope=extracted["scope"], admissions=tuple(extracted["admissions"]),
                  mentions=tuple(extracted["mentions"])),
        receipt["company_id"],
    )
    for change in receipt["what_if"]:
        kind, _, key = change["entity"].partition(":")
        evidence = apply_change(evidence, kind, key, change["field"], change["value"])
    decision = decision_for(evidence, receipt["verdict"]["unreviewed_cards"])
    seal = verdict_seal(decision)
    if seal != receipt["verdict_sha256"]:
        problems.append(f"verdict: the rules give {decision['verdict']} on these rows, "
                        f"the receipt says {receipt['verdict']['overall']}")
    flip = compute_flip(evidence)
    if canonical_json([flip["tried"], flip["first_flip"]]) != canonical_json(
            [receipt["flip"]["tried"], receipt["flip"]["first_flip"]]):
        problems.append("flip: replaying the single changes gives another result")
    return {"status": "TAMPERED" if problems else "VALID", "problems": problems,
            "verdict": decision["verdict"], "seal": seal, "changes_replayed": len(flip["tried"])}


def _integrity(receipt: dict) -> list[str]:
    problems = []
    for source in receipt["sources"]:
        rows, hashes = source["rows"], source["row_sha256"]
        changed = [n for n, row in enumerate(rows) if n >= len(hashes) or sha256_of(row) != hashes[n]]
        problems += [f"{source['query_id']} row {n}{_label(rows[n])}" for n in changed]
        if len(rows) != len(hashes):
            problems.append(f"{source['query_id']}: {len(rows)} row(s), the receipt hashed {len(hashes)}")
        elif not changed and sha256_of(rows) != source["rows_sha256"]:
            problems.append(f"{source['query_id']}: rows_sha256")
    answers = [{"query_id": s["query_id"], "rows_sha256": s["rows_sha256"]} for s in receipt["sources"]]
    if sha256_of(answers) != receipt["data_sha256"]:
        problems.append("data_sha256: answers added, removed or re-hashed")
    return problems


def _label(row: dict) -> str:
    key = next((k for k in LABELS if k in row), None)
    return f" ({key.split('_')[0]} {row[key]})" if key else ""


def _quotes(receipt: dict) -> list[str]:
    """Every quote the model gave must sit word for word in the paragraph it cites (rule reads have none)."""
    paragraphs = {(r["doc_id"], r["section"], r["paragraph_no"]): r["paragraph_text"]
                  for s in receipt["sources"] if s["query_id"] == "Q1" for r in s["rows"]}
    problems = []
    for item in receipt["extracted"]["admissions"] + receipt["extracted"]["mentions"]:
        if not item.get("quote"):
            continue
        key = (item["doc"], item.get("section", "main"), item["paragraph_no"])
        if " ".join(item["quote"].split()) not in " ".join(paragraphs.get(key, "").split()):
            problems.append(f"quote: not found word for word in {' '.join(map(str, key))}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python verify.py receipt.json")
        return 2
    try:
        receipt = json.loads(Path(argv[0]).read_text(encoding="utf-8-sig"))
        if receipt.get("receipt_version") != RECEIPT_VERSION:
            raise ValueError(f"receipt_version {receipt.get('receipt_version')!r}, expected {RECEIPT_VERSION!r}")
        result = check(receipt)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"UNREADABLE: {type(error).__name__}: {error}")
        return 2
    sources = receipt["sources"]
    if result["status"] == "VALID":
        quotes = sum(1 for i in receipt["extracted"]["admissions"] + receipt["extracted"]["mentions"] if i.get("quote"))
        print(f"VALID: {result['verdict']} re-derived offline from {sum(len(s['rows']) for s in sources)} rows "
              f"of {len(sources)} answers; {quotes} quote(s) found word for word; "
              f"flip replayed ({result['changes_replayed']} change(s)).")
        print(f"seal {result['seal']}")
        return 0
    print("TAMPERED: " + "; ".join(result["problems"]))
    print(f"verdict recomputed on the data as found: {result['verdict']}")
    print(f"seal recomputed {result['seal']}, receipt says {receipt['verdict_sha256']}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
