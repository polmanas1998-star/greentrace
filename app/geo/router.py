"""The routes of app/geo: satellite passes, calendars, convergence of the sources, the time slider.

GET  /api/plots/passes?lat=&lon=&days=30        daylight Sentinel-2 passes over a point (or plot_id=)
GET  /api/plots/passes.ics?lat=&lon=&days=30    the same passes as an iCalendar file
GET  /runs/{run_id}/calendar                    a run's events: next pass, evidence expiry, supplier follow-ups
GET  /runs/{run_id}/calendar.ics                the same events as an iCalendar file
GET  /api/plots/convergence?plot_id=            what each source says about one plot, and how many agree
POST /api/plots/convergence                     the same for a GeoJSON plot (Polygon, or Point with area_ha)
GET  /api/plots/verdict_at?plot_id=&year=       the map rule on the maps cut at the end of that year
GET  /api/plots/verdict_series?plot_id=         every year of the slider at once

Mounted by app/api.py with one include_router line. Nothing here decides a verdict: the time slider calls
app/rules.py as it is, the rest reads orbits, runs and maps.
"""
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Response

from app import orchestrator
from app.checks import gfw
from app.geo import convergence, events, ics, passes, plots, settings, time_slider, whisp

router = APIRouter()
CALENDAR = "text/calendar; charset=utf-8"


def _place(lat: Optional[float], lon: Optional[float], plot_id: Optional[str]) -> tuple[float, float, Optional[str]]:
    if plot_id is not None:
        plot = _plot(plot_id)
        return plot["lat"], plot["lon"], plot["plot_id"]
    if lat is None or lon is None:
        raise HTTPException(status_code=422, detail="give lat and lon, or plot_id")
    return lat, lon, None


def _plot(plot_id: str) -> dict[str, Any]:
    try:
        return plots.by_id(plot_id)
    except plots.UnknownPlot as error:
        raise HTTPException(status_code=404, detail=str(error.args[0])) from error


def _sample() -> bool:
    return orchestrator.sample_mode({})


@router.get("/api/plots/passes")
def plot_passes(lat: Optional[float] = None, lon: Optional[float] = None,
                days: int = Query(settings.PASS_DAYS_DEFAULT, ge=1, le=settings.PASS_DAYS_MAX),
                plot_id: Optional[str] = Query(None, max_length=16)) -> dict[str, Any]:
    lat, lon, plot_id = _place(lat, lon, plot_id)
    try:
        return {"plot_id": plot_id, **passes.report(lat, lon, days=days, sample=_sample())}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/api/plots/passes.ics")
def plot_passes_ics(lat: Optional[float] = None, lon: Optional[float] = None,
                    days: int = Query(settings.PASS_DAYS_DEFAULT, ge=1, le=settings.PASS_DAYS_MAX),
                    plot_id: Optional[str] = Query(None, max_length=16)) -> Response:
    lat, lon, plot_id = _place(lat, lon, plot_id)
    try:
        answer, found = events.pass_events(lat, lon, plot_id=plot_id, days=days, sample=_sample())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if answer["status"] != "success":
        raise HTTPException(status_code=503, detail=answer["reason"])
    if not found:
        raise HTTPException(status_code=404, detail=f"no daylight pass in the next {days} days")
    name = f"Sentinel-2 passes over {plot_id or 'the plot'}"
    return Response(ics.calendar(found, passes.tles.parse_iso(answer["from"]), name), media_type=CALENDAR,
                    headers={"Content-Disposition": f'attachment; filename="passes-{plot_id or "plot"}.ics"'})


def _run(run_id: str) -> orchestrator.Investigation:
    run = orchestrator.RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no finished run {run_id} in this process")
    return run


@router.get("/runs/{run_id}/calendar")
def run_calendar(run_id: str) -> dict[str, Any]:
    answer, _ = events.run_events(_run(run_id), sample=_sample())
    return answer


@router.get("/runs/{run_id}/calendar.ics")
def run_calendar_ics(run_id: str) -> Response:
    answer, found = events.run_events(_run(run_id), sample=_sample())
    if not found:
        reasons = "; ".join(s["reason"] for s in answer["not_scheduled"]) or "no event"
        raise HTTPException(status_code=404, detail=f"nothing to schedule for run {run_id}: {reasons}")
    content = ics.calendar(found, passes.tles.parse_iso(answer["built_at"]), f"GreenTrace run {run_id}")
    return Response(content, media_type=CALENDAR,
                    headers={"Content-Disposition": f'attachment; filename="greentrace-{run_id}.ics"'})


def _converge(plot: dict[str, Any]) -> dict[str, Any]:
    sample = _sample()
    return convergence.converge(plot, sample=sample, remote=None if sample else gfw.Tiles(),
                                whisp_key=whisp.api_key())


@router.get("/api/plots/convergence")
def plot_convergence(plot_id: str = Query(..., max_length=16)) -> dict[str, Any]:
    return _converge(_plot(plot_id))


@router.post("/api/plots/convergence")
def plot_convergence_geojson(plot: Any = Body(...)) -> dict[str, Any]:
    try:
        row = plots.from_geojson(plot)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _converge(row)


@router.get("/api/plots/verdict_at")
def plot_verdict_at(plot_id: str = Query(..., max_length=16),
                    year: int = Query(..., ge=settings.SLIDER_FIRST_YEAR, le=settings.SLIDER_LAST_YEAR)
                    ) -> dict[str, Any]:
    return time_slider.verdict_at(_plot(plot_id), year)


@router.get("/api/plots/verdict_series")
def plot_verdict_series(plot_id: str = Query(..., max_length=16)) -> dict[str, Any]:
    return time_slider.series(_plot(plot_id))
