"""The interface: one page (check a claim, live trace, evidence files, reliability tests), served by the API.

    from app.web import mount; mount(app)

GET  /            the page (HTML, CSS and JS from app/web/static, no build step, no CDN)
GET  /ui/setup    what the page needs to draw its controls, read from the code
POST /runs, GET /runs/{run_id}/events, GET /ui/runs: app/web/live.py
GET /api/plot-map, POST /api/plot-measure: app/web/maps.py        POST /ui/bench/{case}: app/web/bench.py
POST /api/lens, GET /api/lens/{key}: app/web/lens.py (the Satellite Lens, app/lens)
"""
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import orchestrator
from app.web import bench, lens, live, maps
from app.world.cases import CASES, CASES_BY_NUMBER, DEFAULT_CLAIM

STATIC = Path(__file__).resolve().parent / "static"
WHAT_IF_FIELDS = ("alert_date", "confidence", "area_ha")  # the ones "try to fool it" offers
EXAMPLES = (("A clean supply chain", 1), ("An admission hidden in an annex", 18))  # buttons under the claim
PLOT_INPUT = True  # the pipeline reads input.plot (app/world/drawn.py): drawing and upload are live
DRAWN_PLOT_WORLD = 1  # a drawn plot joins the clean world (case 1), so its verdict is about that plot only

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
        "examples": [{"label": label, "case": number, "change": CASES_BY_NUMBER[number].change}
                     for label, number in EXAMPLES],
        "plot_input": PLOT_INPUT,
        "drawn_plot_world": DRAWN_PLOT_WORLD,
        "map_plots": [{"id": p.plot_id, "role": p.role} for p in maps.PLOTS.values()],
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
    app.include_router(maps.router)
    app.include_router(bench.router)
    app.include_router(lens.router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
