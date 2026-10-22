"""Fetch one real Sentinel-2 image behind each demo plot of the home page map. No account needed.

    pip install -r scripts/requirements-maps.txt      # rasterio: this script only, never the app
    python -m scripts.fetch_satellite                 # both plots, the recent scene behind the map
    python -m scripts.fetch_satellite --plot P-042
    python -m scripts.fetch_satellite --before        # also the 2020 scene, then the Satellite Lens image
    python -m scripts.fetch_satellite --lens          # only rebuild the Lens images from the two JPEGs (offline)
    python -m scripts.fetch_satellite --timelapse     # one dry-season scene a year, 2019 to now (data/timelapse/)

For each plot it asks the public STAC catalogue Earth Search for Sentinel-2 L2A scenes of
2025-2026 over a 1.4 x 1.4 km box around the plot, with less than 20 % cloud over the scene,
least cloudy first. It keeps the first scene whose scene classification (SCL) shows no
cloud, cloud shadow or missing data inside the map frame; if none does, the one with the
least, and says how much in the .json. Then it reads only the pixels it needs from the
scene's true-colour COG ("visual"; else the red, green and blue bands), reprojects them to
EPSG:4326 on the map frame, and writes app/web/static/maps/<plot>.jpg (at most 300 KB)
and <plot>.json (scene, date, cloud, frame, credit, sha256).

The frame is the one the interface draws (app/web/maps.py, _window): 1200 x 800 m around
the plot, north up, on an SVG of 960 x 640. The image has exactly 960 x 640 pixels over that
frame, so pixel (i, j) sits under SVG point (i + 0.5, j + 0.5) and every 30 m cell the page
draws lands on the pixels under it. Credit, printed with the image: "Contains modified
Copernicus Sentinel data <year>" (Copernicus Sentinel data are free and open).

--before asks for the BEFORE scene the same way: the least cloudy clean frame of the 2019-2020
dry season (December to March, before the EU cut-off of 31/12/2020), same frame, same pixel
grid, written as <plot>_2020.jpg and .json. Then it composes the image the Satellite Lens shows
Nemotron Vision (app/lens): before | after side by side, at most 1024 px wide, the same 3 x 3
grid on each half with A1-C3 written small in the corner of each cell, as <plot>_lens.jpg and
.json (both scenes, the layout). One image of two dates: on the recent scene alone a clearing
of 2021 has grown back and there is nothing left to see (first real test, 22/10).

It also cuts nine pairs, one per grid cell (<plot>_lens_A1.jpg to _C3.jpg and
<plot>_lens_cells.json): the cell cropped from each date's frame JPEG and enlarged by bicubic
resampling to 384 x 256 px, nothing else done to the pixels, before | after, under a strip that
reads "A1 2020 | A1 2026". On the whole grid at 512 px per date, Nemotron answered "forest_kept"
nine times out of nine (second real test, 22/10): a cell of 400 x 267 m is 40 x 27 Sentinel-2
pixels, too small to judge at that size. --lens rebuilds all of these offline from the JPEGs.

--timelapse fetches, for each year from 2019 to the current one, the least cloudy clean frame of
that year's dry season (December of the year before to March), same frame, same pixel grid, into
data/timelapse/<plot>_<year>.jpg and .json; a year with no usable scene gets a .json that says so.
app/timelapse.py serves them (GET /api/timelapse), with the Hansen loss of each year in the frame,
and fetches the missing ones on demand in live mode through fetch_scene() below.
"""
import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.world import geo

ROOT = Path(__file__).resolve().parents[1]
MAPS_DIR = ROOT / "app" / "web" / "static" / "maps"
STAC_API = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
DATES = "2025-01-01T00:00:00Z/2026-12-31T23:59:59Z"
MAX_SCENE_CLOUD = 20  # eo:cloud_cover, percent of the whole scene
# The BEFORE scene: the 2019-2020 dry season, before the cut-off. Harmattan haze makes whole
# scenes look cloudier than the frame is, so the scene filter is looser; the SCL of the frame decides.
BEFORE = {"dates": "2019-12-01T00:00:00Z/2020-03-31T23:59:59Z", "max_cloud": 50, "suffix": "_2020",
          "label": "the 2019-2020 dry season"}
AFTER = {"dates": DATES, "max_cloud": MAX_SCENE_CLOUD, "suffix": "", "label": "2025-2026"}
TIMELAPSE_DIR = ROOT / "data" / "timelapse"
TIMELAPSE_FIRST_YEAR = 2019


def dry_season(year: int) -> dict[str, Any]:
    """The dry season that ends in March of `year`: the clearest months over Soubre, as BEFORE is for 2020."""
    return {"dates": f"{year - 1}-12-01T00:00:00Z/{year}-03-31T23:59:59Z", "max_cloud": BEFORE["max_cloud"],
            "suffix": f"_{year}", "label": f"the {year - 1}-{year} dry season", "year": year}
LENS_HALF_PX = (504, 336)  # each date in the Lens image: 3:2 like the frame
LENS_GAP_PX = 16  # between the two dates: 504 + 16 + 504 = 1024, the width the model reads well
INK = (14, 26, 20)  # #0E1A14, the visual system's ink: the gap and the label boxes
SEARCH_SIDE_M = 1400.0  # the box the catalogue is searched on: 1.4 x 1.4 km around the plot
WINDOW_M = (1200.0, 800.0)  # width, height of the map frame (app/web/maps.py, WINDOW_M)
MAP_PX = (960, 640)  # width, height of the map's SVG (app/web/static/app.js, MAP_W x MAP_H)
MAX_JPEG_BYTES = 300_000
MAX_CANDIDATES = 30  # scenes whose SCL is read before settling for the least cloudy
SCL_BAD = {0: "no data", 1: "saturated", 3: "cloud shadow", 8: "cloud", 9: "cloud", 10: "cirrus"}
TIMEOUT_S = 60
GDAL_ENV = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
            "GDAL_HTTP_MAX_RETRY": "3", "GDAL_HTTP_RETRY_DELAY": "2"}

# The two demo plots (Amir's decision of 22/10), as in app/web/maps.py: centre lat, lon.
PLOTS = {"P-042": (5.7300, -6.5220), "P-041": (5.7052, -6.7244)}


class SatelliteError(RuntimeError):
    """Something the person running the script must see and fix: no scene, a refused download."""


# ---- where: the map frame and the search box ---------------------------------------------------

def frame(lat: float, lon: float) -> dict[str, float]:
    """The map frame around a plot, computed exactly as app/web/maps.py _window does."""
    width, height = WINDOW_M
    d_lat = height / 2 / geo.M_PER_DEG_LAT
    d_lon = width / 2 / geo.m_per_deg_lon(lat)
    return {"lat_min": round(lat - d_lat, 6), "lat_max": round(lat + d_lat, 6),
            "lon_min": round(lon - d_lon, 6), "lon_max": round(lon + d_lon, 6),
            "width_m": width, "height_m": height}


def search_bbox(lat: float, lon: float) -> list[float]:
    """[west, south, east, north] of the 1.4 x 1.4 km box the catalogue is searched on."""
    half = SEARCH_SIDE_M / 2
    d_lat, d_lon = half / geo.M_PER_DEG_LAT, half / geo.m_per_deg_lon(lat)
    return [round(lon - d_lon, 6), round(lat - d_lat, 6), round(lon + d_lon, 6), round(lat + d_lat, 6)]


def pixel_transform(box: dict[str, float]) -> tuple[float, float, float, float, float, float]:
    """(a, b, c, d, e, f) of the affine from pixel (col, row) to (lon, lat): the SVG's own scale."""
    width, height = MAP_PX
    return ((box["lon_max"] - box["lon_min"]) / width, 0.0, box["lon_min"],
            0.0, -(box["lat_max"] - box["lat_min"]) / height, box["lat_max"])


# ---- which scene ------------------------------------------------------------------------------------

def search_body(lat: float, lon: float, limit: int = 100, season: Optional[dict] = None) -> dict[str, Any]:
    season = season or AFTER
    return {"collections": [COLLECTION], "bbox": search_bbox(lat, lon), "datetime": season["dates"],
            "query": {"eo:cloud_cover": {"lt": season["max_cloud"]}},
            "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}], "limit": limit}


def search(lat: float, lon: float, session: Any = None, season: Optional[dict] = None) -> list[dict]:
    """Every matching scene, following the catalogue's "next" links, least cloudy first."""
    import requests

    season = season or AFTER
    session = session or requests.Session()
    url, body, items = f"{STAC_API}/search", search_body(lat, lon, season=season), []
    while url:
        response = session.post(url, json=body, timeout=TIMEOUT_S)
        if response.status_code != 200:
            raise SatelliteError(f"{url} answered HTTP {response.status_code}: {response.text[:200]}")
        page = response.json()
        items += page.get("features", [])
        following = next((link for link in page.get("links", []) if link.get("rel") == "next"), None)
        url, body = (following["href"], following.get("body", body)) if following else (None, None)
    return ordered(items, season["max_cloud"])


def ordered(items: list[dict], max_cloud: float = MAX_SCENE_CLOUD) -> list[dict]:
    """Least cloudy scene first, then the most recent; the catalogue's order is not trusted."""
    kept = [i for i in items if (i.get("properties", {}).get("eo:cloud_cover") or 0) < max_cloud]
    by_date = sorted(kept, key=lambda i: i["properties"]["datetime"], reverse=True)
    return sorted(by_date, key=lambda i: i["properties"]["eo:cloud_cover"])


def bad_fraction(scl: Any) -> float:
    """Share of the frame's SCL pixels that are cloud, cloud shadow, cirrus, saturated or missing."""
    import numpy as np

    values = np.asarray(scl)
    return float(np.isin(values, list(SCL_BAD)).sum()) / values.size if values.size else 1.0


def rgb_hrefs(item: dict) -> tuple[str, list[str]]:
    """("visual", [href]) for the true-colour COG, else ("red-green-blue", [three hrefs])."""
    assets = item.get("assets", {})
    if "visual" in assets:
        return "visual", [assets["visual"]["href"]]
    if all(band in assets for band in ("red", "green", "blue")):
        return "red-green-blue", [assets[band]["href"] for band in ("red", "green", "blue")]
    raise SatelliteError(f"{item.get('id')}: no visual asset and no red/green/blue bands")


def pick(items: list[dict], scl_of: Any, season: Optional[dict] = None) -> tuple[dict, Optional[float]]:
    """The first scene with a clean frame; else the least bad of the first MAX_CANDIDATES."""
    best: tuple[Optional[dict], float] = (None, 2.0)
    for item in items[:MAX_CANDIDATES]:
        if "scl" not in item.get("assets", {}):
            fraction = None
        else:
            fraction = bad_fraction(scl_of(item["assets"]["scl"]["href"]))
            if fraction == 0.0:
                return item, 0.0
        score = 1.0 if fraction is None else fraction
        if score < best[1]:
            best = (item, score)
    if best[0] is None:
        season = season or AFTER
        raise SatelliteError(f"no Sentinel-2 L2A scene of {season['label']} under {season['max_cloud']} % cloud "
                             "covers this frame")
    item = best[0]
    return item, (None if "scl" not in item.get("assets", {}) else best[1])


# ---- reading only the frame, on the frame's own grid ---------------------------------------------------

def read_on_frame(href: str, box: dict[str, float], bands: Optional[list[int]] = None,
                  nearest: bool = False) -> Any:
    """The COG's pixels resampled onto the frame's 960 x 640 EPSG:4326 grid; only the window needed is read."""
    import numpy as np
    import rasterio
    from rasterio.transform import Affine
    from rasterio.warp import Resampling, reproject, transform_bounds
    from rasterio.windows import Window, from_bounds

    width, height = MAP_PX
    destination = Affine(*pixel_transform(box))
    with rasterio.Env(**GDAL_ENV), rasterio.open(href) as src:
        west, south, east, north = transform_bounds("EPSG:4326", src.crs, box["lon_min"], box["lat_min"],
                                                    box["lon_max"], box["lat_max"], densify_pts=21)
        margin = 3 * max(abs(src.res[0]), abs(src.res[1]))
        exact = from_bounds(west - margin, south - margin, east + margin, north + margin, src.transform)
        col, row = math.floor(exact.col_off), math.floor(exact.row_off)
        window = Window(col, row, math.ceil(exact.col_off + exact.width) - col,
                        math.ceil(exact.row_off + exact.height) - row)
        indexes = bands or list(range(1, src.count + 1))
        data = src.read(indexes, window=window, boundless=True, fill_value=0)
        out = np.zeros((len(indexes), height, width), dtype=data.dtype)
        reproject(data, out, src_transform=src.window_transform(window), src_crs=src.crs, src_nodata=0,
                  dst_transform=destination, dst_crs="EPSG:4326", dst_nodata=0,
                  resampling=Resampling.nearest if nearest else Resampling.bilinear)
    return out


def stretch(rgb: Any) -> Any:
    """Reflectance bands (uint16) to display bytes: 2 % - 98 % of the frame, the same for the three bands."""
    import numpy as np

    values = rgb[rgb > 0]
    if not values.size:
        raise SatelliteError("the frame holds no data in these bands")
    low, high = np.percentile(values, [2, 98])
    return (np.clip((rgb.astype("float64") - low) / max(high - low, 1.0), 0, 1) * 255).round().astype("uint8")


def jpeg(rgb: Any, max_bytes: int = MAX_JPEG_BYTES) -> tuple[bytes, int]:
    """(bytes, quality): the best JPEG quality from 90 down that stays under max_bytes."""
    import warnings

    from rasterio.errors import NotGeoreferencedWarning
    from rasterio.io import MemoryFile

    count, height, width = rgb.shape
    for quality in range(90, 39, -5):
        with warnings.catch_warnings(), MemoryFile() as memory:  # the frame lives in the .json, not the JPEG
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
            with memory.open(driver="JPEG", width=width, height=height, count=count, dtype="uint8",
                             QUALITY=str(quality)) as image:
                image.write(rgb)
            data = memory.read()
        if len(data) <= max_bytes:
            return data, quality
    raise SatelliteError(f"no JPEG quality down to 40 fits in {max_bytes} bytes")


# ---- the files -------------------------------------------------------------------------------------------

def credit(item: dict) -> str:
    return f"Contains modified Copernicus Sentinel data {item['properties']['datetime'][:4]}"


def description(plot_id: str, item: dict, box: dict, asset: str, hrefs: list[str], fraction: Optional[float],
                data: bytes, quality: int, season: Optional[dict] = None,
                centre: Optional[tuple[float, float]] = None) -> dict[str, Any]:
    properties = item["properties"]
    season = season or AFTER
    centre = centre or PLOTS[plot_id]
    return {
        "plot_id": plot_id,
        "role": "before" if season is BEFORE else "timelapse" if "year" in season else "after",
        "season": season["label"],
        "scene_id": item["id"],
        "datetime": properties["datetime"],
        "date": properties["datetime"][:10],
        "cloud_cover_scene_percent": properties["eo:cloud_cover"],
        "frame_bad_pixels_share": fraction,  # SCL: cloud, shadow, cirrus, saturated or no data; None: no SCL
        "frame": box,
        "crs": "EPSG:4326",
        "pixels": list(MAP_PX),
        "pixel_deg": [pixel_transform(box)[0], -pixel_transform(box)[4]],
        "asset": asset,
        "sources": hrefs,
        "catalogue": {"stac_api": STAC_API, "collection": COLLECTION,
                      "search": search_body(*centre, season=season)},
        "licence": credit(item),
        "licence_terms": "Copernicus Sentinel data: free, full and open access (Copernicus data policy, "
                         "Commission Delegated Regulation (EU) No 1159/2013)",
        "jpeg_quality": quality,
        "jpg_bytes": len(data),
        "jpg_sha256": hashlib.sha256(data).hexdigest(),
        "made_by": "scripts/fetch_satellite.py",
        "made_on": datetime.now(timezone.utc).date().isoformat(),
    }


def fetch(plot_id: str, out_dir: Path = MAPS_DIR, season: Optional[dict] = None) -> dict[str, Any]:
    season = season or AFTER
    return fetch_scene(plot_id, *PLOTS[plot_id], season, out_dir, f"{plot_id}{season['suffix']}")


def fetch_scene(key: str, lat: float, lon: float, season: dict, out_dir: Path, name: str,
                quiet: bool = False, box: Optional[dict] = None) -> dict[str, Any]:
    """The best scene of a season on a frame (the map's around (lat, lon) by default), as <out_dir>/<name>.jpg."""
    plot_id = key
    box = box or frame(lat, lon)
    items = search(lat, lon, season=season)
    if not quiet:
        print(f"{plot_id}: {len(items)} scene(s) of {season['label']} under {season['max_cloud']} % cloud")
    item, fraction = pick(items, lambda href: read_on_frame(href, box, nearest=True)[0], season)
    asset, hrefs = rgb_hrefs(item)
    if asset == "visual":
        rgb = read_on_frame(hrefs[0], box, bands=[1, 2, 3])
    else:
        import numpy as np

        rgb = stretch(np.concatenate([read_on_frame(href, box, bands=[1]) for href in hrefs]))
    data, quality = jpeg(rgb)
    info = description(plot_id, item, box, asset, hrefs, fraction, data, quality, season, (lat, lon))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.jpg").write_bytes(data)
    (out_dir / f"{name}.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8", newline="\n")
    if not quiet:
        print(f"{plot_id}: {item['id']} of {info['date']}, scene cloud {info['cloud_cover_scene_percent']} %, "
              f"frame {'not checked (no SCL)' if fraction is None else f'{fraction:.1%} bad pixels'}, "
              f"{len(data) / 1000:.0f} KB, {info['licence']}")
    return info


# ---- the Satellite Lens image: before | after, one grid, labels in the corners ---------------------------

# 5 x 7 glyphs, drawn pixel by pixel: the characters the grid and the years need, no font file.
GLYPHS = {
    " ": (".....",) * 7,
    "0": (".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": ("..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."),
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": ("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": (".####", "#....", "#....", "#....", "#....", "#....", ".####"),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": ("####.", "....#", "....#", ".###.", "....#", "....#", "####."),
}
LABEL_SCALE = 2  # each glyph pixel is 2 x 2: a 10 x 14 px character, small in a 168 x 112 px cell
LABEL_PAD = 2
GRID_LINE = (231, 238, 233)  # the visual system's light text colour, 1 px


def label_box(text: str) -> Any:
    """A small ink box with the text in light pixels: (3, h, w) uint8."""
    import numpy as np

    width = len(text) * 5 * LABEL_SCALE + (len(text) - 1) * LABEL_SCALE + 2 * LABEL_PAD
    height = 7 * LABEL_SCALE + 2 * LABEL_PAD
    box = np.zeros((3, height, width), dtype="uint8")
    box[:] = np.array(INK, dtype="uint8")[:, None, None]
    for index, char in enumerate(text):
        left = LABEL_PAD + index * 6 * LABEL_SCALE
        for row, line in enumerate(GLYPHS[char]):
            for col, mark in enumerate(line):
                if mark == "#":
                    y, x = LABEL_PAD + row * LABEL_SCALE, left + col * LABEL_SCALE
                    box[:, y:y + LABEL_SCALE, x:x + LABEL_SCALE] = np.array(GRID_LINE, dtype="uint8")[:, None, None]
    return box


def with_grid(half: Any) -> Any:
    """One date: a 1 px 3 x 3 grid, and the cell's name in its top-left corner (A1 top-left, C3 bottom-right)."""
    import numpy as np

    out = half.copy()
    _, height, width = out.shape
    line = np.array(GRID_LINE, dtype="uint8")[:, None]
    for k in (1, 2):
        out[:, :, round(k * width / 3)] = line
        out[:, round(k * height / 3), :] = line
    for r, row in enumerate("123"):
        for c, column in enumerate("ABC"):
            box = label_box(f"{column}{row}")
            y, x = round(r * height / 3) + 2, round(c * width / 3) + 2
            out[:, y:y + box.shape[1], x:x + box.shape[2]] = box
    return out


def compose(before: Any, after: Any) -> Any:
    """before | after, each LENS_HALF_PX with its grid, an ink gap between: (3, 336, 1024) uint8."""
    import numpy as np

    width, height = LENS_HALF_PX
    for name, half in (("before", before), ("after", after)):
        if half.shape != (3, height, width):
            raise SatelliteError(f"the {name} half is {half.shape}, expected (3, {height}, {width})")
    gap = np.zeros((3, height, LENS_GAP_PX), dtype="uint8")
    gap[:] = np.array(INK, dtype="uint8")[:, None, None]
    return np.concatenate([with_grid(before), gap, with_grid(after)], axis=2)


def read_half(path: Path) -> Any:
    """A 960 x 640 frame JPEG, averaged down to LENS_HALF_PX: same frame, so the grids line up."""
    import rasterio
    from rasterio.enums import Resampling

    width, height = LENS_HALF_PX
    with rasterio.Env(), rasterio.open(path) as src:
        return src.read([1, 2, 3], out_shape=(3, height, width), resampling=Resampling.average)


def lens_image(plot_id: str, out_dir: Path = MAPS_DIR) -> dict[str, Any]:
    """<plot>_lens.jpg and .json from <plot>_2020.jpg and <plot>.jpg (both fetched by this script)."""
    scenes = {}
    for role, suffix in (("before", BEFORE["suffix"]), ("after", AFTER["suffix"])):
        image, meta = out_dir / f"{plot_id}{suffix}.jpg", out_dir / f"{plot_id}{suffix}.json"
        if not (image.is_file() and meta.is_file()):
            raise SatelliteError(f"{image.name} is missing: run python -m scripts.fetch_satellite"
                                 f"{' --before' if role == 'before' else ''} --plot {plot_id}")
        scenes[role] = (image, json.loads(meta.read_text(encoding="utf-8")))
    data, quality = jpeg(compose(read_half(scenes["before"][0]), read_half(scenes["after"][0])))
    width, height = LENS_HALF_PX
    info = {
        "plot_id": plot_id,
        "before": {k: scenes["before"][1].get(k) for k in ("scene_id", "date", "licence", "jpg_sha256")},
        "after": {k: scenes["after"][1].get(k) for k in ("scene_id", "date", "licence", "jpg_sha256")},
        "frame": scenes["after"][1]["frame"],
        "layout": {"pixels": [2 * width + LENS_GAP_PX, height], "half": [width, height], "gap": LENS_GAP_PX,
                   "left": "before", "right": "after",
                   "grid": "3 x 3 on each half: columns A-C west to east, rows 1-3 north to south",
                   "labels": "cell name, small, in the top-left corner of each cell"},
        "jpeg_quality": quality,
        "jpg_bytes": len(data),
        "jpg_sha256": hashlib.sha256(data).hexdigest(),
        "made_by": "scripts/fetch_satellite.py",
        "made_on": datetime.now(timezone.utc).date().isoformat(),
    }
    (out_dir / f"{plot_id}_lens.jpg").write_bytes(data)
    (out_dir / f"{plot_id}_lens.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{plot_id}: Lens image {info['layout']['pixels'][0]} x {height} px, {info['before']['date']} | "
          f"{info['after']['date']}, {len(data) / 1000:.0f} KB")
    return info


# ---- one pair per cell: the cell cropped at both dates, enlarged, side by side ------------------------------

CELL_HALF_PX = (384, 256)  # each date of one cell: 3:2 like the cell, 1.2 x the frame JPEG's own pixels
CELL_STRIP_PX = 20  # the label strip above the pair: "A1 2020" over the left, "A1 2026" over the right
CELL_NAMES = tuple(f"{c}{r}" for r in "123" for c in "ABC")


def read_cell(path: Path, cell: str) -> Any:
    """One ninth of a frame JPEG, enlarged to CELL_HALF_PX by bicubic resampling and nothing else."""
    import warnings

    import rasterio
    from rasterio.enums import Resampling
    from rasterio.errors import NotGeoreferencedWarning
    from rasterio.windows import Window

    width, height = CELL_HALF_PX
    column, row = "ABC".index(cell[0]), "123".index(cell[1])
    with warnings.catch_warnings(), rasterio.Env(), rasterio.open(path) as src:
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        window = Window(column * src.width / 3, row * src.height / 3, src.width / 3, src.height / 3)
        return src.read([1, 2, 3], window=window, out_shape=(3, height, width), resampling=Resampling.cubic)


def strip(text: str, width: int) -> Any:
    """An ink strip CELL_STRIP_PX high with the text at its left."""
    import numpy as np

    out = np.zeros((3, CELL_STRIP_PX, width), dtype="uint8")
    out[:] = np.array(INK, dtype="uint8")[:, None, None]
    label = label_box(text)
    top = (CELL_STRIP_PX - label.shape[1]) // 2
    out[:, top:top + label.shape[1], :label.shape[2]] = label
    return out


def cell_pair(before: Any, after: Any, cell: str, before_year: str, after_year: str) -> Any:
    """One cell at both dates: the strip of names, then before | after, (3, 276, 784) uint8."""
    import numpy as np

    width, height = CELL_HALF_PX
    for name, half in (("before", before), ("after", after)):
        if half.shape != (3, height, width):
            raise SatelliteError(f"the {name} crop of {cell} is {half.shape}, expected (3, {height}, {width})")
    gap = np.zeros((3, height + CELL_STRIP_PX, LENS_GAP_PX), dtype="uint8")
    gap[:] = np.array(INK, dtype="uint8")[:, None, None]
    left = np.concatenate([strip(f"{cell} {before_year}", width), before], axis=1)
    right = np.concatenate([strip(f"{cell} {after_year}", width), after], axis=1)
    return np.concatenate([left, gap, right], axis=2)


def lens_cells(plot_id: str, out_dir: Path = MAPS_DIR) -> dict[str, Any]:
    """<plot>_lens_A1.jpg ... _C3.jpg and <plot>_lens_cells.json, from the two frame JPEGs, offline."""
    scenes = {}
    for role, suffix in (("before", BEFORE["suffix"]), ("after", AFTER["suffix"])):
        image, meta = out_dir / f"{plot_id}{suffix}.jpg", out_dir / f"{plot_id}{suffix}.json"
        if not (image.is_file() and meta.is_file()):
            raise SatelliteError(f"{image.name} is missing: run python -m scripts.fetch_satellite"
                                 f"{' --before' if role == 'before' else ''} --plot {plot_id}")
        scenes[role] = (image, json.loads(meta.read_text(encoding="utf-8")))
    years = {role: scenes[role][1]["date"][:4] for role in scenes}
    files = {}
    for cell in CELL_NAMES:
        pair = cell_pair(read_cell(scenes["before"][0], cell), read_cell(scenes["after"][0], cell),
                         cell, years["before"], years["after"])
        data, quality = jpeg(pair)
        name = f"{plot_id}_lens_{cell}.jpg"
        (out_dir / name).write_bytes(data)
        files[cell] = {"file": name, "jpg_bytes": len(data), "jpg_sha256": hashlib.sha256(data).hexdigest(),
                       "jpeg_quality": quality}
    width, height = CELL_HALF_PX
    info = {
        "plot_id": plot_id,
        "before": {k: scenes["before"][1].get(k) for k in ("scene_id", "date", "licence", "jpg_sha256")},
        "after": {k: scenes["after"][1].get(k) for k in ("scene_id", "date", "licence", "jpg_sha256")},
        "frame": scenes["after"][1]["frame"],
        "layout": {"pixels": [2 * width + LENS_GAP_PX, height + CELL_STRIP_PX], "half": [width, height],
                   "strip": CELL_STRIP_PX, "left": "before", "right": "after",
                   "crop": "one ninth of each date's frame JPEG (columns A-C west to east, rows 1-3 north to south)",
                   "resampling": "bicubic, enlargement only: no sharpening, no contrast or colour change"},
        "cells": files,
        "made_by": "scripts/fetch_satellite.py",
        "made_on": datetime.now(timezone.utc).date().isoformat(),
    }
    (out_dir / f"{plot_id}_lens_cells.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8",
                                                        newline="\n")
    print(f"{plot_id}: 9 cell pairs {info['layout']['pixels'][0]} x {info['layout']['pixels'][1]} px, "
          f"{info['before']['date']} | {info['after']['date']}")
    return info


def timelapse(plot_id: str, out_dir: Path = TIMELAPSE_DIR, last_year: Optional[int] = None) -> list[dict]:
    """One scene a year; a year with no usable scene is written down as such, never skipped in silence."""
    done = []
    for year in range(TIMELAPSE_FIRST_YEAR, (last_year or datetime.now(timezone.utc).year) + 1):
        name = f"{plot_id}_{year}"
        try:
            done.append(fetch(plot_id, out_dir, dry_season(year)))
        except SatelliteError as error:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{name}.jpg").unlink(missing_ok=True)
            note = {"plot_id": plot_id, "year": year, "season": dry_season(year)["label"], "image": None,
                    "status": f"no usable scene: {error}", "made_by": "scripts/fetch_satellite.py",
                    "made_on": datetime.now(timezone.utc).date().isoformat()}
            (out_dir / f"{name}.json").write_text(json.dumps(note, indent=2) + "\n", encoding="utf-8", newline="\n")
            print(f"{plot_id} {year}: {note['status']}", file=sys.stderr)
            done.append(note)
    return done


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plot", choices=sorted(PLOTS), action="append", help="one plot (default: both)")
    parser.add_argument("--before", action="store_true", help="also fetch the 2020 scene, then build the Lens image")
    parser.add_argument("--lens", action="store_true", help="only build the Lens images from the two JPEGs")
    parser.add_argument("--timelapse", action="store_true", help="one dry-season scene a year, 2019 to now")
    args = parser.parse_args(argv)
    for plot_id in args.plot or list(PLOTS):
        if args.timelapse:
            timelapse(plot_id)
            continue
        try:
            if args.before:
                fetch(plot_id, season=BEFORE)  # the recent scene is already there: --before adds 2020 only
            elif not args.lens:
                fetch(plot_id)
            if args.before or args.lens:
                lens_image(plot_id)
                lens_cells(plot_id)
        except SatelliteError as error:
            print(f"{plot_id}: error: {error}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
