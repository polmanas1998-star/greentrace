"""Fill in the signed envelopes an answer did not carry inline, from Nightshift's own receipt log.

    python -m scripts.recover_nightshift_receipts data/nightshift_receipts/<file>.jsonl [<nightshift receipts.jsonl>]

Nightshift returns a receipt id with every answer, and usually the envelope meterlessd signed. When
the envelope is missing from the answer, Nightshift still keeps it in its receipt log
(%LOCALAPPDATA%/ai.meterless.runtime/receipts.jsonl on Windows). This script finds each missing one
there by receipt id, keeps it only if its signature verifies and its body names the same receipt id
and model, and marks it `envelope_source: "nightshift receipts.jsonl, recovered after the run"`.
Nothing else in the file changes. Exit 0 when every receipt then holds, 1 otherwise.
"""
import json
import os
import sys
from pathlib import Path

from app.adapters.meterless_receipt import signature_valid

DEFAULT_LOG = Path(os.path.expandvars(r"%LOCALAPPDATA%")) / "ai.meterless.runtime" / "receipts.jsonl"


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    kept = Path(argv[0])
    log = Path(argv[1]) if len(argv) > 1 else DEFAULT_LOG
    lines = [json.loads(t) for t in kept.read_text(encoding="utf-8").splitlines() if t.strip()]
    missing = {line["receipt_id"] for line in lines if not isinstance(line.get("envelope"), dict)}
    found = {}
    for text in log.read_text(encoding="utf-8").splitlines():
        if not text.strip():
            continue
        envelope = json.loads(text)
        receipt = (envelope.get("body") or {}).get("receipt") or {}
        if receipt.get("receipt_id") in missing:
            found[receipt["receipt_id"]] = envelope
    recovered = 0
    for line in lines:
        envelope = found.get(line["receipt_id"])
        if envelope is None or isinstance(line.get("envelope"), dict):
            continue
        receipt = envelope["body"]["receipt"]
        if signature_valid(envelope) and (receipt.get("winning_route") or {}).get("model") == line["routed_model"]:
            line.update({"envelope": envelope, "signer": envelope["body"].get("signer"), "signature_valid": True,
                         "envelope_source": "nightshift receipts.jsonl, recovered after the run"})
            recovered += 1
    kept.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines), encoding="utf-8",
                    newline="\n")
    valid = sum(bool(line.get("signature_valid")) for line in lines)
    print(f"{len(missing)} envelope(s) missing, {recovered} recovered and verified; {valid}/{len(lines)} receipts hold")
    return 0 if valid == len(lines) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
