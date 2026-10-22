"""What the analyst takes away from a run: plot file, supplier letters, findings table, evidence file.

Pure functions of the evidence and the output: same run, same text. Their words are
chosen with care. GreenTrace helps an analyst review a supplier file; it does not
certify anything, so the plot file says "no issue found by GreenTrace", never that a
plot may be declared or is compliant, and a supplier letter reports what our checks
found and asks for documents, never accuses.
"""
import csv
import io
import re
from typing import Any

from shapely import wkt
from shapely.geometry import mapping

from app.rules import Evidence

NO_ISSUE = "no issue found by GreenTrace"
CHECKS = "alerts after 2020-12-31, coordinates, polygon, yield, double declaration"
ALERTS_MISSING = {"source_unavailable", "no_independent_source"}  # no plot could be checked for alerts
DATE = "[date]"
DECIMALS = 6  # EUDR geolocation precision

_ASK = re.compile(r"^Ask (?:the supplier|both suppliers|every supplier without plots|F-\d{3}) "
                  r"(?P<verb>for|to|which) (?P<rest>.+?)(?:, and hold its deliveries)?\.$")


# ---- the plot file ------------------------------------------------------------------------

def eudr_geojson(evidence: Evidence, decision: dict[str, Any]) -> dict[str, Any]:
    """The delivering plots no finding touches, as GeoJSON, and every other one with its reason."""
    year = evidence.company["fiscal_year"]
    delivering = sorted({d["plot_id"] for d in evidence.deliveries
                         if d["plot_id"] and d["year"] == year and d["tonnes"] > 0})
    reasons: dict[str, list[str]] = {}
    for finding in decision["findings"]:
        for kind, key in (ref.split(":", 1) for ref in finding["refs"] if ":" in ref):
            if kind == "plot":
                reasons.setdefault(key, []).append(finding["detail"])
            elif kind == "supplier":
                for plot in evidence.plots:
                    if plot["supplier_id"] == key:
                        reasons.setdefault(plot["plot_id"], []).append(f"supplier {key}: {finding['detail']}")
        if finding["code"] in ALERTS_MISSING:
            for plot_id in delivering:
                reasons.setdefault(plot_id, []).append(f"not checked for alerts: {finding['detail']}")
    for admission in evidence.extracted.admissions:
        for plot_id in admission.get("plots") or []:
            reasons.setdefault(plot_id, []).append(
                f"named in the company's own report ({admission['doc']} {admission['section']} "
                f"paragraph {admission['paragraph_no']})")

    first_rows = {}
    for plot in evidence.plots:
        first_rows.setdefault(plot["plot_id"], plot)
    features = [_feature(first_rows[plot_id]) for plot_id in delivering
                if plot_id in first_rows and plot_id not in reasons]
    excluded = [{"plot": plot_id, "reason": "; ".join(dict.fromkeys(reasons[plot_id]))}
                for plot_id in delivering if plot_id in reasons]
    return {
        "plots_no_issue_found": {"type": "FeatureCollection", "features": features},
        "excluded": excluded,
    }


def _feature(plot: dict) -> dict[str, Any]:
    if plot["polygon_wkt"]:
        geometry = _rounded(mapping(wkt.loads(plot["polygon_wkt"])))
    else:
        geometry = {"type": "Point", "coordinates": [round(plot["lon"], DECIMALS), round(plot["lat"], DECIMALS)]}
    return {"type": "Feature", "geometry": geometry,
            "properties": {"plot_id": plot["plot_id"], "supplier_id": plot["supplier_id"],
                           "area_ha": plot["area_ha"], "status": NO_ISSUE, "checked": CHECKS}}


def _rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, DECIMALS)
    if isinstance(value, (list, tuple)):
        return [_rounded(item) for item in value]
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    return value


# ---- supplier letters ------------------------------------------------------------------------

def supplier_letters(evidence: Evidence, output: dict[str, Any]) -> list[dict[str, str]]:
    """One draft letter per supplier a finding concerns: what our checks found, what we would need."""
    year = evidence.company["fiscal_year"]
    plots_of: dict[str, set[str]] = {}
    for plot in evidence.plots:
        plots_of.setdefault(plot["supplier_id"], set()).add(plot["plot_id"])
    untraced = _untraced_tonnes(evidence)

    points: dict[str, list[str]] = {}
    for finding in output.get("findings", []):
        named = {e["id"] for e in finding["evidence"] if e["source"] == "supplier"}
        plots = {e["id"] for e in finding["evidence"] if e["source"] == "plot"}
        for supplier_id in sorted(plots_of):
            if supplier_id in named or plots & plots_of[supplier_id]:
                points.setdefault(supplier_id, []).append(finding["finding"])
    for supplier_id, tonnes in untraced.items():
        points.setdefault(supplier_id, []).append(
            f"the {tonnes:g} t you delivered in {year} are not linked to any plot in our registry")

    names = {s["supplier_id"]: s["name"] for s in evidence.suppliers}
    letters = []
    for supplier_id in sorted(points):
        requests = _requests(output.get("recommended_actions", []), supplier_id,
                             plots_of.get(supplier_id, set()), supplier_id in untraced)
        letters.append({"supplier_id": supplier_id, "name": names.get(supplier_id, supplier_id),
                        "letter": _letter(supplier_id, names.get(supplier_id, supplier_id), year,
                                          list(dict.fromkeys(points[supplier_id])), requests)})
    return letters


def _untraced_tonnes(evidence: Evidence) -> dict[str, float]:
    known, year = {p["plot_id"] for p in evidence.plots}, evidence.company["fiscal_year"]
    totals: dict[str, float] = {}
    for d in evidence.deliveries:
        if d["year"] == year and d["plot_id"] not in known and d["tonnes"] > 0:
            totals[d["supplier_id"]] = round(totals.get(d["supplier_id"], 0.0) + d["tonnes"], 2)
    return dict(sorted(totals.items()))


def _requests(actions: list[str], supplier_id: str, plots: set[str], untraced: bool) -> list[str]:
    """The analyst's recommended actions, turned into what to ask this supplier; internal steps left out."""
    requests = []
    for action in actions:
        ours = supplier_id in action or any(re.search(rf"\b{p}\b", action) for p in plots)
        ours = ours or (untraced and "every supplier without plots" in action)
        match = _ASK.match(action)
        if not (ours and match):
            continue
        rest = match["rest"].replace("its ", "your ")
        line = {"for": rest, "to": f"please {rest}", "which": f"which {rest}?"}[match["verb"]]
        requests.append(line[0].upper() + line[1:] + ("" if line.endswith("?") else "."))
    return requests


def _letter(supplier_id: str, name: str, year: int, points: list[str], requests: list[str]) -> str:
    lines = [
        "DRAFT prepared by GreenTrace, to be reviewed by our compliance team before sending.",
        "",
        DATE,
        "",
        f"Dear {name} ({supplier_id}),",
        "",
        f"As part of our review of the cocoa we bought in {year}, our checks found the following points "
        "about the plots and volumes in our records for you:",
        "",
        *[f"- {point[0].upper() + point[1:]}." for point in points],
        "",
        "These points come from public satellite alerts and from our own registry. They may have a simple "
        "explanation, and we would like to understand them with you.",
    ]
    if requests:
        lines += ["", "To complete our file, could you send us:", "", *[f"- {r}" for r in requests]]
    lines += ["", "Thank you for your help.", "", "Kind regards,", "", "[name, compliance team]"]
    return "\n".join(lines) + "\n"


# ---- findings table and evidence file -------------------------------------------------------------

def findings_csv(output: dict[str, Any]) -> str:
    """One row per claim part, then one per finding, with where each comes from."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["row", "claim_part", "verdict", "rule", "detail", "sources", "checked_by", "refutations"])
    for part in output.get("parts", []):
        writer.writerow(["part", part["claim_part"], part["verdict"], part["rule"], "; ".join(part["codes"]),
                         "", "", ""])
    for f in output.get("findings", []):
        writer.writerow(["finding", f["claim_part"], f["verdict"], f["rule"], f["finding"],
                         "; ".join(f"{e['source']}:{e['id']}" for e in f["evidence"]),
                         "; ".join(f["checked_by"]),
                         "; ".join(f"{t['id']} {t['result']}" for t in f["refutations_tried"])])
    return out.getvalue()


def evidence_markdown(output: dict[str, Any]) -> str:
    """The evidence file an analyst files: verdict, why, what would change it, and its fingerprints."""
    lines = [f"# GreenTrace evidence file: {output['verdict']}", "",
             f"Run `{output['run_id']}`, {output['mode']} data"
             + (", rule fallbacks only (degraded)" if output.get("degraded") else "") + ".", "",
             output["summary"], "", f"**Human review:** {output['human_review']}", "",
             "## Verdict by claim part", "", "| Claim part | Verdict | Rule | Findings |", "|---|---|---|---|"]
    lines += [f"| {p['claim_part']} | {p['verdict']} | {p['rule']} | {', '.join(p['codes']) or 'none'} |"
              for p in output["parts"]]
    lines += ["", "## Findings", ""]
    for f in output["findings"] or [{"finding": None}]:
        if f["finding"] is None:
            lines.append("No finding: every check passed.")
            break
        lines += [f"- **{f['verdict']}** (rule {f['rule']}, {f['claim_part']}): {f['finding']}.",
                  f"  Sources: {', '.join(e['source'] + ' ' + e['id'] for e in f['evidence'])}. "
                  f"Checked by: {', '.join(f['checked_by'])}."]
        lines += [f"  Refutation {t['id']} {t['result']}: {t['detail']}." for t in f["refutations_tried"]]
    lines += ["", "## What would change the verdict", "", output.get("flip", "")]
    for tried in output.get("flip_tried", []):
        change = tried["change"]
        lines.append(f"- {change['entity']} {change['field']}: {change['from']} -> {change['to']} "
                     f"gives {tried['verdict']}")
    for title, key in (("Not tested", "untested_factors"), ("Ask for", "recommended_actions"),
                       ("Limits", "limitations")):
        if output.get(key):
            lines += ["", f"## {title}", "", *[f"- {item}" for item in output[key]]]
    export = output.get("eudr_export")
    if export:
        lines += ["", "## Plot file", "",
                  f"{len(export['plots_no_issue_found']['features'])} delivering plot(s) with {NO_ISSUE}; "
                  f"{len(export['excluded'])} left out:"]
        lines += [f"- {e['plot']}: {e['reason']}" for e in export["excluded"]]
    fingerprints = output.get("fingerprints", {})
    lines += ["", "## Fingerprints", "", f"- verdict_sha256: `{output.get('verdict_sha256')}`"]
    lines += [f"- {key}: `{value}`" for key, value in fingerprints.items()]
    return "\n".join(lines) + "\n"
