"""The time machine on the analyst's own plots: frame fitted to the outline, loss cells per year, anywhere."""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from shapely import wkt
from shapely.geometry import box as rectangle

from app import plotcheck
from app import timelapse as tl
from app.api import app
from app.checks import gfw
from app.checks.landcover import CELLS_FILE
from app.web.features import map_engine
from app.world import geo
from scripts import fetch_satellite as sat
from tests.test_gfw import GHANA, FakeTiles, tile_with

pytestmark = pytest.mark.skipif(not CELLS_FILE.exists(), reason="data/reference_cells.csv.gz not built")
TODAY = date(2026, 10, 22)


def square(plot_id, lat, lon, area):
    ring = [[x, y] for x, y in wkt.loads(geo.square_wkt(lat, lon, area)).exterior.coords]
    return {"type": "Feature", "properties": {"plot_id": plot_id},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


def batch(*features, batch_id="tm-test"):
    plotcheck.check({"type": "FeatureCollection", "features": list(features)}, batch_id, map_engine(sample=True),
                    year=2026, log=lambda record: None)
    return batch_id


def in_frame(frame, lat, lon):
    return frame["lat_min"] <= lat <= frame["lat_max"] and frame["lon_min"] <= lon <= frame["lon_max"]


def test_the_analysts_p042_gets_the_shipped_scenes_and_the_orange_cells(monkeypatch):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    batch_id = batch(square("P-042", 5.7300, -6.5220, 10.0))
    out = TestClient(app).get(f"/api/check-plots/{batch_id}/plots/P-042/timelapse").json()
    assert out["plot"] == "P-042" and out["frame"] == sat.frame(5.73, -6.522)
    # the analyst's own square around P-042 has the frame of the shipped P-042 scenes
    assert {y["year"]: y["image"] for y in out["years"] if y["image"]} == {
        year: f"/api/timelapse/image/P-042_{year}.jpg" for year in range(2019, 2027)}
    year_2021 = next(y for y in out["years"] if y["year"] == 2021)
    lost = year_2021["hansen_loss"]["lost"]
    assert len(lost) == year_2021["hansen_loss"]["cells"] > 0
    assert all(in_frame(out["frame"], lat, lon) for lat, lon, _ in lost)
    assert any(on_forest for _, _, on_forest in lost)  # P-042: 2020 forest lost in 2021
    assert out["outline"]["type"] == "Polygon" and out["hansen"]["cell_deg"] == 0.00025


def test_a_large_plot_gets_a_larger_frame_that_holds_it():
    outline = wkt.loads(geo.square_wkt(5.7100, -6.6000, 200.0))  # 1.4 km a side
    out = tl.timelapse(None, 5.71, -6.60, outline=outline, sample=True, today=TODAY, log=lambda record: None)
    frame = out["frame"]
    assert frame["width_m"] > 1200 and frame["width_m"] / frame["height_m"] == pytest.approx(1.5, abs=0.01)
    assert rectangle(frame["lon_min"], frame["lat_min"], frame["lon_max"], frame["lat_max"]).contains(outline)
    assert out["key"].endswith(f"_{frame['width_m']}m") and out["plot"] is None


def test_far_from_soubre_sample_mode_says_there_is_no_loss_map(monkeypatch):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    batch_id = batch(square("GH-1", *GHANA, 5.0), batch_id="tm-ghana")
    out = TestClient(app).get(f"/api/check-plots/{batch_id}/plots/GH-1/timelapse").json()
    assert out["hansen"]["note"] == tl.NO_LOSS_MAP and out["hansen"]["source"] is None
    assert all(y["hansen_loss"] is None and not y["image"] for y in out["years"])


def test_far_from_soubre_live_mode_reads_the_loss_from_the_tiles(tmp_path):
    fake = FakeTiles()
    try:
        x, y = gfw.tile_xy(*GHANA)
        fake.tiles[(int(x), int(y))] = tile_with(2018, 2022)  # the frame lies in the tile's east half
        tiles = gfw.Tiles(url=fake.url, cache_dir=tmp_path / "gfw")
        outline = wkt.loads(geo.square_wkt(*GHANA, 5.0))
        out = tl.timelapse(None, *GHANA, outline=outline, sample=False, remote=tiles, today=TODAY,
                           fetcher=lambda *args, **kwargs: (_ for _ in ()).throw(ImportError("no rasterio")),
                           cache_dir=tmp_path / "scenes", shipped_dir=tmp_path / "none", log=lambda record: None)
    finally:
        fake.close()
    assert out["hansen"]["source"] == gfw.SOURCE
    by_year = {y["year"]: y["hansen_loss"] for y in out["years"]}
    assert by_year[2022]["cells"] > 0  # the 2018 loss of the west half is outside the frame, and before 2019
    assert all(by_year[year]["cells"] == 0 for year in (2019, 2020, 2021, 2023, 2024))
    assert by_year[2022]["ha_on_2020_forest"] is None  # no EU 2020 forest map there
    assert all(flag is None for _, _, flag in by_year[2022]["lost"])


def test_unknown_plot_and_a_point_without_area():
    point = {"type": "Feature", "properties": {"id": "PT"}, "geometry": {"type": "Point", "coordinates": [-6.6, 5.72]}}
    batch_id = batch(point, batch_id="tm-point")
    client = TestClient(app)
    out = client.get(f"/api/check-plots/{batch_id}/plots/PT/timelapse").json()
    assert out["outline"] is None and out["frame"] == sat.frame(5.72, -6.6)
    assert client.get(f"/api/check-plots/{batch_id}/plots/NOPE/timelapse").status_code == 404
    assert client.get("/api/check-plots/nope/plots/PT/timelapse").status_code == 404
