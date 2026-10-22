from pathlib import Path
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
    for needed in ("Run the demo case", CLAIM, 'aria-live="polite"', "Try to fool it", "What would change its mind"):
        assert needed in page.text
    for path in ("app.css", "app.js", "mark.svg", "fonts/Geist-Variable.woff2", "fonts/GeistMono-Variable.woff2",
                 "fonts/OFL.txt"):
        assert client.get(f"/static/{path}").status_code == 200, path
    assert "SIL Open Font License" in (STATIC / "fonts" / "OFL.txt").read_text(encoding="utf-8")
    # The jury runs SAMPLE_MODE without network: the page must not fetch anything from elsewhere.
    for name in ("index.html", "app.css", "app.js"):
        text = (STATIC / name).read_text(encoding="utf-8").replace("http://www.w3.org/2000/svg", "")  # a name, not a fetch
        assert not re.search(r"(https?:)?//[a-z0-9.-]+\.[a-z]{2,}/", text), name


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


# ---- the home map, the reliability tests, the evidence files list ---------------------------------

def test_the_page_has_the_mockup_tabs_and_no_silent_button(client):
    page = client.get("/").text
    for tab in ("Check a claim", "Evidence files", "Reliability tests", "Download audit package",
                "Or paste your supplier's claim", "Check this claim"):
        assert tab in page
    # Upload and drawing are wired to input.plot now: live buttons, a real file input, nothing "coming".
    buttons = re.findall(r"<button[^>]*id=\"(?:plot-upload|plot-draw|draw-undo|draw-clear|draw-check)\"[^>]*>.*?</button>", page)
    assert len(buttons) == 5 and not any(" disabled" in b or "coming" in b for b in buttons)
    assert 'id="plot-file"' in page and client.get("/ui/setup").json()["plot_input"] is True
    examples = client.get("/ui/setup").json()["examples"]
    assert [(e["label"], e["case"]) for e in examples] == [("A clean supply chain", 1),
                                                           ("An admission hidden in an annex", 18)]


def test_the_map_is_measured_from_the_committed_cells(client):
    from app.checks.landcover import measure, plot_box, read_cells
    from app.web.maps import PLOTS

    data = client.get("/api/plot-map", params={"plot": "P-042"}).json()
    plot = {"polygon_wkt": PLOTS["P-042"].polygon_wkt}
    expected = measure(plot, read_cells(*plot_box(plot)))
    m = data["measure"]
    assert (m["forest_2020_ha"], m["loss_after_2020_ha"]) == (expected["forest_2020_ha"], expected["loss_after_2020_ha"])
    assert m["forest_2020_ha"] == pytest.approx(4.62, abs=0.01) and m["loss_after_2020_ha"] == pytest.approx(3.42, abs=0.01)
    assert m["outline_ha"] == pytest.approx(10.0, abs=0.01) and m["main_loss_year"] == 2021
    w = data["window"]
    assert all(w["lat_min"] <= lat <= w["lat_max"] and w["lon_min"] <= lon <= w["lon_max"] for lat, lon, _, _ in data["cells"])
    assert len(data["plot"]["vertices"]) == 4
    shipped = (Path(__file__).resolve().parents[1] / "app/web/static/maps/P-042.jpg").exists()
    assert data["image"] == ("/static/maps/P-042.jpg" if shipped else None)  # real scene once fetched

    honest = client.get("/api/plot-map", params={"plot": "P-041"}).json()["measure"]
    assert honest["forest_2020_ha"] == 0 and honest["loss_after_2020_ha"] == 0
    assert client.get("/api/plot-map", params={"plot": "P-999"}).status_code == 404


def test_a_background_image_is_shown_only_when_it_exists(client, monkeypatch, tmp_path):
    from app.web import maps

    monkeypatch.setattr(maps, "MAPS_DIR", tmp_path)
    assert client.get("/api/plot-map", params={"plot": "P-041"}).json()["image"] is None
    (tmp_path / "P-041.jpg").write_bytes(b"\xff\xd8\xff")
    assert client.get("/api/plot-map", params={"plot": "P-041"}).json()["image"] == "/static/maps/P-041.jpg"


def test_the_reliability_tests_give_what_scripts_bench_gives(client):
    from scripts.bench import run_all

    expected = {r["case"]: r["got"] for r in run_all()}
    rows = [client.post(f"/ui/bench/{case}").json() for case in sorted(expected)]
    assert {r["case"]: r["got"] for r in rows} == expected
    assert all(r["ok"] == (r["got"] == r["expected"]) and r["mode"] == "sample" for r in rows)
    assert client.get(f"/runs/{rows[1]['run_id']}").json()["verdict"] == rows[1]["got"]  # its evidence file opens
    assert client.post("/ui/bench/99").status_code == 404


def test_evidence_files_lists_the_runs_kept(client):
    run_id = start(client, case=2)
    events(client, run_id)
    listed = {r["run_id"]: r for r in client.get("/ui/runs").json()}
    assert listed[run_id]["claim"] == CLAIM and listed[run_id]["verdict"] == "CONTRADICTED"


def test_the_map_route_defaults_to_p042_and_draws_only_real_cells(client):
    default = client.get("/api/plot-map").json()
    assert default["plot"]["id"] == "P-042" and default["measure"]["covered"]
    grid = default["grid"]
    # Every cell drawn is EU forest in 2020 or carries a Hansen loss year, as the file says.
    assert all(forest >= grid["forest_from"] or 2001 <= year <= 2024 for _, _, forest, year in default["cells"])
    assert client.get("/api/plot-map", params={"plot": "x" * 40}).status_code == 422


def test_the_visual_rules_hold_in_the_stylesheet():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert "gradient(" not in css and "backdrop-filter" not in css  # no gradient, no glass
    assert "infinite" not in css  # no permanent animation
    motion = css.split("@media (prefers-reduced-motion: no-preference)", 1)
    assert len(motion) == 2 and "animation" not in motion[0]  # motion only for people who did not turn it off
    sizes = {int(n) for n in re.findall(r"font-size:\s*(\d+)px", css)}
    assert sizes <= {12, 14, 15, 16, 20, 24, 40, 64}, sizes  # 15: the trace rows, the system's one exception
    radii = {int(n) for n in re.findall(r"border-radius:\s*(\d+)px", css)}
    assert radii <= {6, 10, 16, 20, 999}, radii  # 999: the status pill of mockup 3


# ---- the judge's plot: drawn or uploaded, measured, then checked by a real run ------------------------

def square(lat, lon, area, plot_id="P-900"):
    from app.world import geo
    ring = [[float(x), float(y)] for x, y in (pair.split() for pair in geo.square_wkt(lat, lon, area)[9:-2].split(", "))]
    return {"type": "Feature", "properties": {"plot_id": plot_id}, "geometry": {"type": "Polygon", "coordinates": [ring]}}


def test_plot_measure_gives_the_numbers_the_run_will_use(client):
    drawn = client.post("/api/plot-measure", json={"plot": square(5.7300, -6.5220, 10.0)}).json()
    shown = client.get("/api/plot-map", params={"plot": "P-042"}).json()["measure"]
    assert drawn["plot"]["plot_id"] == "P-900" and drawn["kind"] == "polygon"
    for key in ("forest_2020_ha", "loss_after_2020_ha", "loss_ha_by_year", "main_loss_year", "covered"):
        assert drawn["measure"][key] == shown[key], key


def test_plot_measure_outside_the_maps_says_no_reference_data(client):
    far = client.post("/api/plot-measure", json={"plot": square(6.70, -6.45, 5.0)}).json()["measure"]
    assert far["covered"] is False and far["reason"] == "no reference data for this area"
    assert far["forest_2020_ha"] is None  # not measured is not zero


@pytest.mark.parametrize("plot, words", [
    ({"type": "LineString", "coordinates": [[-6.52, 5.73], [-6.51, 5.74]]}, "a Polygon, or a Point"),
    ({"type": "Point", "coordinates": [-6.52, 5.73]}, "area_ha"),
    ({"type": "Polygon", "coordinates": [[[-6.52, 5.73], [-6.51, 5.74], [-6.51, 5.73], [-6.52, 5.74], [-6.52, 5.73]]]},
     "invalid polygon"),
    ("not geojson", "GeoJSON object"),
])
def test_plot_measure_refuses_a_bad_plot_with_the_pipelines_reason(client, plot, words):
    response = client.post("/api/plot-measure", json={"plot": plot})
    assert response.status_code == 422 and words in response.json()["detail"]


def test_a_drawn_plot_runs_live_and_its_timeline_shows_the_loss(client):
    response = client.post("/runs", json={"input": {"claim": CLAIM, "plot": square(5.7300, -6.5220, 10.0)},
                                          "options": {"sample_mode": True, "case": 1}})
    run_id = response.json()["run_id"]
    *trace, final = events(client, run_id)
    actions = [json.loads(e["data"])["action"] for e in trace]
    assert "receive_plot" in actions and any("Q10" in json.loads(e["data"])["input_summary"] for e in trace)
    output = json.loads(final["data"])
    assert output["verdict"] == "CONTRADICTED" and output["findings"][0]["evidence"][0] == {"source": "plot", "id": "P-900"}
    (plot,) = client.get(f"/runs/{run_id}/timeline").json()["plots"]
    assert plot["plot_id"] == "P-900" and plot["reference_maps"]["loss_after_2020_ha"] == pytest.approx(3.42, abs=0.01)
