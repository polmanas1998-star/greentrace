"""Three routes on top of the investigation (backend only; the page will use them later).

GET  /runs/{run_id}/suppliers   which suppliers to check first: app/explain/suppliers.py
POST /citizen                   one plain sentence for a shopper, demo brand only: app/citizen.py
GET  /api/timelapse             one Sentinel-2 scene a year and that year's Hansen loss: app/timelapse.py
GET  /api/timelapse/image/{name} a time-machine JPEG, from data/timelapse or data/cache/timelapse only

Mounted by app/api.py. Nothing here decides a verdict: each route reads a finished run, runs the
real pipeline, or reads the maps and the scenes.
"""
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse

from app import citizen, orchestrator, timelapse
from app.explain.suppliers import rank
from app.pipeline import new_run_id, run_case
from app.receipt import decision_for

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
