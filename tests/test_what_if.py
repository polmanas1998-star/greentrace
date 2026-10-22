"""'Try to fool it': change one value, and only what depends on it is re-run."""
import io

import pytest
from fastapi.testclient import TestClient

from app import orchestrator
from app.api import app
from app.memory.fallback import MemoryEngine
from app.pipeline import run_case

CLAIM = "100% of our cocoa comes from deforestation-free farms since 2021."


def demo(run_id="wi-demo", case=2):
    return run_case({"run_id": run_id, "input": {"claim": CLAIM}, "options": {"sample_mode": True, "case": case}},
                    stream=io.StringIO(), memory_engine=MemoryEngine())


def test_demo_flip_is_announced_in_the_evidence_file():
    out = demo()
    assert out["flip"] == "The claim would be PROVEN if alert A-221 were dated 2020-12-31."


def test_moving_the_alert_before_the_cutoff_flips_to_proven():
    demo()
    out = orchestrator.what_if("wi-demo", "alert:A-221", "alert_date", "2019-03-14")
    assert out["what_if"]["verdict_before"] == "CONTRADICTED"
    assert out["verdict"] == "PROVEN"
    assert out["what_if"]["invalidated_facts"]  # the card built on A-221 went stale


def test_a_change_that_keeps_the_alert_after_the_cutoff_keeps_the_verdict():
    demo("wi-keep")
    out = orchestrator.what_if("wi-keep", "alert:A-221", "alert_date", "2023-07-01")
    assert out["verdict"] == "CONTRADICTED"
    assert out["what_if"]["rechecked_cards"]  # the changed card went back to the Evaluator


def test_low_confidence_turns_it_unverifiable():
    demo("wi-low")
    assert orchestrator.what_if("wi-low", "alert:A-221", "confidence", "low")["verdict"] == "UNVERIFIABLE"


def test_what_if_chains_on_the_last_state():
    demo("wi-chain")
    orchestrator.what_if("wi-chain", "alert:A-221", "alert_date", "2019-03-14")
    out = orchestrator.what_if("wi-chain", "alert:A-221", "alert_date", "2022-03-14")
    assert out["verdict"] == "CONTRADICTED"


@pytest.mark.parametrize("entity, field, value", [
    ("alert:A-221", "alert_date", "14/03/2019"), ("alert:A-221", "confidence", "certain"),
    ("alert:A-999", "alert_date", "2019-03-14"), ("plot:P-014", "supplier_id", "F-002"),
    ("plot:P-014", "area_ha", "big"), ("company:chocodurable", "total_tonnes", 1),
])
def test_bad_changes_are_refused(entity, field, value):
    demo("wi-bad")
    with pytest.raises(ValueError):
        orchestrator.what_if("wi-bad", entity, field, value)


def test_unknown_run_is_a_key_error():
    with pytest.raises(KeyError):
        orchestrator.what_if("never-ran", "alert:A-221", "alert_date", "2019-03-14")


def test_api_round_trip(monkeypatch):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("WORLD_MODEL_URL", "http://127.0.0.1:9")  # nothing listens: in-memory fallback, fast
    client = TestClient(app)
    out = client.post("/run", json={"run_id": "wi-api", "input": {"claim": CLAIM}, "options": {"case": 2}}).json()
    assert out["verdict"] == "CONTRADICTED"
    assert client.get("/runs/wi-api").json()["verdict"] == "CONTRADICTED"
    changed = client.post("/runs/wi-api/what_if",
                          json={"entity": "alert:A-221", "field": "alert_date", "value": "2019-03-14"})
    assert changed.status_code == 200 and changed.json()["verdict"] == "PROVEN"
    assert client.post("/runs/wi-api/what_if", json={"entity": "alert:A-221", "field": "alert_date",
                                                      "value": "soon"}).status_code == 422
    assert client.get("/runs/nope").status_code == 404
