"""GreenTrace entry point (kit, section 1).

    python run.py                      # serves the API on 0.0.0.0:$PORT (default 8000)
    python run.py --input case.json    # runs one investigation, result JSON on stdout
    python run.py --input -            # same, reading the case from stdin

In command-line mode stdout carries only the result JSON, so it can be piped into
jq or a file; trace lines go to stderr (still on screen) and to logs/<run_id>.jsonl.
"""
import argparse
import json
import os
import sys

from app.pipeline import run_case


def investigate(source: str) -> int:
    try:
        # utf-8-sig: files saved by Windows tools often start with a BOM.
        if source == "-":
            case = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        else:
            with open(source, encoding="utf-8-sig") as handle:
                case = json.load(handle)
        result = run_case(case, stream=sys.stderr)
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
    parser = argparse.ArgumentParser(description="GreenTrace: checks a deforestation-free claim and explains why.")
    parser.add_argument("--input", metavar="FILE", help="case JSON file, or - for stdin; omit to serve the API")
    args = parser.parse_args(argv)
    return investigate(args.input) if args.input else serve()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
