"""Fetch one real Sentinel-2 image behind each demo plot of the home page map. No account needed.

    pip install -r scripts/requirements-maps.txt      # rasterio: this script only, never the app
    python -m scripts.fetch_satellite                 # both plots
    python -m scripts.fetch_satellite --plot P-042

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

def search_body(lat: float, lon: float, limit: int = 100) -> dict[str, Any]:
    return {"collections": [COLLECTION], "bbox": search_bbox(lat, lon), "datetime": DATES,
            "query": {"eo:cloud_cover": {"lt": MAX_SCENE_CLOUD}},
            "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}], "limit": limit}


def search(lat: float, lon: float, session: Any = None) -> list[dict]:
    """Every matching scene, following the catalogue's "next" links, least cloudy first."""
    import requests

    session = session or requests.Session()
    url, body, items = f"{STAC_API}/search", search_body(lat, lon), []
    while url:
        response = session.post(url, json=body, timeout=TIMEOUT_S)
        if response.status_code != 200:
            raise SatelliteError(f"{url} answered HTTP {response.status_code}: {response.text[:200]}")
        page = response.json()
        items += page.get("features", [])
        following = next((link for link in page.get("links", []) if link.get("rel") == "next"), None)
        url, body = (following["href"], following.get("body", body)) if following else (None, None)
    return ordered(items)


def ordered(items: list[dict]) -> list[dict]:
    """Least cloudy scene first, then the most recent; the catalogue's order is not trusted."""
    kept = [i for i in items if (i.get("properties", {}).get("eo:cloud_cover") or 0) < MAX_SCENE_CLOUD]
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


def pick(items: list[dict], scl_of: Any) -> tuple[dict, Optional[float]]:
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
        raise SatelliteError("no Sentinel-2 L2A scene of 2025-2026 under 20 % cloud covers this frame")
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
                data: bytes, quality: int) -> dict[str, Any]:
    properties = item["properties"]
    return {
        "plot_id": plot_id,
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
        "catalogue": {"stac_api": STAC_API, "collection": COLLECTION, "search": search_body(*PLOTS[plot_id])},
        "licence": credit(item),
        "licence_terms": "Copernicus Sentinel data: free, full and open access (Copernicus data policy, "
                         "Commission Delegated Regulation (EU) No 1159/2013)",
        "jpeg_quality": quality,
        "jpg_bytes": len(data),
        "jpg_sha256": hashlib.sha256(data).hexdigest(),
        "made_by": "scripts/fetch_satellite.py",
        "made_on": datetime.now(timezone.utc).date().isoformat(),
    }


def fetch(plot_id: str, out_dir: Path = MAPS_DIR) -> dict[str, Any]:
    lat, lon = PLOTS[plot_id]
    box = frame(lat, lon)
    items = search(lat, lon)
    print(f"{plot_id}: {len(items)} scene(s) of 2025-2026 under {MAX_SCENE_CLOUD} % cloud")
    item, fraction = pick(items, lambda href: read_on_frame(href, box, nearest=True)[0])
    asset, hrefs = rgb_hrefs(item)
    if asset == "visual":
        rgb = read_on_frame(hrefs[0], box, bands=[1, 2, 3])
    else:
        import numpy as np

        rgb = stretch(np.concatenate([read_on_frame(href, box, bands=[1]) for href in hrefs]))
    data, quality = jpeg(rgb)
    info = description(plot_id, item, box, asset, hrefs, fraction, data, quality)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{plot_id}.jpg").write_bytes(data)
    (out_dir / f"{plot_id}.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{plot_id}: {item['id']} of {info['date']}, scene cloud {info['cloud_cover_scene_percent']} %, "
          f"frame {'not checked (no SCL)' if fraction is None else f'{fraction:.1%} bad pixels'}, "
          f"{len(data) / 1000:.0f} KB, {info['licence']}")
    return info


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plot", choices=sorted(PLOTS), action="append", help="one plot (default: both)")
    for plot_id in parser.parse_args(argv).plot or list(PLOTS):
        try:
            fetch(plot_id)
        except SatelliteError as error:
            print(f"{plot_id}: error: {error}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
