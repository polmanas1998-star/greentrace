"""The interface: one page, two views of a run (live trace, evidence file), served by the API.

    from app.web import mount; mount(app)

GET  /            the page (HTML, CSS and JS from app/web/static, no build step, no CDN)
GET  /ui/setup    what the page needs to draw its controls, read from the code
POST /runs and GET /runs/{run_id}/events: see app/web/live.py
"""
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import orchestrator
from app.web import live
from app.world.cases import CASES, DEFAULT_CLAIM

STATIC = Path(__file__).resolve().parent / "static"
WHAT_IF_FIELDS = ("alert_date", "confidence", "area_ha")  # the ones "try to fool it" offers

router = APIRouter(include_in_schema=False)


@router.get("/")
def page() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@router.get("/ui/setup")
def setup() -> dict[str, Any]:
    return {
        "default_mode": "sample" if orchestrator.sample_mode({}) else "live",
        "demo_case": orchestrator.DEFAULT_SAMPLE_CASE,
        "default_claim": DEFAULT_CLAIM,
        "cases": [{"case": c.number, "slug": c.slug, "change": c.change, "expected": c.expected} for c in CASES],
        "what_if": _what_if_fields(),
    }


def _what_if_fields() -> dict[str, dict[str, dict[str, Any]]]:
    """orchestrator.EDITABLE, as the form controls that match each check."""
    fields: dict[str, dict[str, dict[str, Any]]] = {}
    for kind, checks in orchestrator.EDITABLE.items():
        for name, allowed in checks.items():
            if name not in WHAT_IF_FIELDS:
                continue
            if allowed is float:
                control = {"type": "number"}
            elif isinstance(allowed, set):
                control = {"type": "choice", "choices": sorted(allowed)}
            else:
                control = {"type": "date"}  # a YYYY-MM-DD pattern
            fields.setdefault(kind, {})[name] = control
    return fields


def mount(app: FastAPI) -> None:
    app.include_router(router)
    app.include_router(live.router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
