"""The court's two routes, computed from a finished run kept in this process (no second run).

GET /runs/{run_id}/court    the debate, round by round, the rules' verdict and the Writer's judgment
GET /runs/{run_id}/attacks  the Evaluator's journal of attacks: every refutation tried, its query, its end
"""
from typing import Any

from fastapi import APIRouter, HTTPException

from app import orchestrator
from app.court.defense import attack_log
from app.court.judge import hold_court

router = APIRouter()


@router.get("/runs/{run_id}/court")
def get_court(run_id: str) -> dict[str, Any]:
    return hold_court(_finished(run_id))


@router.get("/runs/{run_id}/attacks")
def get_attacks(run_id: str) -> dict[str, Any]:
    try:
        return attack_log(_finished(run_id))
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _finished(run_id: str) -> orchestrator.Investigation:
    run = orchestrator.RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no finished run {run_id} in this process")
    return run
