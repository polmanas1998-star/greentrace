"""Baseline: NVIDIA Nemotron alone on the 21 trap cases, with the same data and the rules in its prompt.

    python -m scripts.baseline_model_alone          # needs MODEL_API_KEY in .env (NVIDIA direct)
    python -m scripts.baseline_model_alone --save   # also writes data/bench_model_alone.json
    python -m scripts.baseline_model_alone --runs 3 --save   # three independent passes, all kept

One model call per case, deep lane, temperature 0: the model gets every table the
investigation can read for that case (minus the sources the case takes down, as the
agents would find them), the claim, and the verdict rules of app/rules.py word for
word. No agents, no queries, no code checks. It is the "one model with everything"
baseline the GreenTrace score is compared with in the README.
"""
import io
import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from app import rules
from app.adapters.llm import ModelGate
from app.memory.fallback import MemoryEngine
from app.pipeline import run_case
from app.world.cases import CASES, DEFAULT_CLAIM, build_case
from app.world.store import DATA_DIR

OUT = DATA_DIR / "bench_model_alone.json"
PAUSE_S = 20  # each call carries ~20,000 tokens: spaced so NVIDIA's free tier does not throttle
VERDICTS = (rules.PROVEN, rules.CONTRADICTED, rules.UNVERIFIABLE)
# which table a case's unavailable source takes away (app/queries.py SOURCE_OF names)
SOURCE_TABLES = {"alerts": ["alerts"], "registry": ["suppliers", "plots"], "deliveries": ["deliveries"],
                 "reports": ["report_paragraphs"]}
SYSTEM = (
    "You check a cocoa company's deforestation-free promise for the EU Deforestation Regulation "
    "(cut-off 31 December 2020). Decide from the data only, applying these verdict rules exactly:\n"
    + rules.__doc__.split("Rules, in the kit's order:", 1)[1].split('"""')[0]
    + "\nAnswer with one verdict for the whole promise and the ids of the rows that decide it."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "reason": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "reason"],
}


def case_data(number: int) -> tuple[str, list[tuple[str, str]], str]:
    world = build_case(number)
    meta = world.pop("meta")
    for source in meta["unavailable_sources"]:
        for table in SOURCE_TABLES.get(source, [source]):
            world.pop(table, None)
            world[f"{table}_status"] = "source unavailable: it did not answer"
    blocks = [(name, json.dumps(rows, ensure_ascii=False, separators=(",", ":"))) for name, rows in world.items()]
    return meta["claim"], blocks, meta["expected"]


def deciding_ids(number: int) -> tuple[str, set[str]]:
    """GreenTrace's own verdict on the case (rules alone) and the row ids its deciding findings cite."""
    out = run_case({"run_id": f"baseline-ref-{number:02d}", "input": {"claim": DEFAULT_CLAIM},
                    "options": {"sample_mode": True, "case": number}}, stream=io.StringIO(), memory_engine=MemoryEngine())
    ids = {e["id"] for f in out.get("findings") or [] if f.get("verdict") == out.get("verdict")
           for e in f.get("evidence") or []}
    return out.get("verdict"), ids


ROW_ID = re.compile(r"^[ADFP]-\d+$")  # an alert, delivery, supplier or plot row


def row_evidence(expected: str, ref_verdict: str, ref_ids: set[str]) -> bool:
    """Is the evidence scored? Only when GreenTrace reaches the expected verdict on named rows: not for
    PROVEN (nothing decides it), not when the rules alone miss the case, and not when the deciding item
    is a whole source or the company (source down, untraced volume), which a row id cannot name."""
    return expected != rules.PROVEN and ref_verdict == expected and bool(ref_ids) and all(map(ROW_ID.match, ref_ids))


def run_once(gate: ModelGate, references: dict[int, tuple[str, set[str]]]) -> dict:
    results = []
    for i, case in enumerate(CASES):
        if i:
            time.sleep(PAUSE_S)
        claim, blocks, expected = case_data(case.number)
        ref_verdict, ref_ids = references[case.number]
        started = time.perf_counter()
        answer, info = gate.complete_json("Baseline", SYSTEM, [("claim", claim), *blocks], SCHEMA, max_tokens=1200)
        answer = answer if isinstance(answer, dict) else {}
        got, cited = answer.get("verdict"), set(answer.get("evidence_ids") or [])
        # the evidence counts as right when GreenTrace's deciding rows are among those cited
        evidence_ok = None if not row_evidence(expected, ref_verdict, ref_ids) else ref_ids <= cited
        results.append({"case": case.number, "slug": case.slug, "expected": expected, "got": got,
                        "ok": got == expected, "false_accusation": got == rules.CONTRADICTED != expected,
                        "evidence_ok": evidence_ok, "cited": sorted(cited), "reference_ids": sorted(ref_ids),
                        "reason": answer.get("reason"), "provider": info.get("provider"), "model": info.get("model"),
                        "seconds": round(time.perf_counter() - started, 1)})
        r = results[-1]
        flag = "ok  " if r["ok"] else ("FAIL" if got is None else "MISS")
        ev = "" if evidence_ok is None else (" evidence ok" if evidence_ok else " WRONG EVIDENCE")
        print(f"{flag} {r['case']:02d} {r['slug']:<32} expected {expected:<13} got {got}{ev}", flush=True)
    checked = [r for r in results if r["evidence_ok"] is not None]
    run = {"score": sum(r["ok"] for r in results), "false_accusations": sum(r["false_accusation"] for r in results),
           "right_evidence": sum(1 for r in checked if r["ok"] and r["evidence_ok"]), "evidence_checked": len(checked),
           "failed": [r["case"] for r in results if r["got"] is None], "results": results}
    print(f"Nemotron alone (same data, rules in the prompt): {run['score']}/{len(results)} verdicts, "
          f"{run['false_accusations']} false accusation(s); right verdict citing the deciding rows: "
          f"{run['right_evidence']}/{run['evidence_checked']}"
          + (f"; calls that failed: {run['failed']}" if run["failed"] else "") + "\n", flush=True)
    return run


def main(argv: list[str]) -> int:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    gate = ModelGate("baseline-model-alone", env={**os.environ, "SAMPLE_MODE": "false"}, log=lambda record: None)
    if not gate.providers:
        print("no model configured (MODEL_API_KEY in .env): nothing to measure")
        return 2
    n_runs = int(argv[argv.index("--runs") + 1]) if "--runs" in argv else 1
    references = {case.number: deciding_ids(case.number) for case in CASES}
    runs = [run_once(gate, references) for _ in range(n_runs)]
    if "--save" in argv:
        if any(run["failed"] for run in runs):
            print("not saved: some calls failed, a partial run is not a measurement")
            return 1
        OUT.write_text(json.dumps({"measured_on": time.strftime("%Y-%m-%d"),
                                   "model": next((r["model"] for r in runs[0]["results"] if r["model"]), None),
                                   "scores": [run["score"] for run in runs],
                                   "false_accusations": [run["false_accusations"] for run in runs],
                                   "right_evidence": [run["right_evidence"] for run in runs],
                                   "evidence_checked": runs[0]["evidence_checked"], "runs": runs},
                                  indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
