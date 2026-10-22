"""Three routes on top of the investigation (backend only; the page will use them later).

GET  /runs/{run_id}/suppliers   which suppliers to check first: app/explain/suppliers.py
POST /citizen                   one plain sentence for a shopper, demo brand only: app/citizen.py
GET  /api/timelapse             one Sentinel-2 scene a year and that year's Hansen loss: app/timelapse.py
GET  /api/timelapse/image/{name} a time-machine JPEG, from data/timelapse or data/cache/timelapse only
POST /api/check-plots           the analyst's own plot file (GeoJSON): every plot checked, app/plotcheck.py
GET  /api/check-plots/{id}[/plots_no_issue_found.geojson | /findings.csv | /receipt | /package]

Mounted by app/api.py. Nothing here decides a verdict: each route reads a finished run, runs the
real pipeline, or reads the maps and the scenes.
"""
from typing import Any, Optional

import sqlite3

from fastapi import APIRouter, Body, HTTPException, Query, Response
from fastapi.responses import FileResponse

from app import citizen, orchestrator, plotcheck, plotfile, timelapse
from app.explain.suppliers import rank
from app.pipeline import new_run_id, run_case
from app.queries import SQLiteEngine, ZetarisEngine
from app.receipt import decision_for
from app.world.store import attach_reference

router = APIRouter()


@router.get("/runs/{run_id}/suppliers")
def suppliers(run_id: str) -> dict[str, Any]:
    run = orchestrator.RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no finished run {run_id} in this process")
    decision = decision_for(run.evidence, [c["id"] for c in run.cards if c["id"] not in run.reviews])
    return {"run_id": run_id, "verdict": decision["verdict"], **rank(run.evidence, decision)}


@router.post("/citizen")
def ask_as_a_citizen(body: Any = Body(...)) -> dict[str, Any]:
    try:
        return citizen.ask(body, run_case, new_run_id())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/api/timelapse")
def satellite_time_machine(plot: Optional[str] = Query(None, max_length=16), lat: Optional[float] = None,
                           lon: Optional[float] = None) -> dict[str, Any]:
    try:
        return timelapse.timelapse(plot, lat, lon, sample=orchestrator.sample_mode({}))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/api/timelapse/image/{name}")
def time_machine_image(name: str) -> FileResponse:
    path = timelapse.image_path(name)
    if path is None:
        raise HTTPException(status_code=404, detail=f"no time-machine image {name!r}")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


# ---- the analyst's own plot file ----------------------------------------------------------------------

def map_engine(sample: bool) -> SQLiteEngine | ZetarisEngine:
    """Where Q10 reads the cells: the committed cells in SQLite (sample mode), or Zetaris (live)."""
    if sample:
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        connection.row_factory = sqlite3.Row
        attach_reference(connection)
        return SQLiteEngine(connection)
    from app.adapters.zetaris import Zetaris
    try:
        return ZetarisEngine(Zetaris.from_env())
    except Exception as error:  # noqa: BLE001 - missing settings: say what live mode needs
        raise HTTPException(status_code=503, detail=f"live mode needs Zetaris (.env): {error}") from error


@router.post("/api/check-plots")
def check_plots(body: Any = Body(...)) -> dict[str, Any]:
    geojson = body.get("geojson") if isinstance(body, dict) and "geojson" in body else body
    try:
        return plotcheck.check(geojson, f"plots-{new_run_id()}", map_engine(orchestrator.sample_mode({})))
    except plotfile.FileRefused as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _batch(batch_id: str) -> plotcheck.Batch:
    batch = plotcheck.BATCHES.get(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"no plot check {batch_id} in this process")
    return batch


@router.get("/api/check-plots/{batch_id}")
def plot_check_result(batch_id: str) -> dict[str, Any]:
    return _batch(batch_id).output


@router.get("/api/check-plots/{batch_id}/plots_no_issue_found.geojson")
def plot_check_geojson(batch_id: str) -> Response:
    data = plotcheck._json(plotcheck.no_issue_geojson(_batch(batch_id)))
    return Response(data, media_type="application/geo+json",
                    headers={"Content-Disposition": f'attachment; filename="{batch_id}_no_issue_found.geojson"'})


@router.get("/api/check-plots/{batch_id}/findings.csv")
def plot_check_csv(batch_id: str) -> Response:
    return Response(plotcheck.findings_csv(_batch(batch_id)).encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{batch_id}_findings.csv"'})


@router.get("/api/check-plots/{batch_id}/receipt")
def plot_check_receipt(batch_id: str) -> dict[str, Any]:
    return plotcheck.receipt(_batch(batch_id))


@router.get("/api/check-plots/{batch_id}/package")
def plot_check_package(batch_id: str) -> Response:
    return Response(plotcheck.package_zip(_batch(batch_id)), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="greentrace-{batch_id}.zip"'})
