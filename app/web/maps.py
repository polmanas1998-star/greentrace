"""The home page map: one real plot on the real reference maps, read offline.

GET  /api/plot-map?plot=P-042   the 30 m cells around the plot, its outline, and what
                                app/checks/landcover.py measures inside it
POST /api/plot-measure          a plot the judge drew or uploaded (the GeoJSON of input.plot),
                                checked by app/world/drawn.py and measured the same way

Cells come from data/reference_cells.csv.gz (EU JRC GFC2020 forest in 2020, Hansen/UMD
loss year) through landcover.read_cells, and the numbers through landcover.measure: the
page draws them and adds nothing. A background image is shown only when
app/web/static/maps/<plot>.jpg exists (made from Sentinel-2, never invented here).
"""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from shapely import affinity, wkt

from app.checks import landcover
from app.world import drawn, geo

MAPS_DIR = Path(__file__).resolve().parent / "static" / "maps"
DRAWN_PLOT_ID = "P-900"  # a plot the judge draws: free id (the registry runs P-001 to P-040, the demo P-041, P-042)
PLACE = "near Soubré, Côte d'Ivoire · EU JRC 2020 forest map + Hansen loss"
WINDOW_M = (1200.0, 800.0)  # width, height around the plot: the 3:2 frame of the mockup


@dataclass(frozen=True)
class MapPlot:
    plot_id: str
    lat: float
    lon: float
    area_ha: float
    role: str

    @property
    def polygon_wkt(self) -> str:
        return geo.square_wkt(self.lat, self.lon, self.area_ha)


# Amir's decision (22/10): P-042 is the plot shown first; P-041 is the honest counter-example.
# Both outlines are squares of the declared area around the declared point (geo.square_wkt).
PLOTS = {p.plot_id: p for p in (
    MapPlot("P-042", 5.7300, -6.5220, 10.0, "forest in 2020, cleared after"),
    MapPlot("P-041", 5.7052, -6.7244, 10.6, "loss, but no EU 2020 forest inside"),
)}

router = APIRouter(include_in_schema=False)


@router.get("/api/plot-map")
def plot_map(plot: str = Query("P-042", max_length=16)) -> dict[str, Any]:
    if plot not in PLOTS:
        raise HTTPException(status_code=404, detail=f"no map for {plot!r}; maps exist for {sorted(PLOTS)}")
    return {**_measured(plot), "image": _image(plot)}


@router.post("/api/plot-measure")
def plot_measure(plot: Any = Body(..., embed=True)) -> dict[str, Any]:
    """What the run will find in this plot's cells, before the run: same check, same measure, no verdict."""
    try:
        row = drawn.plot_from_geojson(plot)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    measure = landcover.measure(row, landcover.read_cells(*landcover.plot_box(row)))
    return {"plot": {k: row[k] for k in ("plot_id", "lat", "lon", "area_ha")},
            "kind": "polygon" if row["polygon_wkt"] else "point",
            "measure": _numbers(measure, row["area_ha"])}


def _image(plot_id: str) -> str | None:
    return f"/static/maps/{plot_id}.jpg" if (MAPS_DIR / f"{plot_id}.jpg").is_file() else None


@lru_cache(maxsize=len(PLOTS))
def _measured(plot_id: str) -> dict[str, Any]:
    """Read once per process: the cells file is scanned from the north down to the window."""
    plot = PLOTS[plot_id]
    outline = wkt.loads(plot.polygon_wkt)
    window = _window(plot)
    cells = landcover.read_cells(window["lat_min"], window["lat_max"], window["lon_min"], window["lon_max"])
    inside = landcover.read_cells(*landcover.plot_box({"polygon_wkt": plot.polygon_wkt}))
    measure = landcover.measure({"polygon_wkt": plot.polygon_wkt}, inside)
    return {
        "plot": {"id": plot.plot_id, "lat": plot.lat, "lon": plot.lon, "declared_area_ha": plot.area_ha,
                 "role": plot.role, "outline": "square of the declared area around the declared point",
                 "vertices": [[lat, lon] for lon, lat in outline.exterior.coords][:-1]},
        "place": PLACE,
        "window": window,
        "grid": {"cell_deg": landcover.CELL_DEG, "forest_from": landcover.FOREST_FROM,
                 "cutoff_year": landcover.CUTOFF_YEAR},
        # Only the cells that draw something: EU forest in 2020, or a loss year.
        "cells": [[c["lat"], c["lon"], c["forest_fraction_2020"], c["loss_year"]] for c in cells
                  if c["forest_fraction_2020"] >= landcover.FOREST_FROM or c["loss_year"]],
        "measure": _numbers(measure, round(_area_ha(outline, plot.lat), 2)),
        "sources": [s["name"] for s in landcover.load_meta()["sources"]],
        "extent": landcover.load_meta()["extent"],
        "drawn_plot_id": DRAWN_PLOT_ID,
    }


def _window(plot: MapPlot) -> dict[str, float]:
    width, height = WINDOW_M
    d_lat = height / 2 / geo.M_PER_DEG_LAT
    d_lon = width / 2 / geo.m_per_deg_lon(plot.lat)
    extent = landcover.load_meta()["extent"]
    window = {"lat_min": round(plot.lat - d_lat, 6), "lat_max": round(plot.lat + d_lat, 6),
              "lon_min": round(plot.lon - d_lon, 6), "lon_max": round(plot.lon + d_lon, 6),
              "width_m": width, "height_m": height}
    if not (extent["lat_min"] <= window["lat_min"] and window["lat_max"] <= extent["lat_max"]
            and extent["lon_min"] <= window["lon_min"] and window["lon_max"] <= extent["lon_max"]):
        raise ValueError(f"{plot.plot_id}: the map window leaves the committed reference maps")
    return window


def _numbers(measure: dict[str, Any], outline_ha: float) -> dict[str, Any]:
    by_year = measure["loss_ha_by_year"] or {}
    return {
        "outline_ha": outline_ha,
        "forest_2020_ha": measure["forest_2020_ha"],
        "loss_after_2020_ha": measure["loss_after_2020_ha"],
        "loss_ha_by_year": measure["loss_ha_by_year"],
        "main_loss_year": int(max(by_year, key=by_year.get)) if by_year else None,
        "covered": measure["covered"],
        "reason": measure["reason"],
    }


def _area_ha(outline: Any, lat: float) -> float:
    """The outline's area, from degrees to metres at its latitude (same flat-earth model as geo)."""
    metres = affinity.scale(outline, xfact=geo.m_per_deg_lon(lat), yfact=geo.M_PER_DEG_LAT, origin=(0, 0))
    return metres.area / 10_000.0
