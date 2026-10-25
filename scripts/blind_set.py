"""A blind set: trap cases written by an author who never saw the code, the rules or the 21 cases.

    python -m scripts.blind_set brief      # writes data/blind_set/author_brief.md, the only thing the author sees
    python -m scripts.blind_set freeze     # checks data/blind_set/cases.json and writes its sha256 (FROZEN.sha256)
    python -m scripts.blind_set run        # rules alone, sample mode; writes data/bench_blind.json
    python -m scripts.blind_set run --model   # also through the model chain (Meterless Nightshift when set)

The author gets the base world, the three verdicts in plain words and a small language of
changes; each case is the base world plus the changes it lists. `run` refuses to start if
cases.json no longer matches the frozen hash, and an unknown change stops the run instead of
being skipped. Whatever the score, it is written as it comes.
"""
import copy
import hashlib
import io
import json
import sys
from pathlib import Path

from app import orchestrator
from app.memory.fallback import MemoryEngine
from app.pipeline import run_case
from app.world import geo
from app.world.base import build_base_world, refresh_company_total
from app.world.cases import DEFAULT_CLAIM
from app.world.store import DATA_DIR

DIR = DATA_DIR / "blind_set"
CASES_FILE, FROZEN, BRIEF = DIR / "cases.json", DIR / "FROZEN.sha256", DIR / "author_brief.md"
OUT = DATA_DIR / "bench_blind.json"
VERDICTS = ("PROVEN", "CONTRADICTED", "UNVERIFIABLE")
DOCS, SECTIONS = ("rse_2025", "ngo_2025"), ("main", "annex")
SOURCES = ("registry", "documents", "alerts")

VERDICT_TEXT = """\
- **PROVEN**: nothing checked contradicts the promise, and every tonne of the fiscal year is traced to a
  geolocated plot. Proven means "holds on everything that was checked", not "certified".
- **CONTRADICTED**: there is direct evidence, attributable to a plot that delivered to the company, that
  forest was cleared there after 31 December 2020. This is an accusation: it must survive every honest
  attempt to explain it away.
- **UNVERIFIABLE**: the data cannot settle it either way. The evidence is incomplete, inconsistent, too
  weak, or cannot be tied to the company's supply, so the analyst has to ask for more before deciding.

Under the EUDR an UNVERIFIABLE supply cannot be placed on the market until the risk is mitigated, so it is
not a pass. The worst error is a false CONTRADICTED (accusing a clean supplier); the second worst is a
false PROVEN (clearing a supply that should have been questioned)."""

OPS_TEXT = """\
Each change is one JSON object. Distances are in metres. Dates are YYYY-MM-DD.

| op | fields | what it does |
|---|---|---|
| `add_alert` | `alert_id`, `date`, `confidence` (low, high, highest), `system` (RADD, GLAD-L, GLAD-S2), `area_ha`, and either `"plot_id"` + `"where": "inside"`, or `"plot_id"` + `"where": {"outside_m": N}` (N metres east of the plot's edge; for a point-declared plot, of the edge of a disk of its declared area), or `"lat"` + `"lon"` | adds a satellite deforestation alert |
| `remove_all_alerts` | (none) | the alert source answers, with no row at all |
| `source_unavailable` | `source` (registry, documents, alerts) | that source does not answer during the investigation |
| `declare_by_point` | `plot_id` | the plot loses its polygon and is known by its centre point and area only |
| `move_plot` | `plot_id`, `north_m`, `east_m` | moves the plot's point and polygon |
| `swap_lat_lon` | `plot_id` | the registry row has latitude and longitude swapped |
| `set_plot_area` | `plot_id`, `area_ha` | changes the declared area (the polygon is left as it is) |
| `declare_plot_also_by` | `plot_id`, `supplier_id` | a second supplier declares the same plot |
| `remove_plots_of_supplier` | `supplier_id` | the supplier keeps delivering but none of its plots is in the registry |
| `set_delivery_tonnes` | `plot_id`, `year`, `tonnes` | changes that plot's delivery for that year |
| `add_delivery` | `supplier_id`, `plot_id` (or null for untraced volume), `year`, `tonnes`, `certified` (yes, no) | adds a delivery row |
| `stop_deliveries` | `plot_id`, `after_year` | removes that plot's deliveries after that year |
| `add_supplier` | `supplier_id`, `name`, `cooperative_group`, `country`, and optional `plot`: `{plot_id, near_plot, north_m, east_m, area_ha}` (a polygon plot placed relative to an existing plot) | a supplier in the registry; it delivers nothing unless you add deliveries |
| `add_paragraph` | `doc` (rse_2025, ngo_2025), `section` (main, annex), `text` | adds a paragraph at the end of that section |
| `replace_paragraph` | `doc`, `section`, `number`, `text` | rewrites an existing paragraph |

A case may also set `"claim"` to investigate another promise than the default one."""


# ---- the brief the author sees ----------------------------------------------------------------

def _plot_rows(world: dict) -> list[str]:
    tonnes: dict[str, dict[int, float]] = {}
    for d in world["deliveries"]:
        if d["plot_id"]:
            tonnes.setdefault(d["plot_id"], {})[d["year"]] = round(tonnes.get(d["plot_id"], {}).get(d["year"], 0) + d["tonnes"], 2)
    rows = ["| plot | supplier | area (ha) | outline | lat | lon | t 2021 | t 2022 | t 2023 | t 2024 | t 2025 |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in world["plots"]:
        t = tonnes.get(p["plot_id"], {})
        rows.append(f"| {p['plot_id']} | {p['supplier_id']} | {p['area_ha']} | {'polygon' if p['polygon_wkt'] else 'point'} "
                    f"| {p['lat']:.6f} | {p['lon']:.6f} | " + " | ".join(str(t.get(y, '-')) for y in range(2021, 2026)) + " |")
    return rows


def brief() -> str:
    world = build_base_world()
    lines = ["# Write a blind test set for an EUDR investigator", "",
             "You are writing test cases for a system that checks a cocoa importer's deforestation promise for "
             "the EU Deforestation Regulation (EUDR: no forest cleared after 31 December 2020). You have not seen "
             "its code, its rules or its own test cases, and you should not try to guess them: write the cases an "
             "experienced EUDR compliance analyst would use to catch a system that is too quick to accuse or too "
             "quick to clear.", "",
             "## What to return", "",
             "Exactly 10 cases, as one JSON array, nothing else. Each case:", "",
             '```json\n{"id": "B-01", "title": "short name", "changes": [ ... ], "expected": "PROVEN | CONTRADICTED | UNVERIFIABLE", '
             '"why": "two sentences: what an analyst would conclude and why"}\n```', "",
             "- Each case is the base world below plus its 1 to 3 changes, nothing else.",
             "- At least 2 cases of each verdict. Include cases that look guilty but are not, and cases that look clean but are not.",
             "- Decide `expected` yourself, from the definitions below and the regulation, as the analyst would.",
             "- Use only the changes listed in the change language. Invent new ids for new alerts, plots and suppliers (A-5xx, P-5xx, F-5xx).",
             "",
             "## The three verdicts", "", VERDICT_TEXT, "",
             f"## The default promise\n\n\"{DEFAULT_CLAIM}\"\n",
             "## The base world (fictional company, synthetic records)", "",
             f"Company: {world['company'][0]['name']}, fiscal year {world['company'][0]['fiscal_year']}, "
             f"{world['company'][0]['total_tonnes']} t of cocoa. In the base world, nothing contradicts the promise.", "",
             "### Suppliers", "", "| supplier | name | cooperative group | country |", "|---|---|---|---|"]
    lines += [f"| {s['supplier_id']} | {s['name']} | {s['cooperative_group']} | {s['country']} |" for s in world["suppliers"]]
    lines += ["", "### Plots and their deliveries (tonnes per year)", "",
              "A polygon plot is a square of its area around its point. A point plot is known by its centre only.", ""]
    lines += _plot_rows(world)
    lines += ["", "### Satellite alerts already in the alert source", "",
              "| alert | lat | lon | date | confidence | system | area (ha) |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {a['alert_id']} | {a['lat']:.6f} | {a['lon']:.6f} | {a['alert_date']} | {a['confidence']} | {a['system']} | {a['area_ha']} |"
              for a in world["alerts"]]
    lines += ["", "### Documents", ""]
    lines += [f"- `{r['doc_id']}` {r['section']} {r['paragraph_no']}: {r['paragraph_text']}" for r in world["report_paragraphs"]]
    lines += ["", "## The change language", "", OPS_TEXT, ""]
    return "\n".join(lines)


# ---- applying a case ------------------------------------------------------------------------------

def _plot(world: dict, plot_id: str) -> dict:
    for plot in world["plots"]:
        if plot["plot_id"] == plot_id:
            return plot
    raise ValueError(f"unknown plot {plot_id}")


def _reshape(plot: dict) -> None:
    if plot["polygon_wkt"]:
        plot["polygon_wkt"] = geo.square_wkt(plot["lat"], plot["lon"], plot["area_ha"])


def apply(world: dict, change: dict) -> list[str]:
    """Applies one change in place; returns the unavailable sources it adds."""
    op = change.get("op")
    if op == "add_alert":
        if "lat" in change:
            lat, lon = float(change["lat"]), float(change["lon"])
        else:
            plot, where = _plot(world, change["plot_id"]), change["where"]
            if where == "inside":
                lat, lon = geo.offset(plot["lat"], plot["lon"], 20.0, -15.0)
            else:
                edge = (geo.square_side_m(plot["area_ha"]) / 2.0 if plot["polygon_wkt"]
                        else geo.disk_radius_m(plot["area_ha"]))
                lat, lon = geo.offset(plot["lat"], plot["lon"], 0.0, edge + float(where["outside_m"]))
        world["alerts"].append({"alert_id": change["alert_id"], "lat": lat, "lon": lon, "alert_date": change["date"],
                                "confidence": change["confidence"], "area_ha": float(change.get("area_ha", 0.8)),
                                "system": change.get("system", "GLAD-S2")})
    elif op == "remove_all_alerts":
        world["alerts"] = []
    elif op == "source_unavailable":
        if change["source"] not in SOURCES:
            raise ValueError(f"unknown source {change['source']}")
        return [change["source"]]
    elif op == "declare_by_point":
        _plot(world, change["plot_id"])["polygon_wkt"] = ""
    elif op == "move_plot":
        plot = _plot(world, change["plot_id"])
        plot["lat"], plot["lon"] = geo.offset(plot["lat"], plot["lon"], float(change["north_m"]), float(change["east_m"]))
        _reshape(plot)
    elif op == "swap_lat_lon":
        plot = _plot(world, change["plot_id"])
        plot["lat"], plot["lon"] = plot["lon"], plot["lat"]
    elif op == "set_plot_area":
        _plot(world, change["plot_id"])["area_ha"] = float(change["area_ha"])
    elif op == "declare_plot_also_by":
        twin = dict(_plot(world, change["plot_id"]))
        twin["supplier_id"] = change["supplier_id"]
        world["plots"].append(twin)
    elif op == "remove_plots_of_supplier":
        world["plots"] = [p for p in world["plots"] if p["supplier_id"] != change["supplier_id"]]
        for d in world["deliveries"]:
            if d["supplier_id"] == change["supplier_id"]:
                d["plot_id"] = None
    elif op == "set_delivery_tonnes":
        rows = [d for d in world["deliveries"] if d["plot_id"] == change["plot_id"] and d["year"] == int(change["year"])]
        if not rows:
            raise ValueError(f"no delivery of {change['plot_id']} in {change['year']}")
        for d in rows:
            d["tonnes"] = float(change["tonnes"]) / len(rows)
    elif op == "add_delivery":
        number = len(world["deliveries"]) + 1
        world["deliveries"].append({"delivery_id": f"D-B{number:04d}", "supplier_id": change["supplier_id"],
                                    "plot_id": change.get("plot_id"), "year": int(change["year"]),
                                    "tonnes": float(change["tonnes"]), "certified": change.get("certified", "no")})
    elif op == "stop_deliveries":
        world["deliveries"] = [d for d in world["deliveries"]
                               if not (d["plot_id"] == change["plot_id"] and d["year"] > int(change["after_year"]))]
    elif op == "add_supplier":
        world["suppliers"].append({"supplier_id": change["supplier_id"], "company_id": "chocodurable",
                                   "name": change["name"], "cooperative_group": change["cooperative_group"],
                                   "country": change.get("country", "CI")})
        if change.get("plot"):
            spec, anchor = change["plot"], _plot(world, change["plot"]["near_plot"])
            lat, lon = geo.offset(anchor["lat"], anchor["lon"], float(spec["north_m"]), float(spec["east_m"]))
            area = float(spec["area_ha"])
            world["plots"].append({"plot_id": spec["plot_id"], "supplier_id": change["supplier_id"], "lat": lat,
                                   "lon": lon, "polygon_wkt": geo.square_wkt(lat, lon, area), "area_ha": area,
                                   "declared_by": "supplier"})
    elif op in ("add_paragraph", "replace_paragraph"):
        if change["doc"] not in DOCS or change["section"] not in SECTIONS:
            raise ValueError(f"unknown document {change['doc']} {change['section']}")
        rows = [r for r in world["report_paragraphs"] if (r["doc_id"], r["section"]) == (change["doc"], change["section"])]
        if op == "add_paragraph":
            world["report_paragraphs"].append({"doc_id": change["doc"], "section": change["section"],
                                               "paragraph_no": max((r["paragraph_no"] for r in rows), default=0) + 1,
                                               "paragraph_text": change["text"]})
        else:
            target = [r for r in rows if r["paragraph_no"] == int(change["number"])]
            if not target:
                raise ValueError(f"no paragraph {change['doc']} {change['section']} {change['number']}")
            target[0]["paragraph_text"] = change["text"]
    else:
        raise ValueError(f"unknown change {op!r}")
    return []


def build(case: dict) -> dict:
    world = copy.deepcopy(build_base_world())
    unavailable: list[str] = []
    for change in case["changes"]:
        unavailable += apply(world, change)
    refresh_company_total(world)
    world["meta"] = {"case": case["id"], "slug": case["id"], "claim": case.get("claim", DEFAULT_CLAIM),
                     "expected": case["expected"], "unavailable_sources": unavailable}
    return world


# ---- freeze and run ---------------------------------------------------------------------------------

def _digest() -> str:
    return hashlib.sha256(CASES_FILE.read_bytes()).hexdigest()


def load_cases() -> list[dict]:
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    for case in cases:
        if case.get("expected") not in VERDICTS:
            raise ValueError(f"{case.get('id')}: expected must be one of {VERDICTS}")
        build(case)  # every change must apply
    return cases


def freeze() -> int:
    cases = load_cases()
    FROZEN.write_text(f"{_digest()}  cases.json\n", encoding="utf-8")
    print(f"{len(cases)} cases frozen: {_digest()}")
    return 0


def run(use_model: bool) -> int:
    frozen = FROZEN.read_text(encoding="utf-8").split()[0]
    if frozen != _digest():
        print("cases.json does not match FROZEN.sha256: refusing to run")
        return 2
    if use_model:
        from dotenv import load_dotenv
        load_dotenv()
    cases, results = load_cases(), []
    for case in cases:
        world = build(case)
        orchestrator.build_case = lambda number, _w=world: copy.deepcopy(_w)
        run_id = f"blind-{case['id'].lower()}"
        out = run_case({"run_id": run_id, "input": {"claim": world["meta"]["claim"]},
                        "options": {"sample_mode": True, "case": 0, "use_model": use_model}},
                       stream=io.StringIO(), memory_engine=MemoryEngine())
        gate = orchestrator.RUNS[run_id].ctx.model
        calls = list(gate.calls) if gate is not None else []
        signed = [c["meterless_receipt"] for c in calls if c.get("meterless_receipt")]
        got = out.get("verdict")
        results.append({"id": case["id"], "title": case["title"], "expected": case["expected"], "got": got,
                        "ok": got == case["expected"], "verdict_sha256": out.get("verdict_sha256"),
                        "model_calls": sum(c["provider"] != "rules" for c in calls),
                        "receipts_valid": sum(r["signature_valid"] for r in signed), "receipts": len(signed)})
        print(f"{'ok  ' if got == case['expected'] else 'MISS'} {case['id']} {case['title'][:44]:<44} "
              f"expected {case['expected']:<13} got {got}")
    score = sum(r["ok"] for r in results)
    false_accusations = sum(r["got"] == "CONTRADICTED" and r["expected"] != "CONTRADICTED" for r in results)
    false_clears = sum(r["got"] == "PROVEN" and r["expected"] != "PROVEN" for r in results)
    print(f"{score}/{len(results)}, false accusations {false_accusations}, false clears {false_clears}")
    report = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    report.update({"cases_sha256": frozen, "author": report.get("author", "")})
    report["model" if use_model else "rules_alone"] = {
        "score": score, "of": len(results), "false_accusations": false_accusations,
        "false_clears": false_clears, "results": results}
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


def main(argv: list[str]) -> int:
    command = argv[0] if argv else ""
    if command == "brief":
        DIR.mkdir(parents=True, exist_ok=True)
        BRIEF.write_text(brief(), encoding="utf-8")
        print(f"wrote {BRIEF}")
        return 0
    if command == "freeze":
        return freeze()
    if command == "run":
        return run("--model" in argv)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
