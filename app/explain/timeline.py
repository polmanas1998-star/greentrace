"""The timeline (kit, F1): for each plot a finding is about, what happened when.

Per plot: the tonnes it delivered each year, the alerts inside it with their dates and,
for a plot the reference maps measured, the hectares of tree cover lost each year inside
the outline (all of it, and on 2020 forest only) with the cells behind them. The cut-off
date is there so the eye can put each loss before or after it. Pure: same run, same
timeline; nothing here decides, app/rules.py does.
"""
from typing import Any

from app.checks import geometry, landcover
from app.rules import CUTOFF, Evidence, decide


def timeline(evidence: Evidence) -> dict[str, Any]:
    findings = decide(evidence)["findings"]
    about: dict[str, list[str]] = {}
    for finding in findings:
        for ref in finding["refs"]:
            kind, _, key = ref.partition(":")
            if kind == "plot":
                about.setdefault(key, []).append(finding["code"])
    for plot_id in evidence.reference:
        about.setdefault(plot_id, [])

    first_rows: dict[str, dict] = {}
    for plot in evidence.plots:
        first_rows.setdefault(plot["plot_id"], plot)
    return {"cutoff": CUTOFF,
            "plots": [_plot(evidence, first_rows[plot_id], codes) for plot_id, codes in sorted(about.items())
                      if plot_id in first_rows]}


def _plot(evidence: Evidence, plot: dict, codes: list[str]) -> dict[str, Any]:
    plot_id = plot["plot_id"]
    delivered: dict[str, float] = {}
    for d in evidence.deliveries:
        if d["plot_id"] == plot_id and d["tonnes"] > 0:
            delivered[str(d["year"])] = round(delivered.get(str(d["year"]), 0.0) + d["tonnes"], 2)
    alerts = [{"alert_id": a["alert_id"], "alert_date": a["alert_date"], "confidence": a["confidence"]}
              for a in evidence.alerts or [] if geometry.alert_inside(plot, a)]
    return {
        "plot_id": plot_id, "supplier_id": plot["supplier_id"], "area_ha": plot["area_ha"],
        "declared_by": plot.get("declared_by", "supplier"),
        "findings": sorted(set(codes)),
        "delivered_tonnes_by_year": dict(sorted(delivered.items())),
        "alerts": sorted(alerts, key=lambda a: (a["alert_date"], a["alert_id"])),
        "reference_maps": _maps(plot, evidence.reference[plot_id]) if plot_id in evidence.reference else None,
    }


def _maps(plot: dict, cells: list[dict] | None) -> dict[str, Any]:
    if cells is None:
        return {"covered": False, "reason": "the reference maps did not answer"}
    measured = landcover.measure(plot, cells)
    by_index = {landcover.cell_index(c["lat"], c["lon"]): c for c in cells}
    used = []
    for cell in measured["cells"]:
        row = by_index[landcover.cell_index(cell["lat"], cell["lon"])]
        used.append({**cell, "forest_fraction_2020": row["forest_fraction_2020"], "loss_year": row["loss_year"]})
    return {**measured, "cells": used, "forest_from": landcover.FOREST_FROM}
