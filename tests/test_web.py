"""The interface (app/web): the page, the live trace as Server-Sent Events, and "try to fool it" through the API."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from app import orchestrator
from app.api import app
from app.tracing import FIELDS, LOG_DIR
from app.web import STATIC, live

CLAIM = "100% of our cocoa comes from deforestation-free farms since 2021."


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("WORLD_MODEL_URL", "http://127.0.0.1:9")  # nothing listens: in-memory fallback, fast
    return TestClient(app)


def events(client, run_id, last_event_id=None):
    """Read the whole stream (it ends after the final event) and parse each SSE event into its fields."""
    headers = {"Last-Event-ID": str(last_event_id)} if last_event_id is not None else {}
    with client.stream("GET", f"/runs/{run_id}/events", headers=headers) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        text = "".join(response.iter_text())
    parsed = []
    for block in text.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if line and not line.startswith(":"))
        if fields:
            parsed.append(fields)
    return parsed


def start(client, **options):
    response = client.post("/runs", json={"input": {"claim": CLAIM}, "options": {"sample_mode": True, **options}})
    assert response.status_code == 202
    return response.json()["run_id"]


def test_the_page_is_served_with_its_own_fonts_and_no_cdn(client):
    page = client.get("/")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    for needed in ("Run the demo case", CLAIM, 'aria-live="polite"', "Try to fool it", "Human review required"):
        assert needed in page.text
    for path in ("app.css", "app.js", "mark.svg", "fonts/Geist-Variable.woff2", "fonts/GeistMono-Variable.woff2",
                 "fonts/OFL.txt"):
        assert client.get(f"/static/{path}").status_code == 200, path
    assert "SIL Open Font License" in (STATIC / "fonts" / "OFL.txt").read_text(encoding="utf-8")
    # The jury runs SAMPLE_MODE without network: the page must not fetch anything from elsewhere.
    for name in ("index.html", "app.css", "app.js"):
        assert not re.search(r"(https?:)?//[a-z0-9.-]+\.[a-z]{2,}/", (STATIC / name).read_text(encoding="utf-8")), name


def test_setup_is_read_from_the_code(client):
    setup = client.get("/ui/setup").json()
    assert setup["default_mode"] == "sample" and setup["default_claim"] == CLAIM
    assert [c["case"] for c in setup["cases"]] == list(range(1, 22))
    assert setup["demo_case"] == orchestrator.DEFAULT_SAMPLE_CASE
    assert setup["what_if"] == {
        "alert": {"alert_date": {"type": "date"},
                  "confidence": {"type": "choice", "choices": sorted(orchestrator.EDITABLE["alert"]["confidence"])}},
        "plot": {"area_ha": {"type": "number"}},
    }


def test_a_live_run_streams_every_trace_line_then_the_evidence_file(client):
    run_id = start(client, case=2)
    stream = events(client, run_id)
    *trace, final = stream

    assert [e["event"] for e in trace] == ["trace"] * len(trace)
    assert [e["id"] for e in trace] == [str(n) for n in range(len(trace))]
    lines = [json.loads(e["data"]) for e in trace]
    assert all(tuple(sorted(line)) == tuple(sorted(FIELDS)) and line["run_id"] == run_id for line in lines)
    # The same lines, in the same order, as the run's log file: nothing dropped, nothing invented.
    logged = (LOG_DIR / f"{run_id}.jsonl").read_text(encoding="utf-8").splitlines()
    assert [e["data"] for e in trace] == logged

    actions = [line["action"] for line in lines]
    assert actions.index("request_revision") < actions.index("revise_plan") < actions.index("approve")
    assert "refute_hypothesis" in actions and "query_zetaris" in actions

    assert final["event"] == "result"
    output = json.loads(final["data"])
    assert output["run_id"] == run_id and output["verdict"] == "CONTRADICTED" and output["mode"] == "sample"
    assert client.get(f"/runs/{run_id}").json()["verdict_sha256"] == output["verdict_sha256"]


def test_a_page_that_reconnects_gets_only_the_lines_it_missed(client):
    run_id = start(client)
    first = events(client, run_id)
    again = events(client, run_id, last_event_id=9)
    assert again[0]["id"] == "10" and [e["data"] for e in again] == [e["data"] for e in first[10:]]


def test_try_to_fool_it_from_the_api_after_a_live_run(client):
    run_id = start(client, case=2)
    assert json.loads(events(client, run_id)[-1]["data"])["verdict"] == "CONTRADICTED"
    changed = client.post(f"/runs/{run_id}/what_if",
                          json={"entity": "alert:A-221", "field": "alert_date", "value": "2019-03-14"})
    assert changed.status_code == 200
    out = changed.json()
    assert out["verdict"] == "PROVEN"
    assert out["what_if"]["verdict_before"] == "CONTRADICTED" and out["what_if"]["verdict_after"] == "PROVEN"
    assert out["what_if"]["invalidated_facts"]


def test_a_failed_run_is_told_to_the_page(client):
    response = client.post("/runs", json={"input": {"claim": ""}})
    assert response.status_code == 202
    (only,) = events(client, response.json()["run_id"])
    assert only["event"] == "failed" and "claim" in json.loads(only["data"])["error"]


def test_bad_requests_are_refused_before_any_thread(client):
    assert client.post("/runs", json=[CLAIM]).status_code == 422
    assert client.post("/runs", json={"run_id": "../x", "input": {"claim": CLAIM}}).status_code == 422
    run_id = start(client)
    events(client, run_id)
    assert client.post("/runs", json={"run_id": run_id, "input": {"claim": CLAIM}}).status_code == 409
    assert client.get("/runs/never-started/events").status_code == 404


def test_the_stream_publishes_whole_lines_only():
    run = live.LiveRun("unit")
    run.write('{"a": 1}\n{"b"')
    assert run.lines == ['{"a": 1}']
    run.write(': 2}\n')
    run.finish(output={"verdict": "PROVEN"})
    assert run.lines == ['{"a": 1}', '{"b": 2}']
    assert [block.split("\n")[0] for block in run.events()] == ["id: 0", "id: 1", "event: result"]
