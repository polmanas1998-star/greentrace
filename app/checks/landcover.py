"""What the reference maps say inside one plot: forest in 2020, and tree cover lost each year.

The cells come from data/reference_cells.csv.gz (scripts/fetch_reference_maps.py), or
from query Q10 in live mode: one row per cell of the Hansen grid (0.00025 degree, about
30 m), with the share of the cell the EU map (JRC GFC2020) saw as forest in 2020 and the
year Hansen/UMD saw tree cover lost (0: never). A cell counts for the part of it the plot
covers, and a cell's area follows its latitude. The plot is its polygon, or the disk of
its area around its point, as in app/checks/geometry.py.

Measures only: the verdict stays in app/rules.py. A plot that is not entirely covered by
the cells gives "no reference map for this area yet", never zero. Loss is given twice by
year: on every cell, and on the cells the EU map counted as forest in 2020 (the only
loss the EUDR's cut-off is about). Cells read where no EU 2020 forest map exists (Global
Forest Watch tiles, app/checks/gfw.py) carry forest_fraction_2020 None: the measure then
gives tree cover loss only, and says so (forest_map False), never a forest figure.
"""
import gzip
import json
import math
from pathlib import Path
from typing import Any, Iterable

from shapely import affinity, wkt
from shapely.geometry import Point, box
from shapely.geometry.base import BaseGeometry

from app.world import geo

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
CELLS_FILE = DATA_DIR / "reference_cells.csv.gz"
META_FILE = DATA_DIR / "reference_cells.meta.json"

CELL_DEG = 0.00025  # the Hansen grid: 40 000 cells over a 10 degree tile
GRID_ORIGIN = (10.0, -10.0)  # top-left corner (lat, lon) of Hansen tile 10N_010W
FOREST_FROM = 0.5  # a cell is forest in 2020 when the EU map saw at least half of it as forest
CUTOFF_YEAR = 2020
NO_DATA = "no reference map for this area yet"
DISK_SEGMENTS = 64  # a point-declared plot is a disk drawn with 4 x 64 segments
DECIMALS = 6
UNCOVERED = {"covered": False, "reason": NO_DATA, "forest_2020_ha": None, "loss_ha_by_year": None,
             "forest_loss_ha_by_year": None, "loss_after_2020_ha": None, "cells": []}


def cell_area_ha(lat: float) -> float:
    """Area of one grid cell at this latitude: degrees shrink east-west away from the equator."""
    return (CELL_DEG * geo.M_PER_DEG_LAT) * (CELL_DEG * geo.m_per_deg_lon(lat)) / 10_000.0


def cell_index(lat: float, lon: float) -> tuple[int, int]:
    """(row, column) of the cell holding the point (lat, lon), counted from the tile's top-left corner."""
    return math.floor((GRID_ORIGIN[0] - lat) / CELL_DEG), math.floor((lon - GRID_ORIGIN[1]) / CELL_DEG)


def cell_box(row: int, col: int) -> BaseGeometry:
    north, west = GRID_ORIGIN[0] - row * CELL_DEG, GRID_ORIGIN[1] + col * CELL_DEG
    return box(west, north - CELL_DEG, west + CELL_DEG, north)


def outline(plot: dict) -> BaseGeometry:
    """The plot's polygon, or the disk of its declared area around its point (lon/lat degrees)."""
    if plot.get("polygon_wkt"):
        return wkt.loads(plot["polygon_wkt"])
    radius = geo.disk_radius_m(plot["area_ha"])
    disk = Point(0.0, 0.0).buffer(radius, quad_segs=DISK_SEGMENTS)
    disk = affinity.scale(disk, xfact=1.0 / geo.m_per_deg_lon(plot["lat"]), yfact=1.0 / geo.M_PER_DEG_LAT,
                          origin=(0.0, 0.0))
    return affinity.translate(disk, xoff=plot["lon"], yoff=plot["lat"])


def measure(plot: dict, cells: Iterable[dict]) -> dict[str, Any]:
    """Forest in 2020, loss by year and loss after 2020 inside the plot, with the cells used."""
    shape = outline(plot)
    by_index = {cell_index(c["lat"], c["lon"]): c for c in cells}
    west, south, east, north = shape.bounds
    top, left = cell_index(north, west)
    bottom, right = cell_index(south, east)

    used, forest, after, by_year, on_forest, no_forest_map = [], 0.0, 0.0, {}, {}, False
    for row in range(top, bottom + 1):
        for col in range(left, right + 1):
            square = cell_box(row, col)
            overlap = square.intersection(shape).area / square.area
            if overlap <= 0.0:
                continue
            cell = by_index.get((row, col))
            if cell is None:  # part of the plot falls where the maps were not loaded
                return dict(UNCOVERED)
            area = overlap * cell_area_ha(cell["lat"])
            fraction = cell["forest_fraction_2020"]
            no_forest_map = no_forest_map or fraction is None  # loss read elsewhere, no EU 2020 forest map there
            forest += area * (fraction or 0.0)
            year = cell["loss_year"]
            if year:
                by_year[year] = by_year.get(year, 0.0) + area
                if fraction is not None and fraction >= FOREST_FROM:
                    on_forest[year] = on_forest.get(year, 0.0) + area
                    if year > CUTOFF_YEAR:
                        after += area
            used.append({"lat": cell["lat"], "lon": cell["lon"], "overlap": round(overlap, DECIMALS)})

    if not used:  # a plot with no area at all
        return dict(UNCOVERED)
    return {
        "covered": True,
        "reason": None,
        "forest_map": not no_forest_map,  # False: tree cover loss only, the EU 2020 forest is unknown here
        "forest_2020_ha": None if no_forest_map else round(forest, 4),
        "loss_ha_by_year": _by_year(by_year),
        "forest_loss_ha_by_year": None if no_forest_map else _by_year(on_forest),
        "loss_after_2020_ha": None if no_forest_map else round(after, 4),
        "cells": sorted(used, key=lambda c: (-c["lat"], c["lon"])),
    }


def _by_year(hectares: dict[int, float]) -> dict[str, float]:
    return {str(year): round(ha, 4) for year, ha in sorted(hectares.items())}  # string keys: JSON


COLUMNS = "lat,lon,forest_fraction_2020,loss_year"


def read_cells(lat_min: float, lat_max: float, lon_min: float, lon_max: float,
               path: Path = CELLS_FILE) -> list[dict]:
    """The sample-mode twin of Q10: the committed cells whose centre lies in the rectangle.

    The file runs north to south (scripts/fetch_reference_maps.py), so reading stops past lat_min.
    """
    found = []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        if handle.readline().strip() != COLUMNS:
            raise ValueError(f"{path.name}: expected the columns {COLUMNS}")
        for line in handle:
            lat_text, lon_text, fraction, year = line.rstrip("\n").split(",")
            lat = float(lat_text)
            if lat > lat_max:
                continue
            if lat < lat_min:
                break
            lon = float(lon_text)
            if lon_min <= lon <= lon_max:
                found.append({"lat": lat, "lon": lon, "forest_fraction_2020": float(fraction),
                              "loss_year": int(year)})
    return found


def load_meta(path: Path = META_FILE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def plot_box(plot: dict, margin_m: float = 2 * CELL_DEG * geo.M_PER_DEG_LAT) -> tuple[float, float, float, float]:
    """(lat_min, lat_max, lon_min, lon_max) around the plot, wide enough for every cell it touches."""
    west, south, east, north = outline(plot).bounds
    d_lat = margin_m / geo.M_PER_DEG_LAT
    d_lon = margin_m / geo.m_per_deg_lon((north + south) / 2)
    return (round(south - d_lat, DECIMALS), round(north + d_lat, DECIMALS),
            round(west - d_lon, DECIMALS), round(east + d_lon, DECIMALS))
