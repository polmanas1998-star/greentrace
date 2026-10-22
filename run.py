"""Command line: python run.py <case.json>   (or pipe the case on stdin)

stdout carries only the result JSON, so it can be piped into jq or a file.
Trace lines go to stderr here (still on screen) and to logs/<run_id>.jsonl.
"""
import json
import sys

from app.pipeline import run_case


def main(argv: list[str]) -> int:
    try:
        # utf-8-sig: files saved by Windows tools often start with a BOM.
        if len(argv) > 1:
            with open(argv[1], encoding="utf-8-sig") as handle:
                case = json.load(handle)
        else:
            case = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        result = run_case(case, stream=sys.stderr)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(json.dumps({"status": "error", "error": str(error)}), file=sys.stdout)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
