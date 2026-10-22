"""HTTP API: GET /health, POST /run (JSON in, JSON out)."""
from typing import Any

from fastapi import Body, FastAPI, HTTPException

from app.pipeline import run_case

app = FastAPI(title="GreenTrace", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/run")
def run(case: Any = Body(...)) -> dict[str, Any]:
    try:
        return run_case(case)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
