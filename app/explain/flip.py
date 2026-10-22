"""The flip: the one change of one value that would turn the verdict of the claim around.

Kit, section 6. From the findings that decide the verdict, try, in this fixed order,
the changes of a single value:
  1  an alert's date                  -> 2020-12-31, the cut-off day
  2  an alert's confidence            -> low
  3  a plot's deliveries after 2020   -> 0 t
  4  a supplier's untraced volume     -> 0 t
  5  a plot's position                -> 600 m further from the alert
Within one kind, entities go in id order. A volume that stops being bought also stops
being declared, so 3 and 4 lower the company's declared total by the same tonnes.
Tracing a supplier would need plots the registry does not have, and this file never
invents a plot: 4 leaves the untraced volume out instead, which settles the same check.

Each change is applied to a copy of the evidence and app.rules.decide runs again. The
first change that moves the verdict of the whole claim, not just one part, is the flip.
Every change tried comes back in order, with its verdict, so verify.py can replay the
list exactly. No model, no network, no clock: same evidence, same flip.
"""
import math
from dataclasses import replace
from typing import Any, Callable, Optional

from shapely import affinity, wkt

from app.rules import CUTOFF, FIRST_YEAR_AFTER_CUTOFF, PROVEN, Evidence, decide
from app.world import geo

LOW = "low"
MOVE_M = 600.0
NO_SINGLE_CHANGE = "No single change flips this verdict."
NOTHING_AGAINST = "No finding stands against the claim, so there is no single change to try."
NOTHING_TO_CHANGE = ("No single change flips this verdict: the findings behind it hold no date, "
                     "confidence, tonnage or position to change.")


def compute_flip(evidence: Evidence) -> dict[str, Any]:
    """The verdict, every change tried with the verdict it gives, the first that flips, and one sentence."""
    decision = decide(evidence)
    verdict = decision["verdict"]
    tried = [{"change": change, "verdict": decide(apply(evidence, change))["verdict"]}
             for change in candidates(evidence, decision)]
    first = next((index for index, t in enumerate(tried) if t["verdict"] != verdict), None)
    return {"verdict": verdict, "tried": tried, "first_flip": first, "sentence": _sentence(verdict, tried, first)}


# ---- which changes to try ----------------------------------------------------------------

def candidates(evidence: Evidence, decision: dict[str, Any]) -> list[dict[str, Any]]:
    """The single-value changes to try, in the fixed order, from the refs of the deciding findings."""
    verdict = decision["verdict"]
    deciding = [] if verdict == PROVEN else [f for f in decision["findings"] if f["verdict"] == verdict]
    alert_ids = sorted({key for f in deciding for kind, key in _refs(f) if kind == "alert"})
    plot_ids = sorted({key for f in deciding for kind, key in _refs(f) if kind == "plot"})
    pairs = sorted({(plot, alert) for f in deciding
                    for plot in _keys(f, "plot") for alert in _keys(f, "alert")})
    alerts = {a["alert_id"]: a for a in evidence.alerts or []}
    plots: dict[str, dict] = {}
    for plot in evidence.plots:
        plots.setdefault(plot["plot_id"], plot)

    changes = []
    for field, to in (("alert_date", CUTOFF), ("confidence", LOW)):
        changes += [_change(f"alert:{a}", field, alerts[a][field], to) for a in alert_ids if alerts[a][field] != to]
    for plot_id in plot_ids:
        tonnes = sum(d["tonnes"] for d in evidence.deliveries
                     if d["plot_id"] == plot_id and d["year"] >= FIRST_YEAR_AFTER_CUTOFF)
        if tonnes > 0:
            changes.append(_change(f"plot:{plot_id}", "tonnes_after_2020", round(tonnes, 6), 0))
    if any(f["code"] == "volume_not_traced" for f in deciding):
        for supplier_id, tonnes in _untraced_by_supplier(evidence).items():
            changes.append(_change(f"supplier:{supplier_id}", "untraced_tonnes", round(tonnes, 6), 0))
    for plot_id, alert_id in pairs:
        plot = plots[plot_id]
        changes.append(dict(_change(f"plot:{plot_id}", "position", [plot["lat"], plot["lon"]],
                                    _away_from(plot, alerts[alert_id])), relative_to=f"alert:{alert_id}"))
    return changes


def _refs(finding: dict) -> list[tuple[str, str]]:
    return [tuple(ref.split(":", 1)) for ref in finding["refs"]]


def _keys(finding: dict, kind: str) -> list[str]:
    return [key for k, key in _refs(finding) if k == kind]


def _change(entity: str, field: str, before: Any, after: Any) -> dict[str, Any]:
    return {"entity": entity, "field": field, "from": before, "to": after}


def _untraced_by_supplier(evidence: Evidence) -> dict[str, float]:
    """supplier_id -> tonnes of the fiscal year that no plot of the registry traces."""
    totals: dict[str, float] = {}
    untraced = _untraced(evidence)
    for d in evidence.deliveries:
        if untraced(d) and d["tonnes"] > 0:
            totals[d["supplier_id"]] = totals.get(d["supplier_id"], 0.0) + d["tonnes"]
    return dict(sorted(totals.items()))


def _untraced(evidence: Evidence) -> Callable[[dict], bool]:
    known, year = {p["plot_id"] for p in evidence.plots}, evidence.company["fiscal_year"]
    return lambda d: d["year"] == year and d["plot_id"] not in known


def _away_from(plot: dict, alert: dict) -> list[float]:
    """The plot's centre moved MOVE_M metres along the line from the alert through the plot."""
    north = (plot["lat"] - alert["lat"]) * geo.M_PER_DEG_LAT
    east = (plot["lon"] - alert["lon"]) * geo.m_per_deg_lon((plot["lat"] + alert["lat"]) / 2.0)
    length = math.hypot(north, east)
    if length < 1.0:  # the alert sits on the centre: any way out will do, north it is
        north, east, length = 1.0, 0.0, 1.0
    return list(geo.offset(plot["lat"], plot["lon"], north / length * MOVE_M, east / length * MOVE_M))


# ---- applying one change to a copy ---------------------------------------------------------

def apply(evidence: Evidence, change: dict[str, Any]) -> Evidence:
    """A copy of the evidence with one change made; the input is never modified."""
    kind, key = change["entity"].split(":", 1)
    field, to = change["field"], change["to"]
    if kind == "alert" and field in ("alert_date", "confidence"):
        return replace(evidence, alerts=[dict(a, **{field: to}) if a["alert_id"] == key else a
                                         for a in evidence.alerts or []])
    if kind == "plot" and field == "tonnes_after_2020" and to == 0:
        return _without_tonnes(evidence, lambda d: d["plot_id"] == key and d["year"] >= FIRST_YEAR_AFTER_CUTOFF)
    if kind == "supplier" and field == "untraced_tonnes" and to == 0:
        untraced = _untraced(evidence)
        return _without_tonnes(evidence, lambda d: d["supplier_id"] == key and untraced(d))
    if kind == "plot" and field == "position":
        (lat_from, lon_from), (lat_to, lon_to) = change["from"], to
        return replace(evidence, plots=[_moved(p, lat_to - lat_from, lon_to - lon_from) if p["plot_id"] == key else p
                                        for p in evidence.plots])
    raise ValueError(f"not a change this file can make: {change}")


def _without_tonnes(evidence: Evidence, dropped: Callable[[dict], bool]) -> Evidence:
    year = evidence.company["fiscal_year"]
    removed = sum(d["tonnes"] for d in evidence.deliveries if dropped(d) and d["year"] == year)
    return replace(
        evidence,
        deliveries=[dict(d, tonnes=0.0) if dropped(d) else d for d in evidence.deliveries],
        company=dict(evidence.company, total_tonnes=round(evidence.company["total_tonnes"] - removed, 2)),
    )


def _moved(plot: dict, d_lat: float, d_lon: float) -> dict:
    moved = dict(plot, lat=round(plot["lat"] + d_lat, geo.COORD_DECIMALS),
                 lon=round(plot["lon"] + d_lon, geo.COORD_DECIMALS))
    if plot["polygon_wkt"]:
        shape = affinity.translate(wkt.loads(plot["polygon_wkt"]), xoff=d_lon, yoff=d_lat)
        moved["polygon_wkt"] = wkt.dumps(shape, rounding_precision=geo.COORD_DECIMALS)
    return moved


# ---- the sentence ----------------------------------------------------------------------------

def _sentence(verdict: str, tried: list[dict], first: Optional[int]) -> str:
    if first is not None:
        return f"The claim would be {tried[first]['verdict']} if {_phrase(tried[first]['change'])}."
    if tried:
        return NO_SINGLE_CHANGE
    return NOTHING_AGAINST if verdict == PROVEN else NOTHING_TO_CHANGE


def _phrase(change: dict[str, Any]) -> str:
    key, field = change["entity"].split(":", 1)[1], change["field"]
    if field == "alert_date":
        return f"alert {key} were dated {change['to']}"
    if field == "confidence":
        return f"alert {key} had {change['to']} confidence"
    if field == "tonnes_after_2020":
        return f"plot {key} had delivered nothing after 2020"
    if field == "untraced_tonnes":
        return f"the {change['from']:g} t from supplier {key} that no plot traces were left out of the declared volume"
    return f"plot {key} lay {MOVE_M:.0f} m further from alert {change['relative_to'].split(':', 1)[1]}"
