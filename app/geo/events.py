"""A run's calendar: the next satellite pass, when each piece of evidence expires, and the supplier follow-ups.

  pass       the next daylight Sentinel-2 pass over the plots the findings are about (app/geo/passes.py), or
             over the centre of the supply area when no finding names a plot; grouped when one pass covers
             several plots
  expiry     one reminder per source the run read, on the date its evidence stops being current:
               alerts       the date the run read them (trace, query Q4) + ALERTS_VALID_DAYS
               maps         JRC GFC2020 v4 and Hansen GFC v1.12, when the run measured a plot on them (Q10):
                            the evidence holds while the version is current, so: check it REFERENCE_MAPS_RECHECK_MONTHS
                            after the maps were built (data/reference_cells.meta.json)
               documents    each document the run read (Q1): the date it declares + DOCUMENTS_VALID_MONTHS
  follow_up  verdict UNVERIFIABLE: for each supplier letter, a reminder FOLLOW_UP_DAYS after the run, with the letter
The durations are settings (app/geo/settings.py), not facts; each event's description names the one it used.
The run's dates come from its trace (logs/<run_id>.jsonl): the first line, and the line of each query.
What cannot be scheduled is listed under not_scheduled with the reason, never guessed.
"""
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from app.checks import landcover
from app.geo import ics, passes, settings, tle as tles
from app.tracing import LOG_DIR

_DOC_YEAR = re.compile(r"(?:^|[_-])((?:19|20)\d{2})$")
_QUERY = re.compile(r"^(Q\d+)\((.*)\)$")


# ---- what the trace says about the run ---------------------------------------------------------------------------

def trace_lines(run_id: str, log_dir: Optional[Path]) -> list[dict]:
    path = (log_dir or LOG_DIR) / f"{run_id}.jsonl"
    if not path.is_file():
        return []
    lines = []
    for text in path.read_text(encoding="utf-8").splitlines():
        try:
            lines.append(json.loads(text))
        except ValueError:
            continue
    return lines


def queries(lines: list[dict]) -> list[tuple[str, str, datetime, str]]:
    """(query id, parameters, when, output) of every successful query of the run, in order."""
    found = []
    for line in lines:
        match = _QUERY.match(line.get("input_summary") or "")
        if line.get("action") == "query_zetaris" and line.get("status") == "success" and match:
            found.append((match[1], match[2], tles.parse_iso(line["timestamp"]), line.get("output_summary") or ""))
    return found


def add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    for last in (31, 30, 29, 28):
        try:
            return date(year, month, min(day.day, last))
        except ValueError:
            continue
    raise AssertionError("unreachable")


def declared_date(doc_id: str) -> Optional[tuple[date, str]]:
    """(date, how it was read) of a document: the sample documents declare only their year, in their id."""
    match = _DOC_YEAR.search(doc_id)
    if not match:
        return None
    month, day = settings.DOCUMENT_YEAR_ONLY_MONTH_DAY
    return date(int(match[1]), month, day), (f"{doc_id} declares only its year, {match[1]}: counted from "
                                             f"{month:02d}-{day:02d} of it (DOCUMENT_YEAR_ONLY_MONTH_DAY)")


# ---- events --------------------------------------------------------------------------------------------------------

def all_day(kind: str, uid: str, title: str, day: date, description: str) -> dict[str, Any]:
    return {"kind": kind, "uid": uid, "title": title, "start": day, "end": day + timedelta(days=1),
            "description": description}


def pass_event(found: dict, plot_ids: list[str], place: str) -> dict[str, Any]:
    start, end = tles.parse_iso(found["start"]), tles.parse_iso(found["end"])
    where = f"plot {plot_ids[0]}" if len(plot_ids) == 1 else (f"plots {', '.join(plot_ids)}" if plot_ids else place)
    return {
        "kind": "pass", "uid": f"pass-{found['norad']}-{ics.stamp(start)}@greentrace",
        "title": f"{found['satellite']} pass over {where}",
        "start": start, "end": max(end, start + timedelta(seconds=1)),
        "description": (f"{found['satellite']} passes over {where} at {found['time']}, "
                        f"{found['max_elevation_deg']} degrees above the horizon, sun at "
                        f"{found['sun_elevation_deg']} degrees ({found['direction']}). {passes.NOTE}"),
    }


def run_events(run, *, now: Optional[datetime] = None, sample: bool = True,
               fetch: Callable[[str], str] = tles.default_fetch, shipped: Optional[Path] = None,
               cache: Optional[Path] = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(the answer of GET /runs/{run_id}/calendar, its events for the .ics) of one finished run
    (app.orchestrator.Investigation): every event, and what could not be scheduled."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    run_id, output, evidence = run.ctx.run_id, run.output, run.evidence
    lines = trace_lines(run_id, run.log_dir)
    trace_file = f"logs/{run_id}.jsonl"
    if lines:
        run_at, run_date_from = tles.parse_iso(lines[0]["timestamp"]), f"the first line of {trace_file}"
    else:
        run_at, run_date_from = now, f"{trace_file} not found: dates counted from now"
    events, skipped = [], []

    # pass
    plot_ids = sorted({e["id"] for f in output.get("findings", []) for e in f.get("evidence", [])
                       if e.get("source") == "plot"})
    rows = {p["plot_id"]: p for p in evidence.plots}
    targets = [(rows[p]["lat"], rows[p]["lon"], [p]) for p in plot_ids if p in rows][:settings.PASS_EVENTS_MAX]
    if not targets and evidence.plots:
        targets = [(round(sum(p["lat"] for p in evidence.plots) / len(evidence.plots), 6),
                    round(sum(p["lon"] for p in evidence.plots) / len(evidence.plots), 6), [])]
    tle_set, reason = tles.current(sample, now, fetch=fetch, shipped=shipped, cache=cache)
    if tle_set is None:
        skipped.append({"kind": "pass", "reason": reason})
    else:
        grouped: list[tuple[dict, list[str]]] = []
        for lat, lon, ids in targets:
            found = passes.predict(tle_set, lat, lon, now, settings.PASS_DAYS_DEFAULT)
            if not found:
                skipped.append({"kind": "pass", "reason": f"no daylight pass over {ids or 'the supply area'} "
                                                           f"in the next {settings.PASS_DAYS_DEFAULT} days"})
                continue
            first = found[0]
            same = next((g for g in grouped if g[0]["satellite"] == first["satellite"] and
                         abs((tles.parse_iso(g[0]["time"]) - tles.parse_iso(first["time"])).total_seconds()) <= 120),
                        None)
            if same:
                same[1].extend(ids)
            else:
                grouped.append((first, list(ids)))
        events += [pass_event(found, ids, f"the supply area of run {run_id}") for found, ids in grouped]

    # expiry: alerts
    read = queries(lines)
    alert_reads = [q for q in read if q[0] == "Q4"]
    if evidence.alerts is None:
        skipped.append({"kind": "expiry", "source": "alerts", "reason": "the alert source did not answer in this run"})
    elif alert_reads:
        when = alert_reads[0][2]
        events.append(all_day(
            "expiry", f"{run_id}-expiry-alerts@greentrace",
            f"Evidence expiry: alert data read {when.date().isoformat()} (run {run_id})",
            when.date() + timedelta(days=settings.ALERTS_VALID_DAYS),
            f"Run {run_id} read the deforestation alerts of its supply area on {tles.iso(when)} (query Q4, "
            f"{alert_reads[0][3]}). They count as current for {settings.ALERTS_VALID_DAYS} days "
            f"(ALERTS_VALID_DAYS, app/geo/settings.py: a setting, not a rule). Run the check again to read them anew."))
    else:
        skipped.append({"kind": "expiry", "source": "alerts", "reason": f"no alert query in {trace_file}"})

    # expiry: reference maps, when a plot was measured on them
    if any(q[0] == "Q10" for q in read) or evidence.reference:
        meta = landcover.load_meta()
        built = date.fromisoformat(meta["built_on"])
        for name, version in (("JRC GFC2020", "v4"), ("Hansen GFC", "v1.12")):
            events.append(all_day(
                "expiry", f"{run_id}-expiry-{name.split()[0].lower()}@greentrace",
                f"Evidence expiry: check that {name} {version} is still current (run {run_id})",
                add_months(built, settings.REFERENCE_MAPS_RECHECK_MONTHS),
                f"Run {run_id} measured plots on {name} {version} (the committed maps were built on {built}, "
                f"data/reference_cells.meta.json). The evidence holds while this version is the current one: "
                f"check for a newer version {settings.REFERENCE_MAPS_RECHECK_MONTHS} months after the maps were built "
                f"(REFERENCE_MAPS_RECHECK_MONTHS, app/geo/settings.py: a setting, not a rule)."))

    # expiry: documents
    for doc_id in dict.fromkeys(params.split("=", 1)[1].strip("'") for q, params, _, _ in read if q == "Q1"):
        declared = declared_date(doc_id)
        if declared is None:
            skipped.append({"kind": "expiry", "source": f"document {doc_id}", "reason": "it declares no date"})
            continue
        day, how = declared
        events.append(all_day(
            "expiry", f"{run_id}-expiry-doc-{doc_id}@greentrace",
            f"Evidence expiry: document {doc_id} (run {run_id})",
            add_months(day, settings.DOCUMENTS_VALID_MONTHS),
            f"Run {run_id} read {doc_id}. {how}. A document counts as current for "
            f"{settings.DOCUMENTS_VALID_MONTHS} months after the date it declares (DOCUMENTS_VALID_MONTHS, "
            f"app/geo/settings.py: a setting, not a rule): ask for a newer one."))

    # follow_up
    if output.get("verdict") == "UNVERIFIABLE":
        plots_of: dict[str, set[str]] = {}
        for plot in evidence.plots:
            plots_of.setdefault(plot["supplier_id"], set()).add(plot["plot_id"])
        letters = output.get("supplier_letters") or []
        if not letters:
            skipped.append({"kind": "follow_up", "reason": "the verdict is UNVERIFIABLE but no finding concerns a "
                                                           "supplier: no letter to follow up"})
        for number, letter in enumerate(letters, start=1):
            concerned = sorted(plots_of.get(letter["supplier_id"], set()) & set(plot_ids))
            about = (f"plot{'s' if len(concerned) > 1 else ''} {', '.join(concerned)}" if concerned
                     else f"letter {number} of {len(letters)}")
            for days in settings.FOLLOW_UP_DAYS:
                events.append(all_day(
                    "follow_up", f"{run_id}-follow-up-{number}-d{days}@greentrace",
                    f"Follow up supplier letter, day {days}: {about} (run {run_id})",
                    run_at.date() + timedelta(days=days),
                    f"Day {days} after run {run_id} (UNVERIFIABLE, {run_at.date()}): if the supplier has not "
                    f"answered, send the letter again (FOLLOW_UP_DAYS, app/geo/settings.py).\n\n{letter['letter']}"))

    answer = {"run_id": run_id, "verdict": output.get("verdict"), "run_date": tles.iso(run_at),
              "run_date_from": run_date_from, "built_at": tles.iso(now),
              "events": [as_json(e) for e in events], "not_scheduled": skipped}
    return answer, events


def pass_events(lat: float, lon: float, *, plot_id: Optional[str] = None, days: float = settings.PASS_DAYS_DEFAULT,
                **options) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(the passes answer, one event per daylight pass): the calendar of GET /api/plots/passes.ics.
    The titles name the plot by its id, or "the plot": coordinates stay out of the Google links."""
    answer = passes.report(lat, lon, days=days, **options)
    events = [pass_event(found, [plot_id] if plot_id else [], "the plot") for found in answer["passes"]]
    return answer, events


def as_json(event: dict[str, Any]) -> dict[str, Any]:
    timed = isinstance(event["start"], datetime)
    shown = tles.iso if timed else (lambda d: d.isoformat())
    return {"kind": event["kind"], "title": event["title"], "start": shown(event["start"]),
            "end": shown(event["end"]), "all_day": not timed, "description": event["description"],
            "uid": event["uid"], "google_calendar_url": ics.google_url(event)}
