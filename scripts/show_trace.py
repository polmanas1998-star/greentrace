"""Print a run's trace as one readable line per step.

    python -m scripts.show_trace logs/demo-002.jsonl
"""
import json
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip())
        return 2
    with open(argv[0], encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            step = json.loads(line)
            arrow = f"-> {step['target_agent']}" if step["target_agent"] else ""
            provider = step["provider"] or ""
            print(f"{number:>3} {step['agent_name']:<12} {step['action']:<20} {arrow:<16} "
                  f"{provider:<9} {step['output_summary'][:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
