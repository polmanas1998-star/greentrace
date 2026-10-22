"""The reference-map measure: exact on a hand-made grid, then on the committed cells of Soubre."""
import gzip
import hashlib
import json

import pytest

from app.canonical import canonical_json
from app.checks.landcover import (
    CELL_DEG, CELLS_FILE, META_FILE, NO_DATA, cell_area_ha, cell_box, cell_index, load_meta, measure, plot_box,
    read_cells,
)
from app.world import geo

MAX_BYTES = 5_000_000
P041_WKT = None  # the outline drawn on 18/10: to paste here from Amir's prep files (see the skip reason)

committed = pytest.mark.skipif(not CELLS_FILE.exists(), reason="data/reference_cells.csv.gz not built yet "
                               "(python -m scripts.fetch_reference_maps needs the JRC tile)")


# ---- a hand-made grid: every number below is checked by hand ---------------------------------

TOP, LEFT = 17_200, 13_100  # cell (row, col) of the north-west corner, near 5.70 N, 6.725 W


def centre(row, col):
    return 10.0 - (row + 0.5) * CELL_DEG, -10.0 + (col + 0.5) * CELL_DEG


def grid_cells(rows=10, cols=10, forest=1.0, losses=None):
    """rows x cols cells from (TOP, LEFT); losses: {(row offset, col offset): (year, forest fraction)}."""
    cells = []
    for r in range(rows):
        for c in range(cols):
            year, fraction = (losses or {}).get((r, c), (0, forest))
            lat, lon = centre(TOP + r, LEFT + c)
            cells.append({"lat": round(lat, 6), "lon": round(lon, 6), "forest_fraction_2020": fraction,
                          "loss_year": year})
    return cells


def square(r0, c0, r1, c1):
    """A polygon on cell edges, from cell (r0, c0) to cell (r1, c1) included, offsets from TOP/LEFT."""
    north, west = 10.0 - (TOP + r0) * CELL_DEG, -10.0 + (LEFT + c0) * CELL_DEG
    south, east = 10.0 - (TOP + r1 + 1) * CELL_DEG, -10.0 + (LEFT + c1 + 1) * CELL_DEG
    return {"polygon_wkt": f"POLYGON(({west} {south}, {west} {north}, {east} {north}, {east} {south}, {west} {south}))"}


def ha(*rows):
    return sum(cell_area_ha(centre(TOP + r, LEFT)[0]) for r in rows)


def test_grid_helpers_agree():
    lat, lon = centre(TOP, LEFT)
    assert cell_index(lat, lon) == (TOP, LEFT)
    assert cell_box(TOP, LEFT).centroid.y == pytest.approx(lat) and cell_box(TOP, LEFT).centroid.x == pytest.approx(lon)


def test_a_cell_shrinks_away_from_the_equator():
    assert cell_area_ha(0.0) > cell_area_ha(5.7) > cell_area_ha(10.0)
    assert cell_area_ha(5.7) / cell_area_ha(0.0) == pytest.approx(geo.m_per_deg_lon(5.7) / geo.m_per_deg_lon(0.0))
    assert cell_area_ha(5.7) == pytest.approx(0.0766, abs=0.0005)  # 27.6 m x 27.7 m


def test_loss_is_counted_by_year_and_after_2020_only_on_forest():
    losses = {(0, 0): (2023, 0.9), (1, 0): (2023, 0.4), (2, 0): (2014, 1.0), (3, 0): (2021, 0.5)}
    result = measure(square(0, 0, 3, 0), grid_cells(losses=losses))
    assert result["covered"] is True
    assert result["loss_ha_by_year"] == {"2014": round(ha(2), 4), "2021": round(ha(3), 4), "2023": round(ha(0, 1), 4)}
    assert result["loss_after_2020_ha"] == round(ha(0, 3), 4)  # 0.4 is not forest; 2014 is before the cut-off
    assert result["forest_2020_ha"] == round(0.9 * ha(0) + 0.4 * ha(1) + ha(2) + 0.5 * ha(3), 4)
    assert len(result["cells"]) == 4 and {c["overlap"] for c in result["cells"]} == {1.0}


def test_a_cell_counts_for_the_part_the_plot_covers():
    north, west = 10.0 - TOP * CELL_DEG, -10.0 + LEFT * CELL_DEG
    half = {"polygon_wkt": f"POLYGON(({west} {north - CELL_DEG}, {west} {north}, {west + CELL_DEG / 2} {north}, "
                           f"{west + CELL_DEG / 2} {north - CELL_DEG}, {west} {north - CELL_DEG}))"}
    result = measure(half, grid_cells(losses={(0, 0): (2023, 1.0)}))
    assert result["loss_ha_by_year"] == {"2023": round(ha(0) / 2, 4)}
    assert result["cells"] == [{"lat": round(centre(TOP, LEFT)[0], 6), "lon": round(centre(TOP, LEFT)[1], 6),
                                "overlap": 0.5}]


def test_a_point_plot_is_the_disk_of_its_area():
    lat, lon = centre(TOP + 5, LEFT + 5)
    result = measure({"polygon_wkt": "", "lat": lat, "lon": lon, "area_ha": 2.0}, grid_cells(forest=1.0))
    assert result["forest_2020_ha"] == pytest.approx(2.0, rel=0.01)


def test_outside_or_half_outside_the_maps_is_no_data_never_zero():
    for plot in (square(20, 20, 22, 22), square(8, 8, 12, 12)):  # outside; straddling the edge
        result = measure(plot, grid_cells())
        assert result == {"covered": False, "reason": NO_DATA, "forest_2020_ha": None, "loss_ha_by_year": None,
                          "loss_after_2020_ha": None, "cells": []}


def test_same_plot_same_measure():
    cells = grid_cells(losses={(2, 3): (2023, 1.0)})
    assert canonical_json(measure(square(1, 1, 4, 4), cells)) == canonical_json(measure(square(1, 1, 4, 4), cells))


def test_read_cells_returns_the_rectangle_from_a_file_north_to_south(tmp_path):
    cells = grid_cells(rows=6, cols=4)
    path = tmp_path / "cells.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        handle.write("lat,lon,forest_fraction_2020,loss_year\n")
        for c in cells:
            handle.write(f"{c['lat']:.6f},{c['lon']:.6f},{c['forest_fraction_2020']:.3f},{c['loss_year']}\n")
    lat_max, lat_min = centre(TOP + 1, LEFT)[0], centre(TOP + 3, LEFT)[0]
    lon_min, lon_max = centre(TOP, LEFT + 1)[1], centre(TOP, LEFT + 2)[1]
    found = read_cells(lat_min, lat_max, lon_min, lon_max, path=path)
    assert [(cell_index(c["lat"], c["lon"])) for c in found] == [
        (TOP + r, LEFT + c) for r in (1, 2, 3) for c in (1, 2)]


# ---- the script's arithmetic, on small rasters made here (needs rasterio, the script's only extra) --

def test_jrc_pixels_are_averaged_onto_the_cells_by_overlap():
    rasterio = pytest.importorskip("rasterio")
    np = pytest.importorskip("numpy")
    from scripts.fetch_reference_maps import MapError, resample_forest

    # 2 x 2 cells, each covered by 3 x 3 JRC pixels: cell (0, 0) has 9 forest pixels, (0, 1) has 3, (1, *) none.
    pixels = np.zeros((6, 6), dtype=np.uint8)
    pixels[0:3, 0:3] = 1
    pixels[0, 3:6] = 1
    west, north = -6.725, 5.7
    src = rasterio.Affine(CELL_DEG / 3, 0, west, 0, -CELL_DEG / 3, north)
    dst = rasterio.Affine(CELL_DEG, 0, west, 0, -CELL_DEG, north)
    assert resample_forest(pixels, src, 255, dst, (2, 2)).tolist() == [[1.0, 0.333], [0.0, 0.0]]
    with pytest.raises(MapError, match="unexpected values"):
        resample_forest(np.full((6, 6), 7, dtype=np.uint8), src, 255, dst, (2, 2))
    with pytest.raises(MapError, match="no data"):
        resample_forest(np.full((6, 6), 255, dtype=np.uint8), src, 255, dst, (2, 2))


def test_the_extent_sits_on_the_grid_and_holds_the_demo_places():
    pytest.importorskip("rasterio")
    from scripts.fetch_reference_maps import EXTENT, MapError, check_extent, grid

    check_extent(EXTENT)
    assert grid(EXTENT)[1] == (640, 1120)
    with pytest.raises(MapError, match="video window"):
        check_extent(dict(EXTENT, lon_max=-6.55))
    with pytest.raises(MapError, match="grid"):
        grid(dict(EXTENT, lat_max=5.80001))


def test_the_cells_file_is_the_same_bytes_every_time():
    np = pytest.importorskip("numpy")
    pytest.importorskip("rasterio")
    from scripts.fetch_reference_maps import cells_csv_gz

    extent = {"lat_min": 5.700, "lat_max": 5.7005, "lon_min": -6.725, "lon_max": -6.7245}
    years, fractions = np.array([[2023, 0], [0, 2014]]), np.array([[0.8754, 1.0], [0.0, 0.5]])
    data = cells_csv_gz(extent, fractions, years)
    assert data == cells_csv_gz(extent, fractions, years)
    assert gzip.decompress(data).decode().splitlines() == [
        "lat,lon,forest_fraction_2020,loss_year",
        "5.700375,-6.724875,0.875,2023", "5.700375,-6.724625,1.000,0",
        "5.700125,-6.724875,0.000,0", "5.700125,-6.724625,0.500,2014",
    ]


# ---- the committed cells (skipped, and said so, until they are built) -----------------------------

@committed
def test_the_committed_file_is_small_and_matches_its_meta():
    data = CELLS_FILE.read_bytes()
    meta = load_meta()
    assert len(data) < MAX_BYTES
    assert (meta["bytes"], meta["sha256"]) == (len(data), hashlib.sha256(data).hexdigest())
    assert {s["licence"].split(",")[0].split(" (")[0] for s in meta["sources"]} == {"CC BY 4.0"}


@committed
def test_a_plot_outside_the_maps_says_no_reference_data():
    daloa = {"polygon_wkt": geo.square_wkt(6.80, -6.60, 3.2)}  # the fictional world lies outside Soubre
    assert measure(daloa, read_cells(*plot_box(daloa)))["reason"] == NO_DATA


@committed
def test_the_dam_lake_is_not_counted_as_forest_lost():
    lake = {"polygon_wkt": "", "lat": 5.70533, "lon": -6.55601, "area_ha": 2.0}
    result = measure(lake, read_cells(*plot_box(lake)))
    assert result["covered"] and result["loss_after_2020_ha"] == 0


@committed
@pytest.mark.skipif(P041_WKT is None, reason="P-041's exact outline is not in the repository yet")
def test_p041_lost_about_4_66_ha_in_2023():
    plot = {"polygon_wkt": P041_WKT}
    result = measure(plot, read_cells(*plot_box(plot)))
    # One Hansen cell is 0.077 ha here: two cells of difference is within the method's own noise.
    assert result["loss_ha_by_year"]["2023"] == pytest.approx(4.66, abs=0.15)
    assert canonical_json(result) == canonical_json(measure(plot, read_cells(*plot_box(plot))))


@committed
def test_meta_lists_the_places_the_extent_must_hold():
    assert set(json.loads(META_FILE.read_text(encoding="utf-8"))["contains"]) >= {
        "demo plot P-041 and 2.5 km around it", "video window, 2.4 x 1.4 km"}
