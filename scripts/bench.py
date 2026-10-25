"""Run the 21 trap cases end to end and score the verdicts against the expected ones.

    python -m scripts.bench            # sample mode (SQLite), prints the table and the score
    python -m scripts.bench --save     # also writes data/bench_sample.json (the committed baseline)
    python -m scripts.bench --model --save-receipts   # through Meterless Nightshift: keeps every signed
                                       # receipt (data/nightshift_receipts/) and the score (data/bench_nightshift.json)

Every run goes through the real agents. Without a model the agents use their rule
fallbacks, so this score is the "rules alone" baseline the README reports.
"""
import io
import json
import sys
import time

from dotenv import load_dotenv

from app import orchestrator
from app.memory.fallback import MemoryEngine
from app.pipeline import run_case
from app.world.cases import CASES, DEFAULT_CLAIM
from app.world.store import DATA_DIR

BASELINE = DATA_DIR / "bench_sample.json"
NIGHTSHIFT = DATA_DIR / "bench_nightshift.json"
RECEIPTS = DATA_DIR / "nightshift_receipts"


def run_all(use_model: bool = False, receipts: list | None = None) -> list[dict]:
    results = []
    for case in CASES:
        run_id = f"bench-{case.number:02d}"
        out = run_case({"run_id": run_id, "input": {"claim": DEFAULT_CLAIM},
                        "options": {"sample_mode": True, "case": case.number, "use_model": use_model}},
                       stream=io.StringIO(), memory_engine=MemoryEngine())
        gate = orchestrator.RUNS[run_id].ctx.model
        calls = list(gate.calls) if gate is not None else []
        signed = [c["meterless_receipt"] for c in calls if c.get("meterless_receipt")]
        if receipts is not None and gate is not None:
            receipts += [{"case": case.number, **r} for r in gate.receipts]
        results.append({"case": case.number, "slug": case.slug, "expected": case.expected,
                        "got": out.get("verdict"), "ok": out.get("verdict") == case.expected,
                        "verdict_sha256": out.get("verdict_sha256"), "degraded": out.get("degraded"),
                        "model_calls": sum(c["provider"] != "rules" for c in calls),
                        "rules_fallbacks": sum(c["provider"] == "rules" for c in calls),
                        "receipts": len(signed), "receipts_valid": sum(r["signature_valid"] for r in signed),
                        "models": sorted({c["model"] for c in calls if c.get("model")})})
    return results


def main(argv: list[str]) -> int:
    use_model = "--model" in argv
    if use_model:
        load_dotenv()
    receipts: list = []
    results = run_all(use_model, receipts)
    for r in results:
        print(f"{'ok  ' if r['ok'] else 'MISS'} {r['case']:02d} {r['slug']:<32} expected {r['expected']:<13} got {r['got']}")
    score = sum(r["ok"] for r in results)
    if not use_model:
        label = "sample mode (rules alone)"
    elif all(r["degraded"] for r in results):  # no provider configured: every agent fell back to its rules
        label = "--model, but no model is configured (MODEL_API_KEY in .env), so rules alone"
    else:
        label = "with the model"
    print(f"\n{label}: {score}/{len(results)}")
    if "--save" in argv and not use_model:
        BASELINE.write_text(json.dumps({"score": score, "results": results}, indent=2) + "\n",
                            encoding="utf-8", newline="\n")
        print(f"wrote {BASELINE.name}")
    if "--save-receipts" in argv and receipts:
        RECEIPTS.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%MZ", time.gmtime())
        path = RECEIPTS / f"bench_{stamp}.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in receipts),
                        encoding="utf-8", newline="\n")
        totals = {"cases": len(results), "score": score,
                  "model_calls": sum(r["model_calls"] for r in results),
                  "rules_fallbacks": sum(r["rules_fallbacks"] for r in results),
                  "receipts": len(receipts), "receipts_valid": sum(r["signature_valid"] for r in receipts),
                  "routed_models": sorted({r["routed_model"] for r in receipts if r.get("routed_model")})}
        NIGHTSHIFT.write_text(json.dumps({"measured_on": time.strftime("%Y-%m-%d"), "receipts_file":
                                          f"data/nightshift_receipts/{path.name}", **totals, "results": results},
                                         indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"{totals['receipts_valid']}/{totals['receipts']} signed receipts valid; "
              f"{totals['rules_fallbacks']} rule fallback(s); wrote {NIGHTSHIFT.name} and {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
