"""Meterless Scout Intent on GreenTrace's own texts: false alarms on every report paragraph of the 21 trap
cases, and the frozen held-out set (data/scout_heldout.json), never used to tune the patterns.

    python -m scripts.scout_bench          # needs meterless/server.ts running (npm start in meterless/)
    python -m scripts.scout_bench --save   # also writes data/bench_scout.json
"""
import json
import sys
import time

from app.memory import scout
from app.world.cases import CASES, build_case
from app.world.store import DATA_DIR

HELD_OUT = DATA_DIR / "scout_heldout.json"
OUT = DATA_DIR / "bench_scout.json"


def main(argv: list[str]) -> int:
    paragraphs = {}
    for case in CASES:
        for p in build_case(case.number)["report_paragraphs"]:
            paragraphs.setdefault((p["doc_id"], p["section"], p["paragraph_no"], p["paragraph_text"]), case.number)
    keys = list(paragraphs)
    results = scout.guard([k[3] for k in keys])
    blocked = [{"case": paragraphs[k], "paragraph": f"{k[0]} {k[1]} {k[2]}", "reason": r["reason"]}
               for k, r in zip(keys, results) if r["level"] == "block"]
    held = json.loads(HELD_OUT.read_text(encoding="utf-8"))["texts"]
    verdicts = scout.guard([t["text"] for t in held])
    rows = [{**t, "blocked": v["level"] == "block", "reason": v.get("reason")} for t, v in zip(held, verdicts)]
    attacks = [r for r in rows if r["attack"]]
    clean = [r for r in rows if not r["attack"]]
    summary = {"measured_on": time.strftime("%Y-%m-%d"), "case_paragraphs": len(keys), "case_paragraphs_blocked": blocked,
               "held_out_attacks_blocked": sum(r["blocked"] for r in attacks), "held_out_attacks": len(attacks),
               "held_out_clean_blocked": sum(r["blocked"] for r in clean), "held_out_clean": len(clean),
               "held_out_misses": [r["text"] for r in rows if r["blocked"] != r["attack"]], "held_out": rows}
    print(f"{len(keys)} distinct report paragraphs across the 21 cases: {len(blocked)} blocked "
          + "; ".join(f"case {b['case']} {b['paragraph']} ({b['reason']})" for b in blocked))
    print(f"held-out: {summary['held_out_attacks_blocked']}/{len(attacks)} attacks blocked, "
          f"{summary['held_out_clean_blocked']}/{len(clean)} clean texts blocked; misses: {summary['held_out_misses']}")
    if "--save" in argv:
        OUT.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
