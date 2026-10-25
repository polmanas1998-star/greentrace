"""Nightshift's signed compute receipts: checked at call time, kept, and never trusted when broken."""
import base64
import copy
import json
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.adapters import meterless_receipt
from app.adapters.llm import ModelGate, configure

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
TOKEN = "nightshift-test-token-0001"


def signed(receipt_id="rcpt_req_test", routed="nvidia/nemotron-3.5-lightning-30b-a3b"):
    """A Meterless object shaped like Nightshift 1.2.3's, signed with a key made for this test."""
    key = Ed25519PrivateKey.generate()
    body = {"signer": "meterlessd", "ts_ms": 1792928514677, "manifest_hash": "",
            "receipt": {"receipt_id": receipt_id, "routed_model": routed, "note": "é, UTF-8 kept"}}
    envelope = {"body": body, "sig_b64": base64.b64encode(key.sign(meterless_receipt.signed_bytes(body))).decode(),
                "pubkey_b64": base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()}
    return {"receipt_id": receipt_id, "routed_model": routed, "usage": {"latency_ms": 812},
            "winning_route": {"provider": "nvidia", "kind": "cloud"}, "receipt_envelope": envelope}


def test_a_signed_receipt_verifies_and_one_changed_byte_breaks_it():
    meterless = signed()
    receipt = meterless_receipt.read(meterless)
    assert receipt["signature_valid"] and receipt["signer"] == "meterlessd" and receipt["route"] == "nvidia/cloud"
    assert "signature valid" in meterless_receipt.summary(receipt)
    forged = copy.deepcopy(meterless)
    forged["receipt_envelope"]["body"]["receipt"]["routed_model"] = "nvidia/nemotron-3-ultra-550b-a55b"
    assert not meterless_receipt.read(forged)["signature_valid"]
    assert "SIGNATURE INVALID" in meterless_receipt.summary(meterless_receipt.read(forged))


def test_no_receipt_or_no_envelope_is_said_not_hidden():
    assert meterless_receipt.read(None) is None
    assert meterless_receipt.summary(None) == "no Meterless receipt in the answer"
    bare = meterless_receipt.read({"receipt_id": "rcpt_x"})
    assert not bare["signature_valid"] and "without a signed envelope" in meterless_receipt.summary(bare)


class Answering:
    def __init__(self, meterless):
        self.meterless = meterless
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.models = SimpleNamespace(list=lambda **o: [SimpleNamespace(id="meterless/fast")])

    def _create(self, **request):
        message = SimpleNamespace(content=json.dumps({"ok": True}))
        return SimpleNamespace(model=request["model"], choices=[SimpleNamespace(message=message)], usage=None,
                               model_extra={"meterless": self.meterless})


def test_the_gate_checks_and_keeps_every_receipt(tmp_path):
    lines = []
    token_file = tmp_path / "proxy-token"
    token_file.write_text(TOKEN + "\n", encoding="utf-8")
    environment = {"MODEL_PROVIDER": "meterless", "METERLESS_BASE_URL": "127.0.0.1:8088",
                   "METERLESS_RUNTIME_TOKEN_FILE": str(token_file),
                   "METERLESS_MODEL_FAST": "nvidia/nemotron-3.5-lightning-30b-a3b",
                   "METERLESS_MODEL_DEEP": "nvidia/nemotron-3-ultra-550b-a55b"}
    model = ModelGate("receipt-test", env=environment, client_factory=lambda provider: Answering(signed()),
                      log=lines.append)
    answer, info = model.complete_json("Writer", "system", [("a", "b")], SCHEMA)
    assert answer == {"ok": True} and info["meterless_receipt"]["signature_valid"]
    assert model.calls[0]["meterless_receipt"]["receipt_id"] == "rcpt_req_test"
    assert model.receipts[0]["envelope"]["body"]["signer"] == "meterlessd"
    call = next(line for line in lines if line["action"] == "model_call")
    assert "signed by meterlessd: signature valid" in call["output_summary"]
    assert TOKEN not in json.dumps(lines)


def test_the_token_comes_from_nightshifts_file_and_a_missing_file_is_said(tmp_path):
    token_file = tmp_path / "proxy-token"
    token_file.write_text(TOKEN, encoding="utf-8")
    provider, why = configure("meterless", {"METERLESS_BASE_URL": "127.0.0.1:8088",
                                            "METERLESS_RUNTIME_TOKEN_FILE": str(token_file)})
    assert why is None and provider.secret == TOKEN
    provider, why = configure("meterless", {"METERLESS_BASE_URL": "127.0.0.1:8088",
                                            "METERLESS_RUNTIME_TOKEN_FILE": str(tmp_path / "none")})
    assert provider is None and "cannot be read" in why


def test_a_lane_is_a_pinned_model_or_mode_never_auto():
    base = {"METERLESS_BASE_URL": "127.0.0.1:8088", "METERLESS_RUNTIME_TOKEN": TOKEN}
    provider, _ = configure("meterless", {**base, "METERLESS_MODEL_DEEP": "nvidia/nemotron-3-ultra-550b-a55b"})
    assert provider.models["deep"] == "nvidia/nemotron-3-ultra-550b-a55b"
    with pytest.raises(ValueError, match="never meterless/auto"):
        configure("meterless", {**base, "METERLESS_MODEL_FAST": "meterless/auto"})
