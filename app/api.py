"""HTTP API (kit, section 2.6): JSON in, JSON out.

GET  /health                 the service answers
POST /run                    one investigation; returns the evidence file
GET  /runs/{run_id}          the evidence file of a finished run (kept in memory)
POST /runs/{run_id}/what_if  change one value, re-run only what depends on it
"""
from typing import Any

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel

from app import orchestrator
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
    finished = orchestrator.RUNS.get(run_id)
    if finished is None:
        raise HTTPException(status_code=404, detail=f"no finished run {run_id} in this process")
    return finished.output


@app.post("/runs/{run_id}/what_if")
def what_if(run_id: str, change: WhatIf) -> dict[str, Any]:
    try:
        return orchestrator.what_if(run_id, change.entity, change.field, change.value)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
