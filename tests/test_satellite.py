"""The satellite backgrounds: where they sit and how a scene is chosen. No network here, ever.

The script itself runs on a machine that reaches Earth Search (python -m scripts.fetch_satellite);
these tests check the geometry against the interface's frame, the pixel-to-cell alignment (on a
GeoTIFF made here, in UTM like Sentinel-2), the scene choice and the files' description.
"""
import hashlib
import json

import pytest

from app.checks.landcover import CELL_DEG, GRID_ORIGIN, load_meta
from app.world import geo
from scripts import fetch_satellite as sat

# The frames the interface publishes (app/web/static/maps/README.md, from app/web/maps.py _window).
INTERFACE_FRAMES = {
    "P-042": {"lat_min": 5.726383, "lat_max": 5.733617, "lon_min": -6.527417, "lon_max": -6.516583},
    "P-041": {"lat_min": 5.701583, "lat_max": 5.708817, "lon_min": -6.729817, "lon_max": -6.718983},
}


def box_of(plot_id):
    return sat.frame(*sat.PLOTS[plot_id])


# ---- the frame: the interface's, inside the maps, inside the searched box --------------------------

@pytest.mark.parametrize("plot_id", sorted(sat.PLOTS))
def test_the_frame_is_the_one_the_interface_draws(plot_id):
    box = box_of(plot_id)
    assert {k: box[k] for k in INTERFACE_FRAMES[plot_id]} == INTERFACE_FRAMES[plot_id]
    assert (box["width_m"], box["height_m"]) == (1200.0, 800.0)


@pytest.mark.parametrize("plot_id", sorted(sat.PLOTS))
def test_same_frame_as_the_interface_code_when_it_is_there(plot_id):
    maps = pytest.importorskip("app.web.maps", reason="app/web is on the interface branch")
    assert maps._window(maps.PLOTS[plot_id]) == box_of(plot_id)


@pytest.mark.parametrize("plot_id", sorted(sat.PLOTS))
def test_the_frame_lies_in_the_reference_maps_and_in_the_searched_box(plot_id):
    box, extent = box_of(plot_id), load_meta()["extent"]
    assert extent["lat_min"] <= box["lat_min"] and box["lat_max"] <= extent["lat_max"]
    assert extent["lon_min"] <= box["lon_min"] and box["lon_max"] <= extent["lon_max"]
    west, south, east, north = sat.search_bbox(*sat.PLOTS[plot_id])
    assert west < box["lon_min"] and box["lon_max"] < east and south < box["lat_min"] and box["lat_max"] < north
    lat, lon = sat.PLOTS[plot_id]
    assert geo.distance_m(south, lon, north, lon) == pytest.approx(1400, abs=1)
    assert geo.distance_m(lat, west, lat, east) == pytest.approx(1400, abs=1)


# ---- the pixels: one per SVG unit, so the cells fall on them -------------------------------------------

def svg_xy(box, lat, lon):
    """app/web/static/app.js renderMap: x(lon), y(lat) on the 960 x 640 SVG."""
    width, height = sat.MAP_PX
    return ((lon - box["lon_min"]) / (box["lon_max"] - box["lon_min"]) * width,
            (box["lat_max"] - lat) / (box["lat_max"] - box["lat_min"]) * height)


def cell_corners(box):
    first_row = int((GRID_ORIGIN[0] - box["lat_max"]) / CELL_DEG) + 1
    first_col = int((box["lon_min"] - GRID_ORIGIN[1]) / CELL_DEG) + 1
    rows = range(first_row, first_row + int((box["lat_max"] - box["lat_min"]) / CELL_DEG))
    cols = range(first_col, first_col + int((box["lon_max"] - box["lon_min"]) / CELL_DEG))
    return [(GRID_ORIGIN[0] - r * CELL_DEG, GRID_ORIGIN[1] + c * CELL_DEG) for r in rows for c in cols]


@pytest.mark.parametrize("plot_id", sorted(sat.PLOTS))
def test_every_cell_corner_is_at_the_same_place_in_the_image_and_on_the_svg(plot_id):
    box = box_of(plot_id)
    a, _, c, _, e, f = sat.pixel_transform(box)
    corners = cell_corners(box)
    assert len(corners) > 1000
    for lat, lon in corners:
        x, y = svg_xy(box, lat, lon)
        assert (lon - c) / a == pytest.approx(x, abs=1e-6) and (lat - f) / e == pytest.approx(y, abs=1e-6)


@pytest.mark.parametrize("plot_id", sorted(sat.PLOTS))
def test_a_pixel_is_square_on_the_ground_and_finer_than_a_cell(plot_id):
    box = box_of(plot_id)
    a, _, _, _, e, _ = sat.pixel_transform(box)
    east_m, north_m = a * geo.m_per_deg_lon(sat.PLOTS[plot_id][0]), -e * geo.M_PER_DEG_LAT
    assert east_m == pytest.approx(1.25, abs=0.001) and north_m == pytest.approx(1.25, abs=0.001)
    assert CELL_DEG / a > 20  # about 22 pixels across one 30 m cell


def test_a_utm_raster_lands_on_the_frame_where_the_svg_draws_it(tmp_path):
    """A 10 m raster in EPSG:32629 (Sentinel-2's zone here) with one bright 30 m cell, read as the script does."""
    rasterio = pytest.importorskip("rasterio")
    np = pytest.importorskip("numpy")
    from rasterio.transform import from_origin
    from rasterio.warp import transform

    box = box_of("P-042")
    lat, lon = sat.PLOTS["P-042"]
    row, col = int((GRID_ORIGIN[0] - lat) / CELL_DEG), int((lon - GRID_ORIGIN[1]) / CELL_DEG)
    north, west = GRID_ORIGIN[0] - row * CELL_DEG, GRID_ORIGIN[1] + col * CELL_DEG  # the cell under the plot centre

    (x0,), (y0,) = transform("EPSG:4326", "EPSG:32629", [box["lon_min"] - 0.002], [box["lat_max"] + 0.002])
    origin_x, origin_y = 10 * np.floor(x0 / 10), 10 * np.ceil(y0 / 10)
    size = 160
    xs, ys = np.meshgrid(origin_x + 10 * np.arange(size) + 5, origin_y - 10 * np.arange(size) - 5)
    lons, lats = transform("EPSG:32629", "EPSG:4326", xs.ravel(), ys.ravel())
    lons, lats = np.array(lons).reshape(xs.shape), np.array(lats).reshape(xs.shape)
    bright = (lons > west) & (lons < west + CELL_DEG) & (lats < north) & (lats > north - CELL_DEG)
    data = np.where(bright, 255, 40).astype("uint8")
    path = tmp_path / "scene.tif"
    with rasterio.open(path, "w", driver="GTiff", width=size, height=size, count=3, dtype="uint8",
                       crs="EPSG:32629", transform=from_origin(origin_x, origin_y, 10, 10), nodata=0) as dst:
        dst.write(np.stack([data] * 3))

    out = sat.read_on_frame(str(path), box, bands=[1, 2, 3], nearest=True)
    assert out.shape == (3, 640, 960)
    hit_rows, hit_cols = np.nonzero(out[0] == 255)
    left, top = svg_xy(box, north, west)
    right, bottom = svg_xy(box, north - CELL_DEG, west + CELL_DEG)
    # 10 m source pixels whose centre is in the cell: they land inside the cell's SVG square, within one source pixel.
    tolerance = 10 / 1.25 + 1
    assert hit_cols.min() >= left - tolerance and hit_cols.max() <= right + tolerance
    assert hit_rows.min() >= top - tolerance and hit_rows.max() <= bottom + tolerance
    assert (hit_cols.mean() - (left + right) / 2) ** 2 + (hit_rows.mean() - (top + bottom) / 2) ** 2 < 4 ** 2


# ---- the scene: least cloudy, clean frame first -------------------------------------------------------------

def item(scene_id, cloud, date="2025-12-20T10:50:00Z", scl=True, visual=True):
    assets = {"scl": {"href": f"{scene_id}/SCL.tif"}} if scl else {}
    if visual:
        assets["visual"] = {"href": f"{scene_id}/TCI.tif"}
    else:
        assets.update({band: {"href": f"{scene_id}/{band}.tif"} for band in ("red", "green", "blue")})
    return {"id": scene_id, "properties": {"eo:cloud_cover": cloud, "datetime": date}, "assets": assets}


def test_the_search_asks_for_sentinel_2_l2a_of_2025_2026_under_20_percent():
    body = sat.search_body(*sat.PLOTS["P-041"])
    assert body["collections"] == ["sentinel-2-l2a"]
    assert body["datetime"] == "2025-01-01T00:00:00Z/2026-12-31T23:59:59Z"
    assert body["query"] == {"eo:cloud_cover": {"lt": 20}}
    assert body["bbox"] == sat.search_bbox(*sat.PLOTS["P-041"])


def test_scenes_are_ordered_least_cloudy_then_newest_and_cloudy_ones_dropped():
    items = [item("a", 5.0, "2025-02-01T00:00:00Z"), item("b", 1.0), item("c", 5.0, "2026-01-01T00:00:00Z"),
             item("d", 25.0)]
    assert [i["id"] for i in sat.ordered(items)] == ["b", "c", "a"]


def test_the_first_clean_frame_wins_even_behind_a_less_cloudy_scene():
    np = pytest.importorskip("numpy")
    scl = {"a/SCL.tif": np.array([[4, 9], [4, 4]]), "b/SCL.tif": np.array([[4, 5], [6, 4]])}
    chosen, fraction = sat.pick([item("a", 1.0), item("b", 2.0)], scl.__getitem__)
    assert (chosen["id"], fraction) == ("b", 0.0)


def test_with_no_clean_frame_the_least_bad_is_kept_and_said():
    np = pytest.importorskip("numpy")
    scl = {"a/SCL.tif": np.array([[8, 9], [4, 4]]), "b/SCL.tif": np.array([[0, 4], [4, 4]])}
    chosen, fraction = sat.pick([item("a", 1.0), item("b", 2.0)], scl.__getitem__)
    assert (chosen["id"], fraction) == ("b", 0.25)
    chosen, fraction = sat.pick([item("n", 1.0, scl=False)], scl.__getitem__)
    assert (chosen["id"], fraction) == ("n", None)
    with pytest.raises(sat.SatelliteError, match="no Sentinel-2"):
        sat.pick([], scl.__getitem__)


def test_bad_pixels_are_cloud_shadow_cirrus_saturated_or_missing():
    np = pytest.importorskip("numpy")
    assert sat.bad_fraction(np.array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11])) == pytest.approx(6 / 12)


def test_true_colour_first_else_the_three_bands():
    assert sat.rgb_hrefs(item("a", 1.0)) == ("visual", ["a/TCI.tif"])
    assert sat.rgb_hrefs(item("b", 1.0, visual=False)) == ("red-green-blue", ["b/red.tif", "b/green.tif", "b/blue.tif"])


def test_search_follows_next_links_without_trusting_their_order():
    class Response:
        status_code = 200

        def __init__(self, page):
            self.page = page

        def json(self):
            return self.page

    class Session:
        def __init__(self):
            self.calls = []

        def post(self, url, json, timeout):
            self.calls.append(url)
            if len(self.calls) == 1:
                return Response({"features": [item("x", 9.0)],
                                 "links": [{"rel": "next", "href": "page-2", "body": {"next": "t"}}]})
            return Response({"features": [item("y", 3.0)], "links": []})

    session = Session()
    assert [i["id"] for i in sat.search(5.73, -6.522, session)] == ["y", "x"]
    assert session.calls == [f"{sat.STAC_API}/search", "page-2"]


# ---- the files -----------------------------------------------------------------------------------------

def test_the_jpeg_stays_under_300_kb_even_for_noise():
    np = pytest.importorskip("numpy")
    pytest.importorskip("rasterio")
    noise = np.random.default_rng(7).integers(0, 256, (3, 640, 960), dtype="uint8")
    data, quality = sat.jpeg(noise)
    assert len(data) <= sat.MAX_JPEG_BYTES and data[:2] == b"\xff\xd8" and quality < 90
    with pytest.raises(sat.SatelliteError):
        sat.jpeg(noise, max_bytes=1000)


def test_the_description_carries_the_credit_the_frame_and_the_hash():
    box = box_of("P-042")
    info = sat.description("P-042", item("S2B_29NQG_20251220_0_L2A", 0.4), box, "visual", ["u"], 0.0, b"jpeg", 90)
    assert info["licence"] == "Contains modified Copernicus Sentinel data 2025"
    assert info["jpg_sha256"] == hashlib.sha256(b"jpeg").hexdigest()
    assert (info["frame"], info["crs"], info["pixels"]) == (box, "EPSG:4326", [960, 640])
    assert (info["scene_id"], info["date"], info["cloud_cover_scene_percent"]) == ("S2B_29NQG_20251220_0_L2A",
                                                                                  "2025-12-20", 0.4)
    json.dumps(info)  # written as is


# ---- the 2020 scene and the Satellite Lens image ----------------------------------------------------------

def test_the_before_scene_is_asked_in_the_2019_2020_dry_season_before_the_cut_off():
    body = sat.search_body(*sat.PLOTS["P-042"], season=sat.BEFORE)
    start, end = body["datetime"].split("/")
    assert start.startswith("2019-12-01") and end.startswith("2020-03-31") and end[:10] <= "2020-12-31"
    assert body["bbox"] == sat.search_bbox(*sat.PLOTS["P-042"])  # same place as the recent scene
    assert sat.search_body(*sat.PLOTS["P-042"])["datetime"] == sat.DATES  # the recent scene is unchanged


def test_the_lens_image_is_before_then_after_1024_px_with_one_grid_and_small_labels():
    np = pytest.importorskip("numpy")
    width, height = sat.LENS_HALF_PX
    before = np.full((3, height, width), 40, dtype="uint8")
    after = np.full((3, height, width), 200, dtype="uint8")
    image = sat.compose(before, after)
    assert image.shape == (3, height, 2 * width + sat.LENS_GAP_PX) and image.shape[2] <= 1024
    assert (image[:, height // 2 + 20, 60] == 40).all() and (image[:, height // 2 + 20, -60] == 200).all()  # left 2020
    assert (image[:, 200, width + 4] == sat.INK).all()  # the gap between the dates
    line = np.array(sat.GRID_LINE)
    for left in (0, width + sat.LENS_GAP_PX):  # the same grid on both halves
        assert (image[:, 300, left + round(width / 3)] == line).all() and (image[:, round(height / 3), left + 300] == line).all()
    label = sat.label_box("C3")
    assert label.shape[1] <= 20 and label.shape[2] <= 30  # small: a corner, not the cell
    c3 = image[:, round(2 * height / 3) + 2:round(2 * height / 3) + 2 + label.shape[1],
               round(2 * width / 3) + 2:round(2 * width / 3) + 2 + label.shape[2]]
    assert (c3 == label).all()  # the cell name sits in its top-left corner


def test_a_half_of_the_wrong_size_is_refused():
    np = pytest.importorskip("numpy")
    width, height = sat.LENS_HALF_PX
    with pytest.raises(sat.SatelliteError):
        sat.compose(np.zeros((3, 640, 960), dtype="uint8"), np.zeros((3, height, width), dtype="uint8"))


def test_the_lens_image_needs_both_scenes_and_says_which_command_to_run(tmp_path):
    with pytest.raises(sat.SatelliteError, match="--before"):
        sat.lens_image("P-042", out_dir=tmp_path)


def test_a_cell_pair_is_the_cell_at_both_dates_under_its_names():
    np = pytest.importorskip("numpy")
    width, height = sat.CELL_HALF_PX
    before = np.full((3, height, width), 30, dtype="uint8")
    after = np.full((3, height, width), 220, dtype="uint8")
    pair = sat.cell_pair(before, after, "B2", "2020", "2026")
    assert pair.shape == (3, height + sat.CELL_STRIP_PX, 2 * width + sat.LENS_GAP_PX) and pair.shape[2] <= 1024
    assert (pair[:, sat.CELL_STRIP_PX:, :width] == 30).all() and (pair[:, sat.CELL_STRIP_PX:, -width:] == 220).all()
    left = sat.label_box("B2 2020")
    top = (sat.CELL_STRIP_PX - left.shape[1]) // 2
    assert (pair[:, top:top + left.shape[1], :left.shape[2]] == left).all()  # "B2 2020" over the left
    right = sat.label_box("B2 2026")
    x = width + sat.LENS_GAP_PX
    assert (pair[:, top:top + right.shape[1], x:x + right.shape[2]] == right).all()  # "B2 2026" over the right


def test_a_cell_is_cropped_from_its_ninth_and_only_enlarged(tmp_path):
    np = pytest.importorskip("numpy")
    rasterio = pytest.importorskip("rasterio")
    image = np.zeros((3, 640, 960), dtype="uint8")
    for r in range(3):
        for c in range(3):
            image[:, r * 213 + 20:(r + 1) * 213 - 20, c * 320 + 20:(c + 1) * 320 - 20] = 10 + 30 * (3 * r + c)
    path = tmp_path / "frame.jpg"
    with rasterio.open(path, "w", driver="JPEG", width=960, height=640, count=3, dtype="uint8", QUALITY="100") as out:
        out.write(image)
    for index, cell in enumerate(sat.CELL_NAMES):
        crop = sat.read_cell(path, cell)
        assert crop.shape == (3, *reversed(sat.CELL_HALF_PX))
        centre = crop[:, crop.shape[1] // 2, crop.shape[2] // 2].astype(int)
        assert abs(centre.mean() - (10 + 30 * index)) <= 3  # the middle of A1 comes from A1, of C3 from C3
