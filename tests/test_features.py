"""Suppliers to check first, citizen mode, satellite time machine: real counts, real runs, real scenes."""
import io
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import citizen, orchestrator
from app import timelapse as tl
from app.api import app
from app.checks import landcover
from app.explain.suppliers import NO_FINDING, STATUS_ORDER, rank
from app.memory.fallback import MemoryEngine
from app.pipeline import run_case
from app.receipt import decision_for
from app.world.cases import CASES, DEFAULT_CLAIM, build_case
from scripts import fetch_satellite as sat

TODAY = date(2026, 10, 22)


def run(case, run_id=None, claim=DEFAULT_CLAIM):
    run_id = run_id or f"ft-{case:02d}"
    out = run_case({"run_id": run_id, "input": {"claim": claim}, "options": {"sample_mode": True, "case": case}},
                   stream=io.StringIO(), memory_engine=MemoryEngine())
    return out, orchestrator.RUNS[run_id]


def ranked(case):
    _, investigation = run(case)
    return rank(investigation.evidence, decision_for(investigation.evidence, []))


def fiscal_tonnes(case):
    world = build_case(case)
    year = world["company"][0]["fiscal_year"]
    totals = {}
    for d in world["deliveries"]:
        if d["year"] == year:
            totals[d["supplier_id"]] = round(totals.get(d["supplier_id"], 0.0) + d["tonnes"], 2)
    return totals


# ---- 1. which suppliers to check first ------------------------------------------------------------------

def test_case_2_puts_f007_first_with_the_field_report_it_needs():
    out = ranked(2)
    first, *rest = out["suppliers"]
    assert (first["rank"], first["supplier_id"], first["status"]) == (1, "F-007", "CONTRADICTED")
    assert first["findings"] == {"contradicted": 1, "unverifiable": 0}
    assert first["missing_proof"] == "Ask F-007 for a field report on P-014, and hold its deliveries."
    assert first["why"].startswith("Rank 1: 1 finding(s) contradict the claim on this supplier;")
    assert {r["status"] for r in rest} == {NO_FINDING} and all(r["missing_proof"] is None for r in rest)


def test_the_tonnes_are_the_rows_tonnes_nothing_else():
    for case in (2, 8, 10, 12):
        out = ranked(case)
        assert {r["supplier_id"]: r["tonnes"] for r in out["suppliers"]} == fiscal_tonnes(case)


def test_case_8_asks_f031_for_the_plots_of_its_untraced_tonnes():
    first = ranked(8)["suppliers"][0]
    tonnes = fiscal_tonnes(8)["F-031"]
    assert (first["supplier_id"], first["status"], first["untraced_tonnes"], first["tonnes"]) == (
        "F-031", "UNVERIFIABLE", tonnes, tonnes)
    assert first["missing_proof"] == f"Ask F-031 for the plots its {tonnes:g} t of 2025 came from."


def test_case_10_ranks_both_declarers_of_p022_by_tonnes():
    first, second = ranked(10)["suppliers"][:2]
    assert {first["supplier_id"], second["supplier_id"]} == {"F-002", "F-019"}
    assert first["status"] == second["status"] == "UNVERIFIABLE" and first["tonnes"] > second["tonnes"]
    assert first["why"].endswith(f"more than {second['supplier_id']} ({second['tonnes']:g} t) with the same status.")
    assert first["missing_proof"] == second["missing_proof"] == "Ask both suppliers which one farms P-022."


def test_case_12_has_no_finding_with_rules_alone_so_tonnes_decide():
    out = ranked(12)
    assert {r["status"] for r in out["suppliers"]} == {NO_FINDING}
    tonnes = [r["tonnes"] for r in out["suppliers"]]
    assert tonnes == sorted(tonnes, reverse=True)
    assert all("no finding concerns this supplier" in r["why"] for r in out["suppliers"])


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.number:02d}")
def test_the_written_rule_holds_on_every_case(case):
    rows = ranked(case.number)["suppliers"]
    keys = [(STATUS_ORDER.index(r["status"]), -r["tonnes"], r["supplier_id"]) for r in rows]
    assert keys == sorted(keys) and [r["rank"] for r in rows] == list(range(1, len(rows) + 1))


def test_the_route_reads_the_finished_run_and_never_changes_its_verdict():
    client = TestClient(app)
    out = client.post("/run", json={"run_id": "ft-api", "input": {"claim": DEFAULT_CLAIM},
                                    "options": {"case": 2, "sample_mode": True}}).json()
    body = client.get("/runs/ft-api/suppliers").json()
    assert body["verdict"] == out["verdict"] == "CONTRADICTED" and body["fiscal_year"] == 2025
    assert client.get("/runs/ft-api").json()["verdict_sha256"] == out["verdict_sha256"]
    assert client.get("/runs/nope/suppliers").status_code == 404


# ---- 2. citizen mode -------------------------------------------------------------------------------------

def ask(body, case=None):
    def investigate(request):
        request["options"]["sample_mode"] = True
        return run_case(request, stream=io.StringIO(), memory_engine=MemoryEngine())
    return citizen.ask({**body, **({"case": case} if case else {})}, investigate, f"ft-citizen-{case or 2}")


KALOU = {"brand": "Kalou Chocolat", "product": "Kalou Dark 70 %", "promise": DEFAULT_CLAIM}


def test_kalou_on_case_2_is_false_with_the_real_run_behind():
    answer = ask(KALOU)
    direct, _ = run(2, "ft-direct")
    assert answer["answer"] == "False" and answer["verdict_sha256"] == direct["verdict_sha256"]
    assert answer["sentence"] == "False: satellites saw forest cleared on a farm that kept selling cocoa afterwards."
    assert 2 <= len(answer["reasons"]) <= 3
    assert answer["reasons"][1].startswith("GreenTrace tried 6 ways to prove that wrong")
    assert answer["evidence_file"] == f"/runs/{answer['run_id']}"


@pytest.mark.parametrize("case, expected", [(1, "True as far as the evidence goes"), (8, "No proof found"),
                                            (18, "True as far as the evidence goes")])
def test_the_answer_is_the_rules_verdict_in_plain_words(case, expected):
    answer = ask(KALOU, case)
    assert answer["answer"] == expected and 2 <= len(answer["reasons"]) <= 3
    assert answer["verdict"] == run(case, f"ft-c{case}")[0]["verdict"]


def test_a_vague_promise_finds_no_proof():
    answer = ask({**KALOU, "promise": "We love the planet."})
    assert answer["answer"] == "No proof found" and answer["verdict"] is None and len(answer["reasons"]) == 2


@pytest.mark.parametrize("brand", ["Nestle", "Lindt", "Cote d'Or", "Kalou", "Chocolat", "ChocoDurable SA"])
def test_any_other_brand_gets_no_verdict_and_no_run(brand):
    before = len(orchestrator.RUNS)
    answer = ask({**KALOU, "brand": brand})
    assert answer == {"answer": None, "sentence": citizen.NO_DATA_BRAND, "reasons": [], "evidence_file": None}
    assert len(orchestrator.RUNS) == before


def test_the_demo_brand_is_matched_whatever_the_spelling_but_not_another_product():
    assert ask({**KALOU, "brand": "  KALOU-chocolat ", "product": "kalou dark 70%"})["answer"] == "False"
    assert ask({**KALOU, "product": "Kalou Milk"})["sentence"] == citizen.NO_DATA_PRODUCT


def test_the_route_validates_and_never_grades_a_brand():
    client = TestClient(app)
    body = client.post("/citizen", json=KALOU).json()
    assert body["answer"] == "False" and not {"score", "rating", "grade"} & set(body)
    assert client.get(body["evidence_file"]).status_code == 200
    for bad in ({"brand": "Kalou Chocolat"}, {**KALOU, "promise": "x" * 501}, {**KALOU, "case": 99}, ["no"]):
        assert client.post("/citizen", json=bad).status_code == 422


# ---- 3. satellite time machine -----------------------------------------------------------------------------

def quiet(**kwargs):
    return tl.timelapse(today=TODAY, log=lambda record: None, **kwargs)


def test_sample_mode_shows_only_the_scenes_the_repository_ships():
    out = quiet(plot="P-042", sample=True)
    assert [y["year"] for y in out["years"]] == list(range(2019, 2027))
    with_image = {y["year"]: y["image"] for y in out["years"] if y["image"]}
    assert with_image == {2020: "/static/maps/P-042_2020.jpg", 2026: "/static/maps/P-042.jpg"}
    assert with_image.keys() == {y["year"] for y in out["years"] if y["status"] == "scene"}
    assert all(y["status"] == tl.NOT_IN_SAMPLE for y in out["years"] if not y["image"])
    dates = {y["year"]: y["scene"]["date"] for y in out["years"] if y["scene"]}
    assert dates == {2020: "2020-01-03", 2026: "2026-03-12"}


def test_the_frame_is_the_maps_frame_and_the_interface_frame():
    from app.web import maps

    out = quiet(plot="P-041", sample=True)
    assert out["frame"] == sat.frame(*sat.PLOTS["P-041"]) == maps._window(maps.PLOTS["P-041"])
    assert out["pixels"] == [960, 640]


def test_each_year_carries_that_years_hansen_loss_in_the_frame():
    out = quiet(plot="P-042", sample=True)
    box = out["frame"]
    cells = [c for c in landcover.read_cells(box["lat_min"], box["lat_max"], box["lon_min"], box["lon_max"])]
    for year in out["years"]:
        if year["year"] > tl.HANSEN_LAST_YEAR:
            assert year["hansen_loss"] is None and year["hansen_note"] == "Hansen v1.12 maps loss up to 2024"
            continue
        lost = [c for c in cells if c["loss_year"] == year["year"]]
        assert year["hansen_loss"]["cells"] == len(lost)
        assert year["hansen_loss"]["ha"] == pytest.approx(sum(landcover.cell_area_ha(c["lat"]) for c in lost), abs=1e-3)
    assert out["years"][2]["hansen_loss"]["ha_on_2020_forest"] > 0  # P-042: 2020 forest lost in 2021


def test_a_shipped_year_and_its_image_route(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "SHIPPED_DIR", tmp_path)
    (tmp_path / "P-041_2022.jpg").write_bytes(b"\xff\xd8 a jpeg made by the test")
    (tmp_path / "P-041_2022.json").write_text(json.dumps({"scene_id": "S2_TEST", "date": "2022-01-15",
                                                           "licence": "test"}), encoding="utf-8")
    year = next(y for y in quiet(plot="P-041", sample=True)["years"] if y["year"] == 2022)
    assert (year["image"], year["scene"]["scene_id"]) == ("/api/timelapse/image/P-041_2022.jpg", "S2_TEST")
    client = TestClient(app)
    assert client.get(year["image"]).content == b"\xff\xd8 a jpeg made by the test"
    for name in ("..%2F..%2F.env", "P-041_2022.json", "missing.jpg"):
        assert client.get(f"/api/timelapse/image/{name}").status_code == 404


def test_live_mode_fetches_the_missing_years_once_and_writes_failures_down(tmp_path):
    calls = []

    def fetcher(key, lat, lon, season, out_dir, name, quiet=False, box=None):
        calls.append(season["year"])
        if season["year"] == 2023:
            raise sat.SatelliteError("no Sentinel-2 L2A scene of the 2022-2023 dry season under 50 % cloud")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{name}.jpg").write_bytes(b"jpeg")
        (out_dir / f"{name}.json").write_text(json.dumps({"scene_id": f"S2_{season['year']}",
                                                           "date": f"{season['year']}-02-01"}), encoding="utf-8")

    kwargs = dict(plot="P-042", sample=False, fetcher=fetcher, cache_dir=tmp_path, shipped_dir=tmp_path / "none")
    out = quiet(**kwargs)
    assert calls == [2019, 2021, 2022, 2023, 2024, 2025]  # 2020 and 2026 ship with the repository
    year_2023 = next(y for y in out["years"] if y["year"] == 2023)
    assert year_2023["image"] is None and year_2023["status"].startswith("no usable scene: no Sentinel-2")
    calls.clear()
    quiet(**kwargs)
    assert calls == []  # fetched years come from the cache; the failure waits RETRY_DAYS
    tl.timelapse(today=date(2026, 10, 30), log=lambda record: None, **kwargs)
    assert calls == [2023]


def test_live_mode_says_when_it_cannot_fetch(tmp_path):
    def no_rasterio(*args, **kwargs):
        raise ImportError("No module named 'rasterio'")

    out = quiet(plot="P-041", sample=False, fetcher=no_rasterio, cache_dir=tmp_path, shipped_dir=tmp_path)
    assert {y["status"] for y in out["years"] if not y["image"]} == {
        "on-demand fetching needs rasterio (pip install -r scripts/requirements-maps.txt)"}
    spent = quiet(plot="P-041", sample=False, fetcher=no_rasterio, cache_dir=tmp_path, shipped_dir=tmp_path,
                  budget_s=-1)
    assert all(y["status"].startswith("not fetched yet") for y in spent["years"] if not y["image"])


def test_any_point_and_a_plot_centre_is_that_plot():
    point = quiet(lat=5.72, lon=-6.60, sample=True)
    assert point["plot"] is None and not any(y["image"] for y in point["years"])
    assert any(y["hansen_loss"] and y["hansen_loss"]["cells"] for y in point["years"])
    assert quiet(lat=5.73, lon=-6.522, sample=True)["plot"] == "P-042"
    away = quiet(lat=6.80, lon=-6.60, sample=True)  # outside Soubre: sample mode has no loss map there
    assert away["hansen"]["note"] == tl.NO_LOSS_MAP and all(y["hansen_loss"] is None for y in away["years"])
    client = TestClient(app)
    assert client.get("/api/timelapse?plot=P-042").json()["mode"] == "sample"
    assert client.get("/api/timelapse?lat=6.8&lon=-6.6").status_code == 200
    assert client.get("/api/timelapse?lat=89&lon=-6.6").status_code == 422
    assert client.get("/api/timelapse").status_code == 422


def test_seasons_run_december_to_march():
    assert tl.season_year("2019-12-20") == 2020 and tl.season_year("2020-03-31") == 2020
    assert sat.dry_season(2020)["dates"] == sat.BEFORE["dates"]


def test_the_timelapse_option_writes_every_year_down_without_network(tmp_path, monkeypatch):
    def fake_fetch(plot_id, out_dir, season):
        if season["year"] % 2:
            raise sat.SatelliteError("no clean frame")
        (out_dir / f"{plot_id}_{season['year']}.jpg").write_bytes(b"jpeg")
        return {"year": season["year"]}

    monkeypatch.setattr(sat, "fetch", fake_fetch)
    tmp_path.mkdir(exist_ok=True)
    done = sat.timelapse("P-041", tmp_path, last_year=2022)
    assert [d["year"] for d in done] == [2019, 2020, 2021, 2022]
    odd = json.loads((tmp_path / "P-041_2021.json").read_text(encoding="utf-8"))
    assert odd["image"] is None and odd["status"] == "no usable scene: no clean frame"
    assert not (tmp_path / "P-041_2021.jpg").exists()
