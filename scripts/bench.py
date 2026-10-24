"""Run the 21 trap cases end to end and score the verdicts against the expected ones.

    python -m scripts.bench            # sample mode (SQLite), prints the table and the score
    python -m scripts.bench --save     # also writes data/bench_sample.json (the committed baseline)

Every run goes through the real agents. Without a model the agents use their rule
fallbacks, so this score is the "rules alone" baseline the README reports.
"""
import io
import json
import sys

from dotenv import load_dotenv

from app.memory.fallback import MemoryEngine
from app.pipeline import run_case
from app.world.cases import CASES, DEFAULT_CLAIM
from app.world.store import DATA_DIR

BASELINE = DATA_DIR / "bench_sample.json"


def run_all(use_model: bool = False) -> list[dict]:
    results = []
    for case in CASES:
        out = run_case({"run_id": f"bench-{case.number:02d}", "input": {"claim": DEFAULT_CLAIM},
                        "options": {"sample_mode": True, "case": case.number, "use_model": use_model}},
                       stream=io.StringIO(), memory_engine=MemoryEngine())
        results.append({"case": case.number, "slug": case.slug, "expected": case.expected,
                        "got": out.get("verdict"), "ok": out.get("verdict") == case.expected,
                        "verdict_sha256": out.get("verdict_sha256"), "degraded": out.get("degraded")})
    return results


def main(argv: list[str]) -> int:
    use_model = "--model" in argv
    if use_model:
        load_dotenv()
    results = run_all(use_model)
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
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
