"""output_examples/ holds real runs: rerunning each input gives the same verdict and the same seal."""
import io
import json
from pathlib import Path

import pytest

import verify
from app.memory.fallback import MemoryEngine
from app.pipeline import run_case

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES, OUTPUTS = ROOT / "examples", ROOT / "output_examples"
INPUTS = sorted(EXAMPLES.glob("*.json"))


def test_every_input_has_its_output_and_there_are_at_least_three_verdicts():
    assert [p.name for p in INPUTS] == sorted(p.name for p in OUTPUTS.glob("*.json") if "_receipt" not in p.name)
    verdicts = {json.loads((OUTPUTS / p.name).read_text(encoding="utf-8")).get("verdict") for p in INPUTS}
    assert {"PROVEN", "CONTRADICTED", "UNVERIFIABLE"} <= verdicts


@pytest.mark.parametrize("example", INPUTS, ids=lambda p: p.stem)
def test_rerunning_the_input_gives_the_same_verdict_and_seal(example):
    request = json.loads(example.read_text(encoding="utf-8-sig"))
    request["options"] = {**(request.get("options") or {}), "sample_mode": True}
    request["run_id"] = f"rerun-{example.stem[:20]}"
    shipped = json.loads((OUTPUTS / example.name).read_text(encoding="utf-8"))
    again = run_case(request, stream=io.StringIO(), memory_engine=MemoryEngine())
    assert shipped["mode"] == "sample"
    assert (again["verdict"], again["verdict_sha256"]) == (shipped["verdict"], shipped["verdict_sha256"])
    assert again["fingerprints"]["data_sha256"] == shipped["fingerprints"]["data_sha256"]


def test_the_shipped_receipt_is_valid_and_carries_the_shipped_seal():
    receipt = json.loads((OUTPUTS / "01_contradicted_receipt.json").read_text(encoding="utf-8"))
    result = verify.check(receipt)
    shipped = json.loads((OUTPUTS / "01_contradicted.json").read_text(encoding="utf-8"))
    assert (result["status"], result["problems"]) == ("VALID", [])
    assert result["seal"] == receipt["verdict_sha256"] == shipped["verdict_sha256"]
