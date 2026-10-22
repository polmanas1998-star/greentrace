"""The plot a judge draws (C4): measured on the real maps, judged by rule 2 (kit v7), re-checked by R9.

The two demo plots are squares on their points (no exact outline was recorded); their
numbers are the MEASURED ones, also kept in data/reference_cells.meta.json.
"""
import copy
import gzip
import io
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import verify
from app import orchestrator
from app.api import app
from app.checks.landcover import CELLS_FILE, NO_DATA, cell_area_ha, load_meta, measure, plot_box, read_cells
from app.memory.fallback import MemoryEngine
from app.pipeline import run_case
from app.queries import DataGate, SQLiteEngine
from app.receipt import build_receipt
from app.rules import CONTRADICTED, PROVEN, TREE_COVER_NOT_FOREST, UNVERIFIABLE, map_check
from app.world import drawn, geo
from app.world.cases import build_case
from app.world.store import reference_db, to_sqlite

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_05 = ROOT / "examples" / "05_real_plot_contradicted.json"
EXAMPLE_06 = ROOT / "examples" / "06_real_plot_unverifiable.json"

pytestmark = pytest.mark.skipif(not CELLS_FILE.exists(), reason="data/reference_cells.csv.gz not built")


def run_example(path, run_id, **changes):
    request = json.loads(path.read_text(encoding="utf-8"))
    request["input"].update(changes)
    request.update(run_id=run_id, options={**request["options"], "sample_mode": True})
    return run_case(request, stream=io.StringIO(), memory_engine=MemoryEngine())


def drawn_square(lat, lon, area, plot_id="P-041"):
    ring = [[float(x), float(y)] for x, y in (pair.split() for pair in
                                              geo.square_wkt(lat, lon, area)[9:-2].split(", "))]
    return {"type": "Feature", "properties": {"plot_id": plot_id},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


# ---- the two real plots of the demo ----------------------------------------------------------

def test_p042_is_contradicted_by_the_maps_and_survives_its_refutations():
    out = run_example(EXAMPLE_05, "rp-05")
    assert out["verdict"] == CONTRADICTED
    (finding,) = out["findings"]
    assert (finding["rule"], finding["claim_part"]) == (2, "no_deforestation")
    assert finding["finding"].startswith("3.42 ha that the EU map counted as forest in 2020 were lost inside P-042 "
                                         "in 2021, 2022, 2023, 2024")
    assert finding["evidence"] == [{"source": "plot", "id": "P-042"}, {"source": "cells", "id": "P-042"}]
    assert [(t["id"], t["result"]) for t in finding["refutations_tried"]] == [
        ("R3", "rejected"), ("R6", "rejected"), ("R9", "rejected")]
    assert out["flip"] == "The claim would be PROVEN if plot P-042 had delivered nothing after 2020."


def test_p041_is_unverifiable_tree_cover_lost_but_not_2020_forest():
    out = run_example(EXAMPLE_06, "rp-06")
    assert out["verdict"] == UNVERIFIABLE
    (finding,) = out["findings"]
    assert finding["rule"] == 3
    assert finding["finding"] == f"P-041: 0.51 ha of tree cover lost in 2021, 2024; {TREE_COVER_NOT_FOREST}"
    assert out["recommended_actions"] == ["Ask the supplier for a field report on P-041."]


def test_the_examples_say_what_the_meta_file_measured():
    measured = load_meta()["measured"]
    for path, plot_id in ((EXAMPLE_05, "P-042"), (EXAMPLE_06, "P-041")):
        plot = drawn.plot_from_geojson(json.loads(path.read_text(encoding="utf-8"))["input"]["plot"])
        assert (plot["plot_id"], plot["polygon_wkt"]) == (plot_id, measured[plot_id]["polygon_wkt"])
        assert plot["area_ha"] == pytest.approx(measured[plot_id]["area_ha"], abs=0.01)


def test_the_drawn_plot_changes_only_what_it_adds():
    """Same world, no plot drawn: PROVEN. The coverage holds: the declared volume grew by the plot's tonnes."""
    request = json.loads(EXAMPLE_05.read_text(encoding="utf-8"))
    del request["input"]["plot"]
    out = run_case({**request, "run_id": "rp-none", "options": {"case": 1, "sample_mode": True}},
                   stream=io.StringIO(), memory_engine=MemoryEngine())
    assert out["verdict"] == PROVEN
    parts = {p["claim_part"]: p["verdict"] for p in run_example(EXAMPLE_05, "rp-parts")["parts"]}
    assert parts == {"coverage": PROVEN, "no_deforestation": CONTRADICTED, "plausibility": PROVEN, "scope": PROVEN}
    evidence = orchestrator.RUNS["rp-parts"].evidence
    assert evidence.company["total_tonnes"] == round(
        orchestrator.RUNS["rp-none"].evidence.company["total_tonnes"] + 1.5, 2)


# ---- where the maps cannot say -------------------------------------------------------------------

def test_a_plot_outside_the_maps_gets_no_reference_data():
    out = run_example(EXAMPLE_06, "rp-out", plot=drawn_square(6.70, -6.45, 5.0))  # east of Daloa
    assert out["verdict"] == UNVERIFIABLE
    (finding,) = out["findings"]
    assert (finding["rule"], finding["finding"]) == (1, f"P-041: {NO_DATA}")


def test_without_the_cells_the_maps_are_a_source_that_did_not_answer(monkeypatch):
    monkeypatch.setattr(orchestrator, "attach_reference", lambda connection: False)
    out = run_example(EXAMPLE_05, "rp-nomaps")
    assert out["verdict"] == UNVERIFIABLE
    assert [f["finding"] for f in out["findings"]] == ["P-042: the reference maps did not answer"]


def test_a_large_point_needs_a_polygon_and_a_small_one_is_measured_as_its_disk():
    big = {"type": "Point", "coordinates": [-6.5220, 5.7300]}
    out = run_example(EXAMPLE_05, "rp-point-big", plot={"type": "Feature", "geometry": big,
                                                         "properties": {"area_ha": 10.0}})
    assert [(f["rule"], f["verdict"]) for f in out["findings"]] == [(1, UNVERIFIABLE)]
    assert "declared by a point" in out["findings"][0]["finding"]

    run_example(EXAMPLE_05, "rp-point", plot={"type": "Feature", "geometry": big, "properties": {"area_ha": 3.0}})
    maps = TestClient(app).get("/runs/rp-point/timeline").json()["plots"][0]["reference_maps"]
    assert maps["covered"] and maps["forest_2020_ha"] > 0


@pytest.mark.parametrize("plot, message", [
    ("not geojson", "GeoJSON object"),
    ({"type": "LineString", "coordinates": [[-6.5, 5.7], [-6.4, 5.8]]}, "a Polygon, or a Point"),
    ({"type": "Point", "coordinates": [-6.5, 5.7]}, "area_ha"),
    ({"type": "Polygon", "coordinates": [[[-6.5, 5.7], [-6.49, 5.7], [-6.5, 5.7]]]}, "a ring of 4"),
    ({"type": "Polygon", "coordinates": [[[-6.5, 5.7], [-6.49, 5.71], [-6.49, 5.7], [-6.5, 5.71]]]}, "invalid polygon"),
    ({"type": "Polygon", "coordinates": [[[-6.5, 5.7], [-6.5001, 5.7], [-6.5001, 5.7001], [-6.5, 5.7]]]},
     "ha, outside"),
    ({"type": "Point", "coordinates": [-6.5, 95.0], "properties": {"area_ha": 2}}, "longitude, latitude"),
    (drawn_square(5.73, -6.522, 10.0, plot_id="plot 7"), "plot_id"),
])
def test_bad_drawings_are_refused_with_a_reason(plot, message):
    with pytest.raises(ValueError, match=message):
        drawn.plot_from_geojson(plot)


def test_a_drawn_plot_cannot_take_a_registry_id_and_the_api_says_why():
    response = TestClient(app).post("/run", json={
        "input": {"claim": "100% deforestation-free since 2021.", "plot": drawn_square(5.73, -6.522, 10.0, "P-014")},
        "options": {"case": 1, "sample_mode": True}})
    assert response.status_code == 422 and "P-014 is already in the registry" in response.json()["detail"]


# ---- rule 2 of kit v7, on a hand-made grid of real-size cells -------------------------------------

TOP, LEFT = 17_200, 13_100  # near 5.70 N, 6.725 W, as in test_landcover


def grid(rows, cols, losses=(), forest=1.0):
    """rows x cols cells; losses: {(row, col): (year, forest fraction)}; and the plot covering exactly them."""
    cells = []
    for r in range(rows):
        for c in range(cols):
            year, fraction = dict(losses).get((r, c), (0, forest))
            cells.append({"lat": round(10.0 - (TOP + r + 0.5) * 0.00025, 6),
                          "lon": round(-10.0 + (LEFT + c + 0.5) * 0.00025, 6),
                          "forest_fraction_2020": fraction, "loss_year": year})
    north, west = 10.0 - TOP * 0.00025, -10.0 + LEFT * 0.00025
    south, east = 10.0 - (TOP + rows) * 0.00025, -10.0 + (LEFT + cols) * 0.00025
    area = sum(cell_area_ha(c["lat"]) for c in cells)
    plot = {"plot_id": "P-041", "area_ha": round(area, 4),
            "polygon_wkt": f"POLYGON(({west} {south}, {west} {north}, {east} {north}, {east} {south}, {west} {south}))"}
    return plot, cells


def lost(n, year=2022, fraction=1.0):
    return {(0, c): (year, fraction) for c in range(n)}


def code(finding):
    return (finding.code, finding.verdict, finding.rule) if finding else None


def test_half_a_hectare_of_2020_forest_lost_while_delivering_contradicts():
    plot, cells = grid(10, 10, lost(7))  # 7 cells = 0.536 ha, under 10 % of 7.66 ha
    assert code(map_check(plot, cells, {2021, 2025})) == ("forest_loss_in_delivering_plot", CONTRADICTED, 2)
    plot, cells = grid(10, 10, lost(6))  # 0.46 ha: below both thresholds
    assert code(map_check(plot, cells, {2021, 2025})) == ("forest_loss_below_threshold", UNVERIFIABLE, 3)


def test_ten_percent_of_a_small_plot_contradicts_even_under_half_a_hectare():
    plot, cells = grid(3, 3, lost(1))  # 1 cell of 9 = 11 %, 0.077 ha
    assert code(map_check(plot, cells, {2022})) == ("forest_loss_in_delivering_plot", CONTRADICTED, 2)


def test_only_loss_while_the_plot_delivered_counts():
    plot, cells = grid(10, 10, lost(10, year=2024))
    assert code(map_check(plot, cells, {2021, 2022, 2023}))[0] == "forest_loss_below_threshold"
    assert code(map_check(plot, cells, {2021, 2024}))[0] == "forest_loss_in_delivering_plot"


def test_loss_up_to_2020_is_not_the_claims_business():
    plot, cells = grid(10, 10, lost(10, year=2020))
    assert map_check(plot, cells, {2021, 2025}) is None
    plot, cells = grid(10, 10)
    assert map_check(plot, cells, {2021, 2025}) is None


def test_tree_cover_lost_on_land_the_eu_map_did_not_count_as_forest():
    plot, cells = grid(10, 10, lost(10, fraction=0.4), forest=0.0)
    finding = map_check(plot, cells, {2021, 2025})
    assert code(finding) == ("tree_cover_loss_outside_2020_forest", UNVERIFIABLE, 3)
    assert finding.detail.endswith(TREE_COVER_NOT_FOREST)


def test_no_cells_is_never_zero_loss():
    plot, cells = grid(10, 10, lost(10))
    assert code(map_check(plot, None, {2021}))[0] == "no_reference_data"
    assert map_check(plot, cells[:50], {2021}).detail == f"P-041: {NO_DATA}"  # half the plot has no cell


# ---- Q10, the receipt, the timeline ------------------------------------------------------------------

def test_q10_on_sqlite_reads_exactly_the_committed_cells():
    connection = to_sqlite(build_case(1))
    connection.execute("ATTACH DATABASE ? AS reference", (str(reference_db()),))
    gate = DataGate(SQLiteEngine(connection), run_id="rp-q10", log=lambda _: None)
    box = plot_box(drawn.plot_from_geojson(drawn_square(5.73, -6.522, 10.0)))
    rows = gate.ask("Investigator", "Q10", lat_min=box[0], lat_max=box[1], lon_min=box[2], lon_max=box[3]).rows
    assert rows == read_cells(*box) and len(rows) == 256


def test_the_reference_database_is_rebuilt_when_the_cells_change(tmp_path):
    cells = tmp_path / "cells.csv.gz"
    cells.write_bytes(CELLS_FILE.read_bytes())
    db = tmp_path / "cells.sqlite"
    assert reference_db(cells, db) == db
    first = db.stat().st_mtime_ns
    assert reference_db(cells, db) == db and db.stat().st_mtime_ns == first  # same cells, kept
    cells.write_bytes(gzip.compress(b"lat,lon,forest_fraction_2020,loss_year\n5.7,-6.7,1.000,2023\n"))
    reference_db(cells, db)
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM reference_cells").fetchone() == (1,)


def receipt_05(run_id="rp-receipt"):
    run_example(EXAMPLE_05, run_id)
    return json.loads(json.dumps(build_receipt(orchestrator.RUNS[run_id])))


def q10(receipt):
    return next(s for s in receipt["sources"] if s["query_id"] == "Q10" and s["agent"] == "Investigator")


def test_the_receipt_of_a_drawn_plot_is_valid_and_says_where_the_finding_comes_from():
    receipt = receipt_05()
    result = verify.check(receipt)
    assert (result["status"], result["verdict"], result["problems"]) == ("VALID", CONTRADICTED, [])
    assert receipt["declared_plot"]["row"]["plot_id"] == "P-042"
    index = q10(receipt)["index"]
    assert receipt["verdict"]["findings"][0]["derived_from"] == [
        "plot:P-042: declared_plot (drawn by the analyst)", f"Q10#{index} rows 0-255"]


def test_one_q10_cell_redated_2023_to_2019_is_tampered_and_stays_contradicted():
    """consigne v21.1, test (c): one 30 m cell is far below the 0.5 ha threshold."""
    receipt = receipt_05()
    plot = drawn.plot_from_geojson(receipt["declared_plot"]["input"])
    inside = {(c["lat"], c["lon"]) for c in measure(plot, q10(receipt)["rows"])["cells"]}
    number, row = next((n, r) for n, r in enumerate(q10(receipt)["rows"]) if r["loss_year"] == 2023
                       and r["forest_fraction_2020"] >= 0.5 and (r["lat"], r["lon"]) in inside)
    row["loss_year"] = 2019
    result = verify.check(receipt)
    assert result["status"] == "TAMPERED" and result["verdict"] == CONTRADICTED
    assert result["problems"][0] == f"Q10 row {number} (cell {row['lat']}, {row['lon']})"


def test_every_loss_wiped_from_q10_rows_recomputes_proven():
    receipt = receipt_05()
    for row in q10(receipt)["rows"]:
        row["loss_year"] = 0
    result = verify.check(receipt)
    assert result["status"] == "TAMPERED" and result["verdict"] == PROVEN


def test_a_redrawn_plot_row_is_caught():
    receipt = receipt_05()
    tampered = copy.deepcopy(receipt)
    tampered["declared_plot"]["row"]["area_ha"] = 100.0
    assert "declared_plot: the row does not follow from the GeoJSON drawn" in verify.check(tampered)["problems"]


def test_timeline_gives_loss_by_year_the_cutoff_and_the_deliveries():
    client = TestClient(app)
    request = json.loads(EXAMPLE_05.read_text(encoding="utf-8"))
    client.post("/run", json={**request, "run_id": "rp-tl", "options": {"case": 1, "sample_mode": True}})
    body = client.get("/runs/rp-tl/timeline").json()
    assert body["cutoff"] == "2020-12-31"
    (plot,) = body["plots"]
    measured = load_meta()["measured"]["P-042"]
    assert plot["plot_id"] == "P-042" and plot["declared_by"] == "analyst"
    assert plot["findings"] == ["forest_loss_in_delivering_plot"]
    assert plot["delivered_tonnes_by_year"] == {str(y): 1.5 for y in range(2021, 2026)}
    maps = plot["reference_maps"]
    assert maps["loss_ha_by_year"] == measured["loss_ha_by_year"]
    assert maps["forest_loss_ha_by_year"] == measured["forest_loss_ha_by_year"]
    assert len(maps["cells"]) == 144 and {"loss_year", "forest_fraction_2020", "overlap"} <= set(maps["cells"][0])
    assert client.get("/runs/nope/timeline").status_code == 404


def test_timeline_of_case_2_shows_the_alert_inside_p014():
    client = TestClient(app)
    client.post("/run", json={"run_id": "rp-tl2", "input": {"claim": "100% deforestation-free since 2021."},
                              "options": {"case": 2, "sample_mode": True}})
    (plot,) = client.get("/runs/rp-tl2/timeline").json()["plots"]
    assert plot["plot_id"] == "P-014" and plot["reference_maps"] is None
    assert [(a["alert_id"], a["alert_date"]) for a in plot["alerts"]] == [("A-221", "2022-03-14")]
