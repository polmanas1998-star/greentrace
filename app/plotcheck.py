"""Check an analyst's own plot file: every plot measured on the reference maps, judged by app/rules.py.

One batch per file. For each plot that could be read (app/plotfile.py): query Q10 reads the map
cells around it (EU JRC forest in 2020, Hansen/UMD loss year), app/checks/landcover.py measures
what lies inside the outline, and app/rules.check_plot gives the verdict: CONTRADICTED (rule 2 of
kit v7: at least 0.5 ha or 10 % of the plot that the EU map counted as forest in 2020 was lost
after 2020), UNVERIFIABLE (rule 1 or 3: no map here, a point without its outline, a smaller or
non-forest loss), or no issue found. Every plot in the file is treated as producing for the
analyst up to the year of the check: a loss after 2020 always counts.

The results come sorted by risk, with the suppliers ranked the same way, one sentence per plot,
the cells behind each verdict, and the exports: the GeoJSON of the plots with no issue found, a
CSV of every plot, and an audit package whose receipt python verify.py re-derives offline.
"""
import csv
import hashlib
import io
import json
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from shapely import wkt
from shapely.geometry import mapping

from app import plotfile
from app.canonical import sha256_of
from app.checks import gfw, landcover
from app.queries import Answer, DataGate, SourceUnavailable
from app.receipt import RECEIPT_VERSION, ZIP_DATE, rules_sha256
from app.rules import CONTRADICTED, RULES_VERSION, UNVERIFIABLE, check_plot
from app.tracing import LOG_DIR, now_utc, trace

NO_ISSUE = "NO_ISSUE"
ORDER = (CONTRADICTED, UNVERIFIABLE, NO_ISSUE)
KEPT_BATCHES = 50
SOURCES = "EU JRC Global Forest Cover 2020 v4 (forest in 2020) + Hansen/UMD/Google/USGS/NASA GFC v1.12 (loss year)"


@dataclass
class Batch:
    batch_id: str
    producing_until: int
    plots: list[dict]
    errors: list[dict]
    warnings: list[str]
    answers: list = field(default_factory=list)   # the DataGate's Q10 answers, in order
    results: list[dict] = field(default_factory=list)
    output: dict = field(default_factory=dict)
    log_dir: Optional[Path] = None


BATCHES: "OrderedDict[str, Batch]" = OrderedDict()


def check(geojson: Any, batch_id: str, engine: Any, *, year: Optional[int] = None, remote: Optional[gfw.Tiles] = None,
          log: Optional[Callable[[dict], Any]] = None, log_dir: Optional[Path] = None) -> dict[str, Any]:
    """Read the file, check every plot, keep the batch for its exports. engine: SQLiteEngine or ZetarisEngine;
    remote: Global Forest Watch tiles for the plots the reference cells do not cover (live mode only)."""
    plots, errors, warnings = plotfile.read(geojson)
    year = year or datetime.now(timezone.utc).year
    log = log or (lambda record: trace(record, log_dir=log_dir))
    gate = DataGate(engine, batch_id, log=log)
    batch = Batch(batch_id, year, plots, errors, warnings, log_dir=log_dir)
    _step(log, batch_id, "receive_plot_file", f"{len(plots) + len(errors)} feature(s)",
          f"{len(plots)} plot(s) read, {len(errors)} refused, {len(warnings)} warning(s); producing up to {year}")

    for plot in plots:
        cells, source = None, None
        if plot["area_ha"] is not None:
            lat_min, lat_max, lon_min, lon_max = landcover.plot_box(plot)
            try:
                answer = gate.ask("Investigator", "Q10", lat_min=lat_min, lat_max=lat_max,
                                  lon_min=lon_min, lon_max=lon_max)
                cells, source = answer.rows, len(gate.answers) - 1
            except SourceUnavailable:
                pass  # no answer: rule 1 says the maps did not answer
            if remote is not None and (cells is None or not landcover.measure(plot, cells)["covered"]):
                cells, source = _read_gfw(remote, plot, gate, batch_id, log)
        result = _result(plot, cells, source, year, gate.answers[source] if source is not None else None,
                         remote is None)
        batch.results.append(result)
        _step(log, batch_id, "check_plot", f"{plot['plot_id']} ({plot['shape']}, {plot['area_ha']} ha)",
              f"{result['verdict']}: {result['sentence']}")
    batch.answers = list(gate.answers)
    batch.output = _output(batch)
    _step(log, batch_id, "rank_plots", f"{len(batch.results)} plot(s)",
          ", ".join(f"{n} {v}" for v, n in batch.output["summary"]["plots"].items()), agent="Writer")
    BATCHES[batch_id] = batch
    while len(BATCHES) > KEPT_BATCHES:
        BATCHES.popitem(last=False)
    return batch.output


def _read_gfw(remote: gfw.Tiles, plot: dict, gate: DataGate, batch_id: str,
              log: Callable[[dict], Any]) -> tuple[Optional[list[dict]], Optional[int]]:
    """Outside the reference cells: the loss year from the public GFW tiles, kept as one more answer."""
    try:
        reading = remote.cells(*landcover.plot_box(plot))
    except gfw.GfwUnavailable as error:
        _step(log, batch_id, "read_gfw_tiles", plot["plot_id"], f"unavailable: {error}", status="degraded")
        return None, None
    tiles = ", ".join(f"{t['z']}/{t['x']}/{t['y']}" for t in reading.tiles)
    gate.answers.append(Answer("GFW", f"{gfw.SOURCE}: {remote.url.split('?')[0]} tiles {tiles}", reading.cells,
                               "Investigator"))
    _step(log, batch_id, "read_gfw_tiles", plot["plot_id"],
          f"{len(reading.cells)} cells from {len(reading.tiles)} tile(s) ({sum(t['cached'] for t in reading.tiles)} "
          f"from the cache); no EU 2020 forest map here", provider="gfw")
    return reading.cells, len(gate.answers) - 1


def _result(plot: dict, cells: Optional[list[dict]], source: Optional[int], year: int, answer: Optional[Answer],
            sample: bool) -> dict[str, Any]:
    findings = check_plot(plot, cells, {year})
    finding = findings[0] if findings else None
    measured = landcover.measure(plot, cells) if cells is not None and plot["area_ha"] is not None else None
    verdict = finding.verdict if finding else NO_ISSUE
    if finding:
        sentence = finding.detail[0].upper() + finding.detail[1:] + "."
    elif not measured["forest_map"]:
        sentence = (f"No tree cover loss after 2020 inside {plot['plot_id']} on the Hansen/UMD map "
                    f"(read from Global Forest Watch; no EU 2020 forest map here).")
    else:
        sentence = (f"No forest loss after 2020 inside {plot['plot_id']} on the reference maps "
                    f"({measured['forest_2020_ha']:g} ha of EU 2020 forest inside the outline).")
    note = None
    if finding and finding.code == "no_reference_data" and sample:
        note = ("Sample mode reads only the committed maps around Soubre, Cote d'Ivoire, with no network call; "
                "live mode reads the Hansen/UMD loss anywhere from Global Forest Watch tiles.")
    maps = (gfw.SOURCE if answer is not None and answer.query_id == "GFW" else SOURCES)
    return {
        "plot_id": plot["plot_id"], "supplier": plot["supplier_id"], "shape": plot["shape"],
        "area_ha": plot["area_ha"], "centre": [plot["lat"], plot["lon"]],
        "verdict": verdict, "rule": finding.rule if finding else 4, "code": finding.code if finding else None,
        "sentence": sentence,
        "measure": None if measured is None else {
            k: measured.get(k) for k in ("covered", "reason", "forest_map", "forest_2020_ha", "loss_after_2020_ha",
                                         "loss_ha_by_year", "forest_loss_ha_by_year")}
        | {"cells": len(measured["cells"])},
        "evidence": {"query": answer.query_id, "answer": source, "maps": maps} if answer is not None else None,
        "note": note,
        "properties": plot["properties"],
    }


def _risk(result: dict) -> tuple:
    lost = (result["measure"] or {}).get("loss_after_2020_ha") or 0.0
    return ORDER.index(result["verdict"]), -lost, result["plot_id"]


def _output(batch: Batch) -> dict[str, Any]:
    results = sorted(batch.results, key=_risk)
    for number, result in enumerate(results, start=1):
        result["risk_rank"] = number
    counts = {v: sum(1 for r in results if r["verdict"] == v) for v in ORDER}
    return {
        "batch_id": batch.batch_id,
        "producing_until": batch.producing_until,
        "summary": {"plots": counts, "refused": len(batch.errors),
                    "sentence": _summary(counts, len(batch.errors))},
        "plots": results,
        "suppliers": suppliers(results),
        "errors": batch.errors,
        "warnings": batch.warnings,
        "rules_version": RULES_VERSION,
        "exports": {name: f"/api/check-plots/{batch.batch_id}/{name}"
                    for name in ("plots_no_issue_found.geojson", "findings.csv", "receipt", "package")},
        "seal": seal(batch.results),
        "log_file": f"logs/{batch.batch_id}.jsonl",
    }


def _summary(counts: dict, refused: int) -> str:
    parts = [f"{counts[CONTRADICTED]} plot(s) contradict a deforestation-free claim",
             f"{counts[UNVERIFIABLE]} cannot be verified", f"{counts[NO_ISSUE]} show no issue"]
    return "; ".join(parts) + (f"; {refused} feature(s) could not be read." if refused else ".")


def suppliers(results: list[dict]) -> list[dict]:
    """The analyst's suppliers, ranked: any contradicted plot first, then any unverifiable, then by hectares lost."""
    rows: dict[str, dict] = {}
    for r in results:
        row = rows.setdefault(r["supplier"], {"supplier": r["supplier"], "plots": 0, CONTRADICTED: 0,
                                               UNVERIFIABLE: 0, NO_ISSUE: 0, "forest_lost_after_2020_ha": 0.0})
        row["plots"] += 1
        row[r["verdict"]] += 1
        if r["verdict"] == CONTRADICTED:
            row["forest_lost_after_2020_ha"] += (r["measure"] or {}).get("loss_after_2020_ha") or 0.0
    ranked = sorted(rows.values(), key=lambda s: (0 if s[CONTRADICTED] else 1 if s[UNVERIFIABLE] else 2,
                                                  -s["forest_lost_after_2020_ha"], -s[CONTRADICTED],
                                                  -s[UNVERIFIABLE], s["supplier"]))
    for number, row in enumerate(ranked, start=1):
        row["forest_lost_after_2020_ha"] = round(row["forest_lost_after_2020_ha"], 4)
        row["rank"] = number
        row["why"] = (f"Rank {number}: {row[CONTRADICTED]} contradicted, {row[UNVERIFIABLE]} unverifiable and "
                      f"{row[NO_ISSUE]} clear plot(s) of {row['plots']}"
                      + (f"; {row['forest_lost_after_2020_ha']:g} ha of 2020 forest lost on the contradicted ones"
                         if row[CONTRADICTED] else "") + ".")
    return ranked


def seal(results: list[dict]) -> str:
    """The batch's verdicts, in file order: what the receipt must re-derive."""
    return sha256_of([[r["plot_id"], r["verdict"], r["code"], r["rule"]] for r in results])


# ---- exports --------------------------------------------------------------------------------------------

def no_issue_geojson(batch: Batch) -> dict[str, Any]:
    by_id = {p["plot_id"]: p for p in batch.plots}
    features = []
    for r in batch.output["plots"]:
        if r["verdict"] != NO_ISSUE:
            continue
        plot = by_id[r["plot_id"]]
        geometry = (mapping(wkt.loads(plot["polygon_wkt"])) if plot["polygon_wkt"]
                    else {"type": "Point", "coordinates": [plot["lon"], plot["lat"]]})
        features.append({"type": "Feature", "geometry": _plain(geometry),
                         "properties": {**plot["properties"], "greentrace_status": "no issue found by GreenTrace",
                                        "greentrace_checked": SOURCES, "greentrace_batch": batch.batch_id}})
    return {"type": "FeatureCollection", "features": features}


def findings_csv(batch: Batch) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["risk_rank", "plot_id", "supplier", "verdict", "rule", "code", "sentence", "area_ha",
                     "forest_2020_ha", "forest_lost_after_2020_ha", "tree_cover_loss_by_year", "cells", "sources"])
    for r in batch.output["plots"]:
        m = r["measure"] or {}
        writer.writerow([r["risk_rank"], r["plot_id"], r["supplier"], r["verdict"], r["rule"], r["code"] or "",
                         r["sentence"], r["area_ha"], m.get("forest_2020_ha"), m.get("loss_after_2020_ha"),
                         "; ".join(f"{y}: {ha}" for y, ha in (m.get("loss_ha_by_year") or {}).items()),
                         m.get("cells"), SOURCES if r["evidence"] else ""])
    return out.getvalue()


def receipt(batch: Batch) -> dict[str, Any]:
    sources = [{"index": i, "query_id": a.query_id, "agent": a.agent, "sql": a.sql, "rows": a.rows,
                "rows_sha256": a.rows_sha256, "row_sha256": [sha256_of(row) for row in a.rows]}
               for i, a in enumerate(batch.answers)]
    return {
        "receipt_version": RECEIPT_VERSION, "kind": "plot_check", "batch_id": batch.batch_id,
        "producing_until": batch.producing_until, "rules_version": RULES_VERSION, "rules_sha256": rules_sha256(),
        "plots": [{k: v for k, v in p.items() if k != "properties"} for p in batch.plots],
        "data_sha256": sha256_of([{"query_id": s["query_id"], "rows_sha256": s["rows_sha256"]} for s in sources]),
        "sources": sources,
        "results": [{"plot_id": r["plot_id"], "verdict": r["verdict"], "code": r["code"], "rule": r["rule"],
                     "answer": (r["evidence"] or {}).get("answer")} for r in batch.results],
        "seal": batch.output["seal"],
    }


def package_zip(batch: Batch) -> bytes:
    trace_file = (batch.log_dir or LOG_DIR) / f"{batch.batch_id}.jsonl"
    files = {
        "results.json": _json(batch.output),
        "findings.csv": findings_csv(batch).encode("utf-8-sig"),
        "plots_no_issue_found.geojson": _json(no_issue_geojson(batch)),
        "receipt.json": _json(receipt(batch)),
        "trace.jsonl": trace_file.read_bytes() if trace_file.exists() else b"",
    }
    manifest = {"batch_id": batch.batch_id, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "seal": batch.output["seal"], "verify": "python verify.py receipt.json",
                "human_review": "Required before any decision.",
                "files": [{"name": n, "bytes": len(d), "sha256": hashlib.sha256(d).hexdigest()}
                          for n, d in files.items()]}
    files["manifest.json"] = _json(manifest)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(zipfile.ZipInfo(name, date_time=ZIP_DATE), data, compress_type=zipfile.ZIP_DEFLATED)
    return buffer.getvalue()


def _json(value: Any) -> bytes:
    return (json.dumps(value, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def _plain(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def _step(log: Callable[[dict], Any], batch_id: str, action: str, given: str, said: str,
          agent: str = "Investigator", status: str = "success", provider: str = "code") -> None:
    log({"timestamp": now_utc(), "run_id": batch_id, "agent_name": agent, "action": action,
         "input_summary": given[:300], "output_summary": said[:300], "target_agent": None, "confidence": None,
         "retry_count": 0, "status": status, "provider": provider, "model": None, "rows": None})
