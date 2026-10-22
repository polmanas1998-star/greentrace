"""Run one trap case and print its verdict and trace, with or without the model.

    python -m scripts.try_case 12            # sample world, rule fallbacks only
    python -m scripts.try_case 12 --model    # sample world, Nemotron reads the reports
"""
import io
import sys

from dotenv import load_dotenv

from app.pipeline import run_case
from app.world.cases import CASES_BY_NUMBER, DEFAULT_CLAIM
from scripts.show_trace import main as show_trace


def main(argv: list[str]) -> int:
    load_dotenv()
    number, use_model = int(argv[0]), "--model" in argv
    run_id = f"try-{number:02d}-{'model' if use_model else 'rules'}"
    out = run_case({"run_id": run_id, "input": {"claim": DEFAULT_CLAIM},
                    "options": {"sample_mode": True, "case": number, "use_model": use_model}},
                   stream=io.StringIO())
    show_trace([f"logs/{run_id}.jsonl"])
    case = CASES_BY_NUMBER[number]
    print(f"\ncase {number:02d} {case.slug}: expected {case.expected}, got {out['verdict']} "
          f"in {out['execution_time_seconds']} s")
    return 0 if out["verdict"] == case.expected else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
