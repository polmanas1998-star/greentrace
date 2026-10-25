"""The signed compute receipt Meterless Nightshift returns with every chat answer, read and checked.

Nightshift 1.2.3 adds a "meterless" object to each chat completion. Its "receipt_envelope" holds
the receipt ("body"), the signer ("body.signer", meterlessd), an Ed25519 public key ("pubkey_b64")
and a signature ("sig_b64") over the body as compact JSON, keys sorted, UTF-8. We check that
signature here, at call time, and keep the envelope so it can be checked again later
(scripts/verify_nightshift_receipts.py). A missing or broken envelope is said, never hidden.
"""
import base64
import json
from typing import Any, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def signed_bytes(body: dict) -> bytes:
    """The bytes meterlessd signs: the receipt body as compact JSON, keys sorted, UTF-8."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def signature_valid(envelope: Any) -> bool:
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(envelope["pubkey_b64"]))
        key.verify(base64.b64decode(envelope["sig_b64"]), signed_bytes(envelope["body"]))
        return True
    except (InvalidSignature, KeyError, TypeError, ValueError):
        return False


def read(meterless: Any) -> Optional[dict[str, Any]]:
    """The receipt of one answer, or None when the answer carried no Meterless object at all."""
    if not isinstance(meterless, dict):
        return None
    envelope = meterless.get("receipt_envelope")
    route = meterless.get("winning_route") or {}
    return {
        "receipt_id": meterless.get("receipt_id"),
        "routed_model": meterless.get("routed_model"),
        "route": f"{route.get('provider')}/{route.get('kind')}" if route else None,
        "latency_ms": (meterless.get("usage") or {}).get("latency_ms"),
        "signer": (envelope or {}).get("body", {}).get("signer") if isinstance(envelope, dict) else None,
        "signature_valid": signature_valid(envelope) if isinstance(envelope, dict) else False,
        "envelope": envelope,
    }


def summary(receipt: Optional[dict[str, Any]]) -> str:
    """One trace phrase: which receipt, who signed it, whether the signature holds."""
    if receipt is None:
        return "no Meterless receipt in the answer"
    if not receipt["envelope"]:
        return f"receipt {receipt['receipt_id']} came without a signed envelope"
    state = "signature valid" if receipt["signature_valid"] else "SIGNATURE INVALID"
    return f"receipt {receipt['receipt_id']} signed by {receipt['signer']}: {state}, routed to {receipt['routed_model']}"
