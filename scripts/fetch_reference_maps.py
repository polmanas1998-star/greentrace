"""Fetch two public forest maps around Soubre, and write the cells GreenTrace measures plots with.

    pip install -r scripts/requirements-maps.txt      # rasterio: this script only, never the app
    python -m scripts.fetch_reference_maps            # downloads into data/raw/ (git-ignored), writes data/
    python -m scripts.fetch_reference_maps --check    # rebuilds from data/raw/, compares with the committed file

Both maps are open and need no account:
  JRC GFC2020 v4   forest cover in 2020 at 10 m, the EU's reference map for the EUDR (tile N10_W10)
  Hansen v1.12     year of tree cover loss, 2001-2024, at 1 arc-second, about 30 m (tile 10N_010W)
Each row of data/reference_cells.csv.gz is one cell of the Hansen grid inside EXTENT: its centre,
the share of it the JRC map saw as forest in 2020 (JRC pixels averaged over the cell, each weighted
by the part of it inside the cell) and the year Hansen saw tree cover lost (0: never). The same
tiles always give the same bytes.

5.7053 N, 6.5560 W is the dam lake on the Sassandra: the "loss" there is flooding, never use it
as an example of deforestation.
"""
import argparse
import gzip
import hashlib
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.warp import Resampling, reproject
from rasterio.windows import from_bounds

from app.checks.landcover import CELL_DEG, CELLS_FILE, GRID_ORIGIN, META_FILE
from app.world import geo

RAW_DIR = CELLS_FILE.parent / "raw"
EXTENT = {"lat_min": 5.640, "lat_max": 5.800, "lon_min": -6.780, "lon_max": -6.500}  # 18 x 31 km, on the Hansen grid
MUST_CONTAIN = {
    "Soubre town": (5.7850, -6.5940, 0.0, 0.0),
    "demo plot P-041 and 2.5 km around it": (5.7052, -6.7244, 2500.0, 2500.0),
    "video window, 2.4 x 1.4 km": (5.7247, -6.5395, 700.0, 1200.0),
    "dam lake (flooding, not deforestation)": (5.70533, -6.55601, 0.0, 0.0),
}
FRACTION_DECIMALS = 3  # the forest threshold is 0.5: a thousandth is plenty
TIMEOUT_S = 120

SOURCES = {
    "jrc": {
        "name": "Global Forest Cover 2020 (GFC2020), version 4, European Commission Joint Research Centre",
        "url": "https://ies-ows.jrc.ec.europa.eu/iforce/gfc2020/download.py?version=v4&type=tile&lat=N10&lon=W10",
        "file": "JRC_GFC2020_V4_N10_W10.tif",
        "values": "1 forest in 2020, 0 not forest",
        "licence": "CC BY 4.0 (to confirm on the JRC dataset page: not reachable from the build environment)",
    },
    "hansen": {
        "name": "Hansen/UMD/Google/USGS/NASA Global Forest Change 2000-2024, v1.12, lossyear",
        "url": ("https://storage.googleapis.com/earthenginepartners-hansen/GFC-2024-v1.12/"
                "Hansen_GFC-2024-v1.12_lossyear_10N_010W.tif"),
        "file": "Hansen_GFC-2024-v1.12_lossyear_10N_010W.tif",
        "values": "0 no loss, 1-24 loss detected primarily in 2001-2024",
        "licence": "CC BY 4.0, Creative Commons Attribution 4.0 International License",
        "credit": "Source: Hansen/UMD/Google/USGS/NASA",
        "citation": ("Hansen, M. C., P. V. Potapov, R. Moore, M. Hancher, S. A. Turubanova, A. Tyukavina, D. Thau, "
                     "S. V. Stehman, S. J. Goetz, T. R. Loveland, A. Kommareddy, A. Egorov, L. Chini, C. O. Justice, "
                     "and J. R. G. Townshend. 2013. High-Resolution Global Maps of 21st-Century Forest Cover Change. "
                     "Science 342 (15 November): 850-53. Data available on-line from: "
                     "https://glad.earthengine.app/view/global-forest-change."),
        "licence_checked_at": ("https://storage.googleapis.com/earthenginepartners-hansen/GFC-2024-v1.12/"
                               "download.html"),
    },
}


class MapError(RuntimeError):
    """A tile is missing, is not what we expect, or does not cover the extent."""


# ---- download ---------------------------------------------------------------------------------

def download(source: dict, raw_dir: Path) -> Path:
    """The tile in data/raw/, downloaded once; a partial download never takes the final name."""
    path = raw_dir / source["file"]
    if path.exists():
        return path
    raw_dir.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    try:
        with requests.get(source["url"], stream=True, timeout=TIMEOUT_S) as response:
            response.raise_for_status()
            with open(partial, "wb") as handle:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    handle.write(chunk)
    except requests.RequestException as error:
        partial.unlink(missing_ok=True)
        raise MapError(f"could not download {source['file']} from {source['url']}: {error}") from error
    partial.rename(path)
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---- the grid ---------------------------------------------------------------------------------

def grid(extent: dict) -> tuple[rasterio.Affine, tuple[int, int]]:
    """The Hansen cells covering the extent: their transform and (rows, columns)."""
    rows = (extent["lat_max"] - extent["lat_min"]) / CELL_DEG
    cols = (extent["lon_max"] - extent["lon_min"]) / CELL_DEG
    on_grid = [(GRID_ORIGIN[0] - extent["lat_max"]) / CELL_DEG, (extent["lon_min"] - GRID_ORIGIN[1]) / CELL_DEG,
               rows, cols]
    if any(abs(v - round(v)) > 1e-6 for v in on_grid):
        raise MapError(f"the extent must sit on the Hansen grid ({CELL_DEG} degree steps): {extent}")
    transform = rasterio.Affine(CELL_DEG, 0.0, extent["lon_min"], 0.0, -CELL_DEG, extent["lat_max"])
    return transform, (round(rows), round(cols))


def check_extent(extent: dict) -> None:
    for name, (lat, lon, half_n_m, half_e_m) in MUST_CONTAIN.items():
        d_lat, d_lon = half_n_m / geo.M_PER_DEG_LAT, half_e_m / geo.m_per_deg_lon(lat)
        if not (extent["lat_min"] <= lat - d_lat and lat + d_lat <= extent["lat_max"]
                and extent["lon_min"] <= lon - d_lon and lon + d_lon <= extent["lon_max"]):
            raise MapError(f"the extent misses the {name}")


# ---- reading the two maps -----------------------------------------------------------------------

def loss_years(path: Path, extent: dict) -> np.ndarray:
    """Year of loss for every cell of the extent (0: no loss), straight from the Hansen tile."""
    transform, shape = grid(extent)
    with rasterio.open(path) as src:
        if src.crs.to_epsg() != 4326 or abs(src.res[0] - CELL_DEG) > 1e-12:
            raise MapError(f"{path.name}: expected EPSG:4326 at {CELL_DEG} degree, got {src.crs} at {src.res}")
        window = from_bounds(extent["lon_min"], extent["lat_min"], extent["lon_max"], extent["lat_max"],
                             src.transform).round_offsets().round_lengths()
        values = src.read(1, window=window)
    if values.shape != shape:
        raise MapError(f"{path.name}: the extent is not fully inside the tile")
    unknown = sorted(set(np.unique(values).tolist()) - set(range(0, 25)))
    if unknown:
        raise MapError(f"{path.name}: unexpected loss year codes {unknown}")
    return np.where(values > 0, 2000 + values.astype(np.int32), 0)


def forest_fractions(path: Path, extent: dict) -> np.ndarray:
    """Share of each cell the JRC map saw as forest in 2020."""
    transform, shape = grid(extent)
    pad = 4 * CELL_DEG
    with rasterio.open(path) as src:
        if src.crs.to_epsg() != 4326:
            raise MapError(f"{path.name}: expected EPSG:4326, got {src.crs}")
        window = from_bounds(extent["lon_min"] - pad, extent["lat_min"] - pad, extent["lon_max"] + pad,
                             extent["lat_max"] + pad, src.transform).round_offsets().round_lengths()
        return resample_forest(src.read(1, window=window), src.window_transform(window), src.nodata,
                               transform, shape)


def resample_forest(values: np.ndarray, transform: rasterio.Affine, nodata, dst_transform: rasterio.Affine,
                    shape: tuple[int, int]) -> np.ndarray:
    """JRC pixels (1 forest, 0 not) averaged onto the cells, each pixel weighted by its overlap."""
    allowed = {0, 1} | ({int(nodata)} if nodata is not None else set())
    unknown = sorted(set(np.unique(values).tolist()) - allowed)
    if unknown:
        raise MapError(f"JRC tile: unexpected values {unknown}; this script expects 1 forest, 0 not forest")
    forest = values.astype(np.float64)
    if nodata is not None:
        forest[values == nodata] = np.nan
    fractions = np.full(shape, np.nan)
    reproject(forest, fractions, src_transform=transform, src_crs="EPSG:4326", src_nodata=np.nan,
              dst_transform=dst_transform, dst_crs="EPSG:4326", dst_nodata=np.nan,
              resampling=Resampling.average)
    if np.isnan(fractions).any():
        raise MapError("JRC tile: some cells of the extent have no data")
    return np.round(fractions, FRACTION_DECIMALS)


# ---- writing the cells ----------------------------------------------------------------------------

def cells_csv_gz(extent: dict, fractions: np.ndarray, years: np.ndarray) -> bytes:
    """The cells as gzipped CSV, north to south then west to east. Same input, same bytes."""
    transform, (rows, cols) = grid(extent)
    text = io.StringIO()
    text.write("lat,lon,forest_fraction_2020,loss_year\n")
    for row in range(rows):
        lat = extent["lat_max"] - (row + 0.5) * CELL_DEG
        for col in range(cols):
            lon = extent["lon_min"] + (col + 0.5) * CELL_DEG
            text.write(f"{lat:.6f},{lon:.6f},{fractions[row, col]:.{FRACTION_DECIMALS}f},{years[row, col]}\n")
    out = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0, compresslevel=9) as handle:
        handle.write(text.getvalue().encode("utf-8"))
    return out.getvalue()


def meta(extent: dict, data: bytes, rows: int, tiles: dict[str, Path]) -> dict:
    return {
        "file": CELLS_FILE.name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "rows": rows,
        "columns": {
            "lat": "cell centre latitude, degrees, 6 decimals",
            "lon": "cell centre longitude, degrees, 6 decimals",
            "forest_fraction_2020": f"share of the cell mapped as forest in 2020 by JRC GFC2020 v4, "
                                    f"{FRACTION_DECIMALS} decimals",
            "loss_year": "year of tree cover loss from Hansen v1.12 (2001-2024), 0 for no loss",
        },
        "grid": {"crs": "EPSG:4326", "cell_deg": CELL_DEG, "origin_lat_lon": list(GRID_ORIGIN),
                 "aligned_on": "Hansen tile 10N_010W"},
        "extent": extent,
        "contains": {name: [lat, lon] for name, (lat, lon, _, _) in MUST_CONTAIN.items()},
        "sources": [dict(SOURCES[key], tile_sha256=sha256_file(path)) for key, path in tiles.items()],
        "built_by": "scripts/fetch_reference_maps.py",
        "built_on": datetime.now(timezone.utc).date().isoformat(),
    }


def build(raw_dir: Path = RAW_DIR, extent: dict = EXTENT) -> tuple[bytes, dict]:
    check_extent(extent)
    tiles = {key: download(source, raw_dir) for key, source in SOURCES.items()}
    years = loss_years(tiles["hansen"], extent)
    fractions = forest_fractions(tiles["jrc"], extent)
    data = cells_csv_gz(extent, fractions, years)
    return data, meta(extent, data, years.size, tiles)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="rebuild and compare, write nothing")
    args = parser.parse_args(argv)
    try:
        data, info = build()
    except MapError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.check:
        committed = json.loads(META_FILE.read_text(encoding="utf-8"))["sha256"]
        same = committed == info["sha256"]
        print(f"{'same bytes' if same else 'DIFFERENT'}: committed {committed[:12]}, rebuilt {info['sha256'][:12]}")
        return 0 if same else 1
    CELLS_FILE.write_bytes(data)
    META_FILE.write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {CELLS_FILE.name}: {info['rows']} cells, {info['bytes'] / 1e6:.2f} MB, sha256 {info['sha256'][:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
