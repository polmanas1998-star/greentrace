"""The five operations GreenTrace offers other agents (MCP tools, A2A skills), each calling the real code.

check_claim(claim, company?, case?)   one investigation, as POST /run: verdict, plain sentence, run_id
check_plots(geojson, promise?, ...)   the analyst's plot file, as POST /api/check-plots
what_if(run_id, entity, field, value) change one value of a finished run, as POST /runs/{run_id}/what_if
explain(run_id)                       the plain sentence and the evidence each finding cites
verify_receipt(receipt)               python verify.py on a receipt: VALID, TAMPERED or UNREADABLE

A run lives in the memory of the process that ran it (orchestrator.RUNS, the last 50): what_if
and explain answer for runs made by the same server. Refused is the one error a caller can fix
(bad input, unknown run); its message says what to change.
"""
import json
from typing import Any, Optional, TextIO

from fastapi import HTTPException

import verify
from app import orchestrator
from app.pipeline import run_case
from app.receipt import RECEIPT_VERSION, build_receipt
from app.standards import VERSION  # noqa: F401 - the version the MCP server and the agent card give
from app.web import features, plain
from app.world.cases import CASES_BY_NUMBER, DEFAULT_CLAIM

NAMES = ("check_claim", "check_plots", "what_if", "explain", "verify_receipt")
DESCRIPTIONS = {
    "check_claim": "Check a company's deforestation-free claim: the agents investigate, fixed rules decide "
                   "PROVEN, CONTRADICTED or UNVERIFIABLE. Returns the verdict, one plain sentence and the run_id.",
    "check_plots": "Check a plot file (GeoJSON FeatureCollection of farms) on the EU JRC 2020 forest map and "
                   "the Hansen/UMD loss years: one verdict per plot, the same as POST /api/check-plots.",
    "what_if": "Change one value of a finished run (alert:ID alert_date or confidence, plot:ID area_ha, lat "
               "or lon) and re-run only what depends on it: the verdict before and after.",
    "explain": "Why a finished run got its verdict: the plain sentence, each finding with the rule that "
               "decided it and the rows it cites, and the single change that would flip it.",
    "verify_receipt": "Re-derive a verdict from its receipt (GET /runs/{run_id}/receipt) offline, as "
                      "python verify.py does: VALID, TAMPERED (and what differs) or UNREADABLE.",
}
EXAMPLES = {
    "check_claim": [DEFAULT_CLAIM],
    "check_plots": ['{"type": "FeatureCollection", "features": [...]}'],
    "what_if": ['{"run_id": "...", "entity": "alert:A-221", "field": "alert_date", "value": "2020-12-31"}'],
    "explain": ['{"run_id": "..."}'],
    "verify_receipt": ['{"receipt": {...}}'],
}
CITED = {"alert": ("alerts", "alert_id"), "plot": ("plots", "plot_id"),
         "supplier": ("suppliers", "supplier_id"), "delivery": ("deliveries", "delivery_id")}


class Refused(ValueError):
    """The caller can fix this: bad input or a run this process does not keep."""


def check_claim(claim: str, company: Optional[str] = None, case: Optional[int] = None, *,
                stream: Optional[TextIO] = None) -> dict[str, Any]:
    """One investigation through the pipeline (app/pipeline.run_case), the verdict in one sentence."""
    body: dict[str, Any] = {"claim": claim}
    if company:
        body["company_id"] = company
    request: dict[str, Any] = {"input": body}
    if case is not None:
        if not orchestrator.sample_mode({}):
            raise Refused("case picks one of the synthetic worlds: sample mode only (SAMPLE_MODE=true)")
        if case not in CASES_BY_NUMBER:
            raise Refused(f"case takes one of {min(CASES_BY_NUMBER)}..{max(CASES_BY_NUMBER)}")
        request["options"] = {"case": case}
    try:
        out = run_case(request, stream=stream)
    except ValueError as error:
        raise Refused(str(error)) from error
    run = orchestrator.RUNS.get(out["run_id"])
    return {
        "run_id": out["run_id"],
        "status": out["status"],
        "mode": out["mode"],
        "verdict": out.get("verdict"),
        "sentence": _sentence(run) if run is not None and out["status"] == "success" else out.get("summary"),
        "verdict_sha256": out.get("verdict_sha256"),
        "degraded": out["degraded"],
        "log_file": out["log_file"],
    }


def check_plots(geojson: Any, promise: Optional[str] = None, documents: Optional[list] = None) -> dict[str, Any]:
    """The analyst's plot file through the same function as POST /api/check-plots."""
    if isinstance(geojson, str):
        try:
            geojson = json.loads(geojson)
        except json.JSONDecodeError as error:
            raise Refused(f"geojson is not JSON: {error}") from error
    body: Any = geojson
    if promise is not None or documents:
        body = {"geojson": geojson, "promise": promise, "documents": documents}
    try:
        return features.check_plots(body)
    except HTTPException as error:
        raise Refused(str(error.detail)) from error


def what_if(run_id: str, entity: str, field: str, value: Any) -> dict[str, Any]:
    """orchestrator.what_if on a run of this process: the verdict before and after one change."""
    try:
        out = orchestrator.what_if(run_id, entity, field, value)
    except (KeyError, ValueError) as error:
        raise Refused(str(error.args[0]) if error.args else str(error)) from error
    change = out["what_if"]
    return {
        "run_id": run_id,
        "change": change["change"],
        "verdict_before": change["verdict_before"],
        "verdict_after": change["verdict_after"],
        "sentence": _sentence(orchestrator.RUNS[run_id]),
        "invalidated_facts": change["invalidated_facts"],
        "rechecked_cards": change["rechecked_cards"],
        "verdict_sha256": out["verdict_sha256"],
    }


def explain(run_id: str) -> dict[str, Any]:
    """The plain sentence and, per finding, the rule, the refutations tried and the rows it cites."""
    run = finished(run_id)
    out = run.output
    findings = [{**{k: f[k] for k in ("claim_part", "code", "finding", "verdict", "rule", "checked_by",
                                       "refutations_tried")},
                 "evidence": [_cited(run, ref) for ref in f["evidence"]]} for f in out.get("findings", [])]
    return {
        "run_id": run_id,
        "verdict": out.get("verdict"),
        "sentence": _sentence(run),
        "findings": findings,
        "flip": out.get("flip"),
        "untested_factors": out.get("untested_factors", []),
        "human_review": out.get("human_review"),
        "verdict_sha256": out.get("verdict_sha256"),
    }


def receipt(run_id: str) -> dict[str, Any]:
    """The receipt of a run of this process, as GET /runs/{run_id}/receipt gives it."""
    return build_receipt(finished(run_id))


def verify_receipt(receipt: Any) -> dict[str, Any]:
    """verify.check on a receipt (an object or its JSON text), with verify.py's own refusal of unreadable ones."""
    try:
        data = json.loads(receipt) if isinstance(receipt, str) else receipt
        if not isinstance(data, dict):
            raise ValueError("a receipt is a JSON object")
        if data.get("receipt_version") != RECEIPT_VERSION:
            raise ValueError(f"receipt_version {data.get('receipt_version')!r}, expected {RECEIPT_VERSION!r}")
        result = verify.check(data)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        return {"status": "UNREADABLE", "problems": [f"{type(error).__name__}: {error}"], "verdict": None,
                "seal": None, "receipt_seal": None}
    return {**result, "receipt_seal": data.get("verdict_sha256") or data.get("seal")}


def finished(run_id: str) -> orchestrator.Investigation:
    run = orchestrator.RUNS.get(run_id)
    if run is None:
        raise Refused(f"no finished run {run_id!r} in this process: runs are kept in memory by the server "
                      f"that made them (the last {orchestrator.KEPT_RUNS})")
    return run


def _sentence(run: orchestrator.Investigation) -> str:
    """The plain sentence of app/web/plain.py; the Writer's summary when a card left unreviewed changed the verdict."""
    text = plain.sentence(run.evidence)
    return text if plain.decide(run.evidence)["verdict"] == run.output.get("verdict") else run.output["summary"]


def _cited(run: orchestrator.Investigation, ref: dict) -> dict[str, Any]:
    """One evidence reference of a finding, with the row the run read for it."""
    e = run.evidence
    if ref["source"] == "company":
        return {**ref, "row": e.company}
    table, key = CITED.get(ref["source"], (None, None))
    rows = (getattr(e, table) or []) if table else []
    return {**ref, "row": next((r for r in rows if r.get(key) == ref["id"]), None)}
