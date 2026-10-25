"""GreenTrace entry point (kit, section 1).

    python run.py                      # serves the API on 0.0.0.0:$PORT (default 8000)
    python run.py --input case.json    # runs one investigation, result JSON on stdout
    python run.py --input -            # same, reading the case from stdin
    python run.py --input case.json --receipts r.jsonl   # also keeps every signed Meterless receipt

In command-line mode stdout carries only the result JSON, so it can be piped into
jq or a file; trace lines go to stderr (still on screen) and to logs/<run_id>.jsonl.
"""
import argparse
import json
import os
import sys

from dotenv import load_dotenv

from app.pipeline import run_case


def investigate(source: str, receipts: str | None = None) -> int:
    try:
        # utf-8-sig: files saved by Windows tools often start with a BOM.
        if source == "-":
            case = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        else:
            with open(source, encoding="utf-8-sig") as handle:
                case = json.load(handle)
        result = run_case(case, stream=sys.stderr)
        if receipts:
            from app import orchestrator
            gate = orchestrator.RUNS[result["run_id"]].ctx.model
            with open(receipts, "w", encoding="utf-8", newline="\n") as handle:
                for receipt in (gate.receipts if gate is not None else []):
                    handle.write(json.dumps(receipt, ensure_ascii=False) + "\n")
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(json.dumps({"status": "error", "error": str(error)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def serve() -> int:
    import uvicorn

    uvicorn.run("app.api:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
    return 0


def main(argv: list[str]) -> int:
    load_dotenv()  # keys and SAMPLE_MODE from .env; never committed
    parser = argparse.ArgumentParser(description="GreenTrace: checks a deforestation-free claim and explains why.")
    parser.add_argument("--input", metavar="FILE", help="case JSON file, or - for stdin; omit to serve the API")
    parser.add_argument("--receipts", metavar="FILE", help="with --input: write the run's signed Meterless receipts (JSONL)")
    args = parser.parse_args(argv)
    return investigate(args.input, args.receipts) if args.input else serve()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
