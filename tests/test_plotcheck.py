"""The analyst's own plot file: read strictly, every plot measured and judged, exports and receipt."""
import copy
import hashlib
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from shapely import wkt

import verify
from app import plotcheck, plotfile
from app.api import app
from app.checks.landcover import CELLS_FILE, load_meta
from app.rules import CONTRADICTED, TREE_COVER_NOT_FOREST, UNVERIFIABLE
from app.web.features import map_engine
from app.world import geo

pytestmark = pytest.mark.skipif(not CELLS_FILE.exists(), reason="data/reference_cells.csv.gz not built")
MEASURED = load_meta()["measured"]


def square(plot_id, lat, lon, area, **properties):
    ring = [[x, y] for x, y in wkt.loads(geo.square_wkt(lat, lon, area)).exterior.coords]
    return {"type": "Feature", "properties": {"plot_id": plot_id, **properties},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


def collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


P042 = square("P-042", 5.7300, -6.5220, 10.0, supplier="Coop Nord", harvest="2025")
P041 = square("P-041", 5.7052, -6.7244, 10.6, supplier="Coop Sud")


def check(geojson, batch_id="pc-test", lines=None):
    return plotcheck.check(geojson, batch_id, map_engine(sample=True), year=2026,
                           log=(lines.append if lines is not None else lambda record: None))


# ---- every plot measured, judged by the rules ------------------------------------------------------------

def test_the_two_real_plots_get_the_verdicts_and_numbers_measured_before():
    out = check(collection(P041, P042))
    first, second = out["plots"]
    assert (first["plot_id"], first["verdict"], first["rule"], first["risk_rank"]) == ("P-042", CONTRADICTED, 2, 1)
    assert first["measure"]["forest_2020_ha"] == MEASURED["P-042"]["forest_2020_ha"]
    assert first["measure"]["loss_after_2020_ha"] == MEASURED["P-042"]["loss_after_2020_ha"]
    assert first["sentence"].startswith("3.42 ha that the EU map counted as forest in 2020 were lost inside P-042")
    assert (second["plot_id"], second["verdict"]) == ("P-041", UNVERIFIABLE)
    assert second["sentence"].endswith(f"{TREE_COVER_NOT_FOREST}.")
    assert out["summary"]["plots"] == {CONTRADICTED: 1, UNVERIFIABLE: 1, plotcheck.NO_ISSUE: 0}
    assert first["properties"] == {"plot_id": "P-042", "supplier": "Coop Nord", "harvest": "2025"}


def test_a_plot_with_no_issue_says_what_was_checked():
    clear = square("CLEAR", 5.6600, -6.7600, 2.0, supplier="Coop Nord")
    (result,) = check(collection(clear))["plots"]
    assert result["verdict"] == plotcheck.NO_ISSUE and result["rule"] == 4
    assert result["sentence"].startswith("No forest loss after 2020 inside CLEAR on the reference maps")
    assert result["evidence"]["query"] == "Q10" and result["measure"]["covered"] is True


def test_outside_the_maps_points_and_multipolygons():
    far = square("FAR", 6.80, -6.60, 3.0)
    point = {"type": "Feature", "properties": {"id": "PT", "area_ha": 2},
             "geometry": {"type": "Point", "coordinates": [-6.5220, 5.7300]}}
    no_area = {"type": "Feature", "properties": {"id": "NA"}, "geometry": {"type": "Point", "coordinates": [-6.6, 5.7]}}
    big_point = {"type": "Feature", "properties": {"id": "BIG", "area_ha": 9},
                 "geometry": {"type": "Point", "coordinates": [-6.6, 5.7]}}
    multi = {"type": "Feature", "properties": {"id": "MULTI"}, "geometry": {
        "type": "MultiPolygon", "coordinates": [P042["geometry"]["coordinates"], P041["geometry"]["coordinates"]]}}
    out = {r["plot_id"]: r for r in check(collection(far, point, no_area, big_point, multi))["plots"]}
    assert (out["FAR"]["verdict"], out["FAR"]["code"]) == (UNVERIFIABLE, "no_reference_data")
    assert out["FAR"]["sentence"] == "FAR: no reference data for this area."
    assert (out["NA"]["code"], out["BIG"]["code"]) == ("area_missing", "polygon_missing")
    assert out["PT"]["measure"]["covered"] is True
    assert out["MULTI"]["verdict"] == CONTRADICTED  # the P-042 part alone contradicts
    assert out["MULTI"]["area_ha"] == pytest.approx(20.6, abs=0.05)


def test_plots_and_suppliers_are_ranked_by_risk():
    out = check(collection(P041, square("CLEAR", 5.66, -6.76, 2.0, supplier="Coop Nord"), P042))
    assert [r["plot_id"] for r in out["plots"]] == ["P-042", "P-041", "CLEAR"]
    assert [s["supplier"] for s in out["suppliers"]] == ["Coop Nord", "Coop Sud"]
    assert out["suppliers"][0]["why"] == ("Rank 1: 1 contradicted, 0 unverifiable and 1 clear plot(s) of 2; "
                                          f"{MEASURED['P-042']['loss_after_2020_ha']:g} ha of 2020 forest lost "
                                          "on the contradicted ones.")


def test_every_plot_leaves_its_query_and_its_verdict_in_the_trace():
    lines = []
    check(collection(P042, P041), "pc-trace", lines)
    actions = [(line["agent_name"], line["action"]) for line in lines]
    assert actions[0] == ("Investigator", "receive_plot_file")
    assert actions.count(("Investigator", "query_zetaris")) == 2 and actions.count(("Investigator", "check_plot")) == 2
    assert actions[-1] == ("Writer", "rank_plots")


# ---- reading the file strictly ----------------------------------------------------------------------------

@pytest.mark.parametrize("geometry, message", [
    ({"type": "Point", "coordinates": [5.73, -96.5]}, "is not [longitude, latitude]: check the order"),
    ({"type": "LineString", "coordinates": [[-6.5, 5.7], [-6.4, 5.8]]}, "a LineString geometry"),
    ({"type": "Polygon", "coordinates": [[[-6.5, 5.7], [-6.49, 5.71], [-6.49, 5.7], [-6.5, 5.71], [-6.5, 5.7]]]},
     "invalid outline"),
    ({"type": "Polygon", "coordinates": [[[-6.5, 5.7], [-6.49, 5.7], [-6.5, 5.7]]]}, "at least 4 positions"),
    ({"type": "Polygon", "coordinates": [[[-6.5, 5.7], [-6.5001, 5.7], [-6.5001, 5.7001], [-6.5, 5.7]]]},
     "a plot must be between"),
    ({"type": "Point", "coordinates": [-6.5, 5.7, "x"]}, None),  # a third value is ignored, as GeoJSON allows
    (None, "no geometry"),
])
def test_each_bad_feature_says_why_and_the_others_are_checked(geometry, message):
    bad = {"type": "Feature", "properties": {"id": "BAD", "area_ha": 1}, "geometry": geometry}
    out = check(collection(P042, bad))
    if message is None:
        assert out["errors"] == []
        return
    assert [r["plot_id"] for r in out["plots"]] == ["P-042"]
    (error,) = out["errors"]
    assert (error["index"], error["id"]) == (2, "BAD") and message in error["error"]


def test_ids_are_read_or_numbered_and_duplicates_refused():
    nameless = copy.deepcopy(P041)
    nameless["properties"] = {}
    twice = square("P-042", 5.75, -6.60, 3.0)
    out = check(collection(P042, nameless, twice))
    assert {r["plot_id"] for r in out["plots"]} == {"P-042", "#2"}
    assert out["errors"] == [{"index": 3, "id": "P-042", "error": "the id 'P-042' is used twice in the file"}]
    assert {r["supplier"] for r in out["plots"]} == {"Coop Nord", plotfile.NOT_GIVEN}


def test_an_open_ring_is_closed_and_said():
    open_ring = copy.deepcopy(P042)
    open_ring["geometry"]["coordinates"][0].pop()
    out = check(collection(open_ring))
    assert out["warnings"] == ["P-042: an open ring was closed"] and out["plots"][0]["verdict"] == CONTRADICTED


@pytest.mark.parametrize("geojson, message", [
    ({"type": "Feature"}, "must be a GeoJSON FeatureCollection"),
    ({"type": "FeatureCollection", "features": []}, "has no features"),
    (collection(*[P042] * 501), "at most 500 per check"),
    ({**collection(P042), "crs": {"type": "name", "properties": {"name": "EPSG:32629"}}}, "EPSG:4326"),
    (collection({"type": "Feature", "properties": {}, "geometry": None}), "no plot could be read: no geometry"),
])
def test_a_file_that_cannot_be_read_is_refused_whole(geojson, message):
    with pytest.raises(plotfile.FileRefused, match=message):
        check(geojson)


# ---- exports, receipt, routes ------------------------------------------------------------------------------

def test_exports_keep_the_analysts_properties_and_the_receipt_re_derives():
    clear = square("CLEAR", 5.66, -6.76, 2.0, supplier="Coop Nord", ref="LOT-7")
    check(collection(P042, clear, P041), "pc-export")
    batch = plotcheck.BATCHES["pc-export"]
    (feature,) = plotcheck.no_issue_geojson(batch)["features"]
    assert feature["properties"]["ref"] == "LOT-7"
    assert feature["properties"]["greentrace_status"] == "no issue found by GreenTrace"
    rows = plotcheck.findings_csv(batch).splitlines()
    assert len(rows) == 4 and rows[1].startswith("1,P-042,Coop Nord,CONTRADICTED,2,")

    receipt = json.loads(json.dumps(plotcheck.receipt(batch)))
    assert verify.check(receipt) == {"status": "VALID", "problems": [], "verdict": "1 CONTRADICTED, 1 NO_ISSUE, "
                                     "1 UNVERIFIABLE", "seal": batch.output["seal"], "changes_replayed": 0}
    archive = zipfile.ZipFile(io.BytesIO(plotcheck.package_zip(batch)))
    manifest = json.loads(archive.read("manifest.json"))
    for entry in manifest["files"]:
        assert hashlib.sha256(archive.read(entry["name"])).hexdigest() == entry["sha256"]
    assert verify.check(json.loads(archive.read("receipt.json")))["status"] == "VALID"


def test_a_q10_row_changed_in_the_receipt_is_caught_and_the_verdict_recomputed():
    check(collection(P042), "pc-tamper")
    receipt = json.loads(json.dumps(plotcheck.receipt(plotcheck.BATCHES["pc-tamper"])))
    for row in receipt["sources"][0]["rows"]:
        row["loss_year"] = 0
    result = verify.check(receipt)
    assert result["status"] == "TAMPERED" and result["problems"][0].startswith("Q10 row 0 (cell ")
    assert "plot P-042: the rules give NO_ISSUE on these rows, the receipt says CONTRADICTED" in result["problems"]


def test_the_routes(monkeypatch):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    client = TestClient(app)
    out = client.post("/api/check-plots", json={"geojson": collection(P042, P041)}).json()
    assert out["summary"]["plots"][CONTRADICTED] == 1 and out["batch_id"].startswith("plots-")
    assert client.get(f"/api/check-plots/{out['batch_id']}").json()["seal"] == out["seal"]
    assert client.get(out["exports"]["findings.csv"]).headers["content-type"].startswith("text/csv")
    assert client.get(out["exports"]["plots_no_issue_found.geojson"]).json()["features"] == []
    assert verify.check(client.get(out["exports"]["receipt"]).json())["status"] == "VALID"
    assert client.get(out["exports"]["package"]).headers["content-type"] == "application/zip"
    assert client.post("/api/check-plots", json={"type": "Feature"}).status_code == 422
    assert client.get("/api/check-plots/nope").status_code == 404


def test_live_mode_without_zetaris_says_what_it_needs(monkeypatch):
    monkeypatch.setenv("SAMPLE_MODE", "false")
    for name in ("ZETARIS_REST_URL", "ZETARIS_API_KEY", "ZETARIS_ORG_ID", "ZETARIS_USERNAME", "ZETARIS_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    response = TestClient(app).post("/api/check-plots", json=collection(P042))
    assert response.status_code == 503 and "live mode needs Zetaris" in response.json()["detail"]
