"""Plant false evidence in a verdict receipt, one change at a time, and count what verify.py catches.

    python -m scripts.tamper_bench                       # the demo receipt
    python -m scripts.tamper_bench --save                # also writes data/bench_tamper.json

Two forgers, on every field of every row the receipt carries:
- naive: changes one value and leaves the hashes as they were;
- careful: changes one value AND rewrites the row hash, the answer hash and data_sha256, so the
  integrity checks pass; only re-deriving the verdict from the rows can catch it.
A careful change that leaves the rules' verdict and flip as they were is not a lie about the
verdict, and the receipt says VALID: a receipt proves the verdict follows from the rows, not that
the rows are true. The number that matters is: careful changes that WOULD change the verdict or the
flip, and how many of them verify.py catches.
"""
import copy
import json
import sys
from pathlib import Path
from typing import Any

import verify
from app.canonical import sha256_of

ROOT = Path(__file__).resolve().parent.parent
RECEIPT = ROOT / "output_examples" / "01_contradicted_receipt.json"
OUT = ROOT / "data" / "bench_tamper.json"


def mutated(value: Any) -> Any:
    """A plausible false value of the same type (a forger keeps the shape)."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return round(value * 1.01 + 0.001, 6)
    if isinstance(value, str):
        if len(value) == 10 and value[4] == "-" and value[7] == "-":  # a date: one year earlier
            return f"{int(value[:4]) - 1}{value[4:]}"
        if value in ("high", "highest"):
            return "low"
        return value + " (amended)"
    return None


def rehash(receipt: dict) -> dict:
    for source in receipt["sources"]:
        source["row_sha256"] = [sha256_of(row) for row in source["rows"]]
        source["rows_sha256"] = sha256_of(source["rows"])
    receipt["data_sha256"] = sha256_of([{"query_id": s["query_id"], "rows_sha256": s["rows_sha256"]}
                                        for s in receipt["sources"]])
    return receipt


def main(argv: list[str]) -> int:
    base = json.loads(RECEIPT.read_text(encoding="utf-8"))
    assert verify.check(base)["status"] == "VALID", "the reference receipt must verify first"
    naive = {"planted": 0, "caught": 0}
    careful = {"planted": 0, "verdict_or_flip_changed": 0, "caught_of_those": 0, "harmless_valid": 0,
               "harmless_caught": 0}
    for s_index, source in enumerate(base["sources"]):
        for r_index, row in enumerate(source["rows"]):
            for field, value in row.items():
                false = mutated(value)
                if false is None or false == value:
                    continue
                forged = copy.deepcopy(base)
                forged["sources"][s_index]["rows"][r_index][field] = false
                naive["planted"] += 1
                naive["caught"] += verify.check(forged)["status"] == "TAMPERED"

                careful_forged = rehash(forged)
                result = verify.check(careful_forged)
                careful["planted"] += 1
                # would the verdict or the flip change? that is what the forger was after
                changed = result["seal"] != base["verdict_sha256"] or any(p.startswith("flip") for p in result["problems"])
                if changed:
                    careful["verdict_or_flip_changed"] += 1
                    careful["caught_of_those"] += result["status"] == "TAMPERED"
                elif result["status"] == "VALID":
                    careful["harmless_valid"] += 1
                else:
                    careful["harmless_caught"] += 1
    print(f"naive forger: {naive['caught']}/{naive['planted']} planted changes caught")
    print(f"careful forger (hashes rewritten): {careful['planted']} planted; "
          f"{careful['verdict_or_flip_changed']} would change the verdict or the flip, "
          f"{careful['caught_of_those']} of them caught; {careful['harmless_valid']} leave both unchanged (VALID), "
          f"{careful['harmless_caught']} caught anyway (quotes, documents)")
    if "--save" in argv:
        OUT.write_text(json.dumps({"receipt": RECEIPT.name, "naive": naive, "careful": careful}, indent=2) + "\n",
                       encoding="utf-8", newline="\n")
        print(f"wrote {OUT.relative_to(ROOT)}")
    ok = naive["caught"] == naive["planted"] and careful["caught_of_those"] == careful["verdict_or_flip_changed"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
