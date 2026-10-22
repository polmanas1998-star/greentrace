"""The verdict in plain words: one sentence a non-expert can read in thirty seconds.

GET /ui/runs/{run_id}/plain    {"run_id", "verdict", "sentence"}
GET /ui/runs/{run_id}/where    the farm and the alert behind the verdict, to draw on a small map

Only the wording is written here. Every farm, date, number and source in the sentence comes
from the run itself: the findings of app.rules.decide on the run's evidence (the same call
that gave the verdict, so a "try to fool it" change is followed) and the rows they point to.
No model writes it, and it never decides anything: the verdict is the rules'.
"""
import re
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from app import orchestrator
from shapely import wkt

from app.checks import geometry, registry
from app.rules import CONTRADICTED, PROVEN, UNVERIFIABLE, Evidence, decide
from app.web import live

router = APIRouter(include_in_schema=False)

SHOWN = 2  # gaps named in an UNVERIFIABLE sentence; the others are counted


@router.get("/ui/runs/{run_id}/plain")
def plain(run_id: str) -> dict[str, Any]:
    finished = orchestrator.RUNS.get(run_id)
    if finished is not None:
        return {"run_id": run_id, "verdict": finished.output.get("verdict"), "sentence": sentence(finished.evidence)}
    kept = live.LIVE.get(run_id)
    if kept is not None and kept.output and kept.output.get("status") == "too_vague":
        return {"run_id": run_id, "verdict": None,
                "sentence": "This promise is too vague to check: it names nothing the data can test."}
    raise HTTPException(status_code=404, detail=f"no finished run {run_id} in this process")


@router.get("/ui/runs/{run_id}/where")
def where(run_id: str) -> dict[str, Any]:
    """The plot of the first finding that decided the verdict, and the alert it names, as the run read them.

    {"plot": {"plot_id", "area_ha", "ring": [[lat, lon], ...] or null, "lat", "lon"} or null,
     "alert": {"alert_id", "alert_date", "confidence", "lat", "lon"} or null, "finding", "verdict"}
    """
    finished = orchestrator.RUNS.get(run_id)
    if finished is None:
        raise HTTPException(status_code=404, detail=f"no finished run {run_id} in this process")
    e = finished.evidence
    decision = decide(e)
    deciding = [f for f in decision["findings"] if f["verdict"] == decision["verdict"] and "plot" in _refs(f)]
    if not deciding:
        return {"run_id": run_id, "plot": None, "alert": None, "finding": None, "verdict": decision["verdict"]}
    f = deciding[0]
    refs = _refs(f)
    plot = _plot(e, refs["plot"])
    ring = None
    if plot["polygon_wkt"]:
        ring = [[round(lat, 6), round(lon, 6)] for lon, lat in wkt.loads(plot["polygon_wkt"]).exterior.coords]
    alert = _alert(e, refs["alert"]) if "alert" in refs else None
    return {
        "run_id": run_id, "verdict": f["verdict"], "finding": f["detail"],
        "plot": {"plot_id": plot["plot_id"], "area_ha": plot["area_ha"], "lat": plot["lat"], "lon": plot["lon"],
                 "ring": ring},
        "alert": alert and {k: alert[k] for k in ("alert_id", "alert_date", "confidence", "lat", "lon")},
    }


def sentence(e: Evidence) -> str:
    decision = decide(e)
    findings = decision["findings"]
    if decision["verdict"] == CONTRADICTED:
        against = [f for f in findings if f["verdict"] == CONTRADICTED]
        text = f"This promise is false: {_why_false(against[0], e)}."
        if len(against) > 1:
            text += f" {_count(len(against) - 1, 'other finding')} in the evidence file {_says(len(against) - 1)} the same."
        return text
    if decision["verdict"] == UNVERIFIABLE:
        gaps = _unique(_missing(f, e) for f in findings if f["verdict"] == UNVERIFIABLE)
        text = f"This promise cannot be checked yet: {'; and '.join(gaps[:SHOWN])}."
        if len(gaps) > SHOWN:
            text += f" {_count(len(gaps) - SHOWN, 'more gap')} {'is' if len(gaps) - SHOWN == 1 else 'are'} listed in the evidence file."
        return text
    if decision["verdict"] == PROVEN:
        return f"This promise holds: {_join(_checked(e))}."
    raise ValueError(f"unknown verdict {decision['verdict']}")


# ---- CONTRADICTED: what shows the promise is false ----------------------------------------------

def _why_false(f: dict, e: Evidence) -> str:
    refs = _refs(f)
    plot = refs.get("plot")
    if f["code"] == "confirmed_alert_in_delivering_plot":
        alert = _alert(e, refs["alert"])
        return (f"a satellite alert shows forest cut on {_day(alert['alert_date'])} on farm {plot}, "
                f"{_still_selling(e, plot)}")
    if f["code"] == "forest_loss_in_delivering_plot":
        return f"satellite maps show {_ha(_first_number(f['detail']))} of forest cut after 2020 on farm {plot}, {_still_selling(e, plot)}"
    if f["code"] == "company_admission":
        doc = next(iter(refs.get("doc", "").split(":")), "")
        return f"the company's own report admits clearing forest ({doc})" if doc else "the company's own report admits clearing forest"
    return f["detail"]


def _still_selling(e: Evidence, plot_id: str) -> str:
    years = registry.delivery_years(e.deliveries).get(plot_id, set())
    return f"which kept selling cocoa to this company until {max(years)}" if years else "which delivered to this company"


# ---- UNVERIFIABLE: what is missing ---------------------------------------------------------------

def _missing(f: dict, e: Evidence) -> str:
    refs = _refs(f)
    plot = refs.get("plot")
    code = f["code"]
    if code == "volume_not_traced":
        share = registry.traced_share(e.company, e.deliveries, e.plots)
        return f"{100 * (1 - share):.0f}% of the cocoa bought in {e.company['fiscal_year']} cannot be traced back to a farm"
    if code == "invalid_coordinates":
        return f"the location given for farm {plot} is not in Côte d'Ivoire, so no map can check it"
    if code == "polygon_missing":
        return (f"farm {plot} ({_plot(e, plot)['area_ha']} ha) was given as a single point, not its boundary, "
                "so no one can see what was cut inside it")
    if code == "source_unavailable":
        return "the satellite alert service did not answer, so no one could look for forest cut"
    if code == "no_independent_source":
        return "there is no satellite alert data for the farms' area, only the company's own records"
    if code == "unconfirmed_alert_in_plot":
        alert = _alert(e, refs["alert"])
        return (f"a satellite alert on farm {plot} ({_day(alert['alert_date'])}) has {alert['confidence']} confidence only "
                "and needs a check on the ground")
    if code == "alert_near_point_plot":
        gap = geometry.gap_to_point_plot_m(_plot(e, plot), _alert(e, refs["alert"]))
        return (f"a satellite alert lies {gap:.0f} m from farm {plot}, which was given as a single point, "
                "so no one can tell whether it is inside")
    if code == "no_reference_data":
        if "did not answer" in f["detail"]:
            return f"the forest maps did not answer for farm {plot}"
        return f"there is no forest map for the area of farm {plot}"
    if code == "forest_loss_below_threshold":
        return (f"farm {plot} lost {_ha(_first_number(f['detail']))} of forest after 2020, too little for the rules "
                "to call it deforestation, so it needs a check on the ground")
    if code == "tree_cover_loss_outside_2020_forest":
        return (f"farm {plot} lost {_ha(_first_number(f['detail'].split(':', 1)[1]))} of trees after 2020 on land "
                "the EU map did not count as forest, so it needs a check on the ground")
    if code == "sources_in_conflict":
        said = re.search(r"^(\S+) paragraph \S+ reports clearing by (.+) in (\d{4});", f["detail"])
        if said:
            return f"a report ({said[1]}) says {said[2]} cut forest in {said[3]}, but no satellite alert confirms it"
        return "a report says forest was cut, but no satellite alert confirms it"
    if code == "impossible_yield":
        said = re.search(r"declared ([\d.]+) t/ha in (\d{4})", f["detail"])
        if said:
            return (f"farm {plot} declared {said[1]} tonnes of cocoa per hectare in {said[2]}, more than a farm can grow "
                    f"({registry.MAX_PLAUSIBLE_T_PER_HA} t per hectare at most), so the cocoa may come from somewhere else")
    if code == "plot_declared_twice":
        owners = [r.split(":", 1)[1] for r in f["refs"] if r.startswith("supplier:")]
        return f"farm {plot} is declared by two suppliers ({' and '.join(owners)}), so its cocoa may be counted twice"
    if code == "narrower_scope":
        return "the company's own report makes this promise for only part of the cocoa it buys, not all of it"
    return f["detail"]


# ---- PROVEN: what was checked ----------------------------------------------------------------------

def _checked(e: Evidence) -> list[str]:
    share = registry.traced_share(e.company, e.deliveries, e.plots)
    farms = len({p["plot_id"] for p in e.plots})
    bought = f"{e.company['total_tonnes']} t of cocoa bought in {e.company['fiscal_year']}"
    done = [f"all {bought} trace back to {_count(farms, 'farm')}" if round(share, 4) >= 1
            else f"{share:.1%} of the {bought} traces back to {_count(farms, 'farm')}"]
    alerts = e.alerts or []
    done.append(f"none of the {_count(len(alerts), 'satellite alert')} in the area shows forest cut after 2020 "
                "on a farm that sells to this company")
    mapped = sorted(plot_id for plot_id, cells in e.reference.items() if cells is not None)
    if mapped:
        done.append(f"forest maps show no forest cut after 2020 on {_join([f'farm {p}' for p in mapped])}")
    done.append("every farm's harvest fits its size")
    done.append("the company's own report promises the same")
    return done


# ---- small helpers ---------------------------------------------------------------------------------

def _refs(f: dict) -> dict[str, str]:
    refs: dict[str, str] = {}
    for ref in f["refs"]:
        kind, _, key = ref.partition(":")
        refs.setdefault(kind, key)
    return refs


def _plot(e: Evidence, plot_id: Optional[str]) -> dict:
    return next(p for p in e.plots if p["plot_id"] == plot_id)


def _alert(e: Evidence, alert_id: str) -> dict:
    return next(a for a in e.alerts or [] if a["alert_id"] == alert_id)


def _day(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%B %Y}"


def _first_number(text: str) -> float:
    return float(re.search(r"\d+(?:\.\d+)?", text)[0])


def _ha(value: float) -> str:
    return f"{value:.1f} ha" if value >= 1 else f"{value:.2f} ha"


def _count(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _says(n: int) -> str:
    return "says" if n == 1 else "say"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _unique(items) -> list[str]:
    return list(dict.fromkeys(items))
