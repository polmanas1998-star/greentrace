"""Five read-only routes on a finished run: nothing is re-run, every answer is computed from what the run kept.

GET /runs/{run_id}/graph       evidence dependency graph: app/explain/graph.py
GET /runs/{run_id}/world_diff  the World Model before and after the last what_if: app/explain/world_diff.py
GET /runs/{run_id}/sentences   Deep Trace, the report sentence by sentence with its rows: app/explain/deep_trace.py
GET /runs/{run_id}/radar       what each claim needs, and which missing document would flip it: app/explain/radar.py
GET /runs/{run_id}/letter?lang=en|fr|es|pt[&supplier=F-007]   the supplier letters: app/explain/letters_i18n.py
POST /runs/{run_id}/ask        {"question"}: an answer from the run's own lines only: app/explain/ask.py

Mounted by app/api.py. The verdict is never decided here: it is read from the run.
"""
import os
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from app import orchestrator
from app.adapters.llm import ModelGate
from app.explain import ask, deep_trace, graph, letters_i18n, radar, world_diff
from app.tracing import trace

router = APIRouter()


def _finished(run_id: str) -> orchestrator.Investigation:
    run = orchestrator.RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no finished run {run_id} in this process")
    return run


@router.get("/runs/{run_id}/graph")
def evidence_graph(run_id: str) -> dict[str, Any]:
    return graph.graph(_finished(run_id))


@router.get("/runs/{run_id}/world_diff")
def memory_diff(run_id: str) -> dict[str, Any]:
    return world_diff.world_diff(_finished(run_id))


@router.get("/runs/{run_id}/sentences")
def sentences(run_id: str) -> dict[str, Any]:
    return deep_trace.sentences(_finished(run_id))


@router.get("/runs/{run_id}/radar")
def contradiction_radar(run_id: str) -> dict[str, Any]:
    return radar.radar(_finished(run_id))


@router.get("/runs/{run_id}/letter")
def supplier_letter(run_id: str, lang: str = Query("en", max_length=2),
                    supplier: Optional[str] = Query(None, max_length=16)) -> dict[str, Any]:
    run = _finished(run_id)
    model = None
    if lang != "en" and run.ctx.model is not None:  # the run used a model: the translation may too
        gate = ModelGate(run_id, env={**os.environ, "SAMPLE_MODE": "false"},
                         log=lambda record: trace(record, log_dir=run.log_dir))
        model = gate if gate.providers else None
    try:
        return letters_i18n.letters(run, lang, model=model, supplier=supplier)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/runs/{run_id}/ask")
def ask_about_the_run(run_id: str, body: Any = Body(...)) -> dict[str, Any]:
    run = _finished(run_id)
    model = None
    if run.ctx.model is not None:  # the run used a model: the answer may too
        gate = ModelGate(run_id, env={**os.environ, "SAMPLE_MODE": "false"},
                         log=lambda record: trace(record, log_dir=run.log_dir))
        model = gate if gate.providers else None
    question = body.get("question") if isinstance(body, dict) else None
    try:
        return ask.ask(run, question, model=model)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
