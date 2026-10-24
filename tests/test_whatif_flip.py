"""Every change "What would change it" offers can be applied by what_if, and lands on the verdict it announced."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.world.cases import CASES_BY_NUMBER

ROOT = Path(__file__).resolve().parent.parent
CLAIM = "100% of our cocoa comes from deforestation-free farms since 2021."


@pytest.fixture(scope="module")
def client():
    import os
    os.environ["SAMPLE_MODE"] = "true"
    return TestClient(app)


def _tried(client, run_id: str, body: dict) -> list[dict]:
    out = client.post("/run", json={**body, "run_id": run_id}).json()
    return out.get("flip_tried") or []


@pytest.mark.parametrize("number", sorted(CASES_BY_NUMBER))
def test_each_offered_change_applies_to_its_announced_verdict(client, number):
    body = {"input": {"claim": CLAIM, "company_id": "chocodurable"}, "options": {"case": number}}
    for i, tried in enumerate(_tried(client, f"flip-{number}", body)):
        run_id = f"flip-{number}-{i}"
        client.post("/run", json={**body, "run_id": run_id})
        change = tried["change"]
        answer = client.post(f"/runs/{run_id}/what_if",
                             json={"entity": change["entity"], "field": change["field"], "value": change["to"]})
        assert answer.status_code == 200, answer.text
        assert answer.json()["verdict"] == tried["verdict"]


def test_the_real_plot_flip_applies_and_its_receipt_still_verifies(client, tmp_path):
    import subprocess
    import sys
    body = json.loads((ROOT / "examples" / "05_real_plot_contradicted.json").read_text(encoding="utf-8"))
    body["run_id"] = "flip-real-plot"
    client.post("/run", json=body)
    out = client.post("/runs/flip-real-plot/what_if",
                      json={"entity": "plot:P-042", "field": "tonnes_after_2020", "value": 0}).json()
    assert out["verdict"] == "PROVEN"
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(client.get("/runs/flip-real-plot/receipt").json()), encoding="utf-8")
    verified = subprocess.run([sys.executable, str(ROOT / "verify.py"), str(receipt)], capture_output=True, text=True)
    assert verified.returncode == 0 and verified.stdout.startswith("VALID: PROVEN")


def test_a_flip_change_takes_only_its_own_values(client):
    body = {"input": {"claim": CLAIM, "company_id": "chocodurable"}, "options": {"case": 2}, "run_id": "flip-bad"}
    client.post("/run", json=body)
    assert client.post("/runs/flip-bad/what_if", json={"entity": "plot:P-014", "field": "tonnes_after_2020",
                                                       "value": 3}).status_code == 422
    assert client.post("/runs/flip-bad/what_if", json={"entity": "plot:P-999", "field": "tonnes_after_2020",
                                                       "value": 0}).status_code == 422
    assert client.post("/runs/flip-bad/what_if", json={"entity": "plot:P-014", "field": "position",
                                                       "value": "north"}).status_code == 422
