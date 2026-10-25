"""Re-check every Meterless Nightshift receipt kept from a run, offline.

    python -m scripts.verify_nightshift_receipts data/nightshift_receipts/<file>.jsonl

Each line is one model answer's receipt as GreenTrace kept it at call time: the receipt id, the
model Nightshift routed to, and the envelope meterlessd signed (body, Ed25519 public key,
signature). This script verifies each signature again over the body (compact JSON, keys sorted,
UTF-8), and checks that the receipt id and routed model GreenTrace recorded are the ones inside
the signed body. Exit 0 when every receipt holds, 1 otherwise.
"""
import json
import sys
from collections import Counter
from pathlib import Path

from app.adapters.meterless_receipt import signature_valid


def check(line: dict) -> list[str]:
    envelope = line.get("envelope")
    if not isinstance(envelope, dict):
        return ["no signed envelope"]
    problems = [] if signature_valid(envelope) else ["signature does not verify"]
    signed = envelope.get("body", {}).get("receipt", {})
    if line.get("receipt_id") != signed.get("receipt_id"):
        problems.append(f"receipt_id: recorded {line.get('receipt_id')!r}, signed {signed.get('receipt_id')!r}")
    winner = (signed.get("winning_route") or {}).get("model")  # the signed body names the model as the winning route
    if line.get("routed_model") != winner:
        problems.append(f"model: recorded {line.get('routed_model')!r}, signed {winner!r}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    lines = [json.loads(text) for text in Path(argv[0]).read_text(encoding="utf-8").splitlines() if text.strip()]
    failed = 0
    for line in lines:
        problems = check(line)
        if problems:
            failed += 1
            print(f"FAIL case {line.get('case')} {line.get('receipt_id')}: {'; '.join(problems)}")
    keys = Counter(line["envelope"]["pubkey_b64"] for line in lines if isinstance(line.get("envelope"), dict))
    models = Counter(line.get("routed_model") for line in lines)
    print(f"{len(lines) - failed}/{len(lines)} receipts verify (Ed25519, signer meterlessd)")
    print("signing key(s): " + ", ".join(f"{key} x{n}" for key, n in keys.items()))
    print("routed to: " + ", ".join(f"{model} x{n}" for model, n in models.most_common()))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
