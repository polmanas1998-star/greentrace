"""What the page needs around a plot check (POST /api/check-plots, app/plotcheck.py), read from the batch.

GET /ui/check-plots                         the plot checks this process keeps, newest first
GET /ui/check-plots/{batch_id}/view         one plain sentence per plot, and every plot's outline for the map
GET /ui/check-plots/{batch_id}/{plot_id}    one plot: its outline, and the 30 m cells its verdict rests on

The cells are the rows of the batch's own Q10 answer (the receipt hashes the same rows): nothing is
read again, and nothing here decides or changes a verdict.
"""
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from shapely import wkt

from app import plotcheck
from app.checks import landcover
from app.web.plain import plot_sentence
from app.world import geo

router = APIRouter(include_in_schema=False)


@router.get("/ui/check-plots")
def batches() -> list[dict[str, Any]]:
    return [{"batch_id": batch_id, "plots": len(b.results), "summary": b.output["summary"],
             "producing_until": b.producing_until}
            for batch_id, b in reversed(plotcheck.BATCHES.items())]


@router.get("/ui/check-plots/{batch_id}/view")
def view(batch_id: str) -> dict[str, Any]:
    batch = _batch(batch_id)
    verdict = {r["plot_id"]: r["verdict"] for r in batch.results}
    return {
        "batch_id": batch_id,
        "plain": {r["plot_id"]: plot_sentence(r) for r in batch.results},
        "shapes": [{"plot_id": p["plot_id"], "verdict": verdict.get(p["plot_id"]), **_shape(p)} for p in batch.plots],
    }


@router.get("/ui/check-plots/{batch_id}/{plot_id}")
def plot(batch_id: str, plot_id: str) -> dict[str, Any]:
    batch = _batch(batch_id)
    result = next((r for r in batch.results if r["plot_id"] == plot_id), None)
    found = next((p for p in batch.plots if p["plot_id"] == plot_id), None)
    if result is None or found is None:
        raise HTTPException(status_code=404, detail=f"no plot {plot_id} in {batch_id}")
    answer: Optional[int] = (result.get("evidence") or {}).get("answer")
    rows = batch.answers[answer].rows if answer is not None else []
    return {
        "batch_id": batch_id, "plot_id": plot_id, "result": result, "plain": plot_sentence(result),
        "producing_until": batch.producing_until, **_shape(found),
        "cells": [[r["lat"], r["lon"], r["forest_fraction_2020"], r["loss_year"]] for r in rows],
        "grid": {"cell_deg": landcover.CELL_DEG, "forest_from": landcover.FOREST_FROM, "cutoff_year": landcover.CUTOFF_YEAR},
        "window": _window(found),
    }


def _shape(p: dict) -> dict[str, Any]:
    """The outline as [[lat, lon], ...], or a point and the radius of the disk of its declared area."""
    if p["polygon_wkt"]:
        polygons = wkt.loads(p["polygon_wkt"])
        parts = getattr(polygons, "geoms", [polygons])
        return {"rings": [[[round(lat, 6), round(lon, 6)] for lon, lat in part.exterior.coords] for part in parts],
                "lat": p["lat"], "lon": p["lon"], "radius_m": None}
    radius = geo.disk_radius_m(p["area_ha"]) if p["area_ha"] else None
    return {"rings": None, "lat": p["lat"], "lon": p["lon"], "radius_m": radius}


def _window(p: dict) -> dict[str, float]:
    lat_min, lat_max, lon_min, lon_max = landcover.plot_box(p) if p["area_ha"] is not None else (
        p["lat"] - 0.002, p["lat"] + 0.002, p["lon"] - 0.002, p["lon"] + 0.002)
    return {"lat_min": lat_min, "lat_max": lat_max, "lon_min": lon_min, "lon_max": lon_max}


def _batch(batch_id: str) -> plotcheck.Batch:
    batch = plotcheck.BATCHES.get(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"no plot check {batch_id} in this process")
    return batch
