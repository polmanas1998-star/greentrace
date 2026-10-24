"""HTTP API (kit, section 2.6): JSON in, JSON out.

GET  /health                 the service answers
POST /run                    one investigation; returns the evidence file
GET  /runs/{run_id}          the evidence file of a finished run (kept in memory)
POST /runs/{run_id}/what_if  change one value, re-run only what depends on it
GET  /runs/{run_id}/receipt  the verdict receipt: python verify.py receipt.json re-derives it offline
GET  /runs/{run_id}/package  the audit package: one zip, receipt included
GET  /runs/{run_id}/timeline per plot a finding is about: loss by year inside it, cut-off, deliveries
GET  /runs/{run_id}/suppliers, POST /citizen, GET /api/timelapse: app/web/features.py
"""
from typing import Any

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Response
from pydantic import BaseModel

from app import orchestrator
from app.explain.timeline import timeline
from app.receipt import build_receipt, package_zip
from app.pipeline import run_case

load_dotenv()  # keys and SAMPLE_MODE from .env; never committed
app = FastAPI(title="GreenTrace", version="0.1.0")


class WhatIf(BaseModel):
    entity: str          # "alert:A-221" or "plot:P-014"
    field: str           # "alert_date", "confidence", "area_ha", "lat", "lon"
    value: Any


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/run")
def run(case: Any = Body(...)) -> dict[str, Any]:
    try:
        return run_case(case)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    return _finished(run_id).output


@app.get("/runs/{run_id}/receipt")
def get_receipt(run_id: str) -> dict[str, Any]:
    return build_receipt(_finished(run_id))


@app.get("/runs/{run_id}/package")
def get_package(run_id: str) -> Response:
    run = _finished(run_id)
    return Response(package_zip(run, run.log_dir), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="greentrace-{run_id}.zip"'})


@app.get("/runs/{run_id}/timeline")
def get_timeline(run_id: str) -> dict[str, Any]:
    return {"run_id": run_id, **timeline(_finished(run_id).evidence)}


def _finished(run_id: str) -> orchestrator.Investigation:
    finished = orchestrator.RUNS.get(run_id)
    if finished is None:
        raise HTTPException(status_code=404, detail=f"no finished run {run_id} in this process")
    return finished


@app.post("/runs/{run_id}/what_if")
def what_if(run_id: str, change: WhatIf) -> dict[str, Any]:
    try:
        return orchestrator.what_if(run_id, change.entity, change.field, change.value)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

from app.web import mount  # noqa: E402 - the interface (app/web), on top of the routes above
from app.web import features  # noqa: E402 - suppliers to check first, citizen mode, satellite time machine
from app.court import routes as court  # noqa: E402 - /runs/{id}/court, /runs/{id}/attacks
from app.explain.routes import router as explain_plus  # noqa: E402 - graph, world_diff, sentences, radar, letter
from app.geo.router import router as geo_router  # noqa: E402 - passes, calendars, convergence, time slider
mount(app)
app.include_router(features.router)
app.include_router(court.router)
app.include_router(explain_plus)
app.include_router(geo_router)
