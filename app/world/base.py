"""The base world (case 1): seeded, so the same seed always rebuilds the same rows.

1 company, 12 suppliers, 40 plots on a 5 x 8 grid (3 km apart) near Daloa,
deliveries 2021-2025, 30 alerts placed between the plots (none within 900 m of
any plot), a 12-paragraph sustainability report and an 8-paragraph NGO note.
"""
import random
from datetime import date, timedelta
from typing import Any

from app.world import geo
from app.world.texts import base_paragraphs

SEED = 2026
COMPANY_ID = "chocodurable"
FISCAL_YEAR = 2025
YEARS = range(2021, 2026)

# Non-consecutive ids on purpose; the order fixes which plots each supplier owns.
SUPPLIERS = [
    ("F-002", "Blewa", "Union Neyabli"),
    ("F-005", "Dioulabo", "Union Neyabli"),
    ("F-011", "Gnamienso", "Union Neyabli"),
    ("F-007", "Agnibi", "Union Neyabli"),
    ("F-014", "Kodjobra", "Union Tieple"),
    ("F-019", "Lomanie", "Union Tieple"),
    ("F-023", "Sapiadou", "Union Tieple"),
    ("F-026", "Tehirou", "Union Tieple"),
    ("F-031", "Wawassi", "Union Gonzro"),
    ("F-034", "Yobouakro", "Union Gonzro"),
    ("F-038", "Zebrako", "Union Gonzro"),
    ("F-040", "Mandeyo", "Union Gonzro"),
]
PLOTS_PER_SUPPLIER = [4, 4, 4, 4, 3, 3, 3, 3, 3, 3, 3, 3]  # F-007 owns P-013..P-016

GRID_ORIGIN = (6.80, -6.60)
GRID_ROWS, GRID_COLS, GRID_STEP_M = 5, 8, 3000.0
PLOT_JITTER_M, ALERT_JITTER_M = 250.0, 150.0

# Plots that the trap cases rely on: their area and shape are fixed, not drawn.
FIXED_AREA_HA = {
    "P-003": 3.0,   # case 5: alert 300 m outside its polygon
    "P-009": 2.0,   # case 9: 2 ha declaring 30 t
    "P-014": 3.2,   # cases 2-4, 13: the demo plot, 2.4 t a year
    "P-021": 6.0,   # case 7: 6 ha plot declared by a point
    "P-029": 5.0, "P-030": 4.6, "P-031": 4.4,  # F-031, 12 % of the volume (case 8)
    "P-033": 2.0,   # case 6: 2 ha point-declared plot with an alert 300 m away
}
POINT_PLOTS = {"P-006", "P-012", "P-018", "P-024", "P-033", "P-036", "P-039"}
P014_TONNES = 2.4
F031_SHARE = 0.12
CERTIFIED_TARGET = 0.40


def _plot_ids() -> list[tuple[str, str]]:
    owners, number = [], 1
    for (supplier_id, _, _), count in zip(SUPPLIERS, PLOTS_PER_SUPPLIER):
        for _ in range(count):
            owners.append((f"P-{number:03d}", supplier_id))
            number += 1
    return owners


def build_base_world(seed: int = SEED) -> dict[str, Any]:
    rng = random.Random(seed)

    suppliers = [
        {"supplier_id": sid, "company_id": COMPANY_ID, "name": name,
         "cooperative_group": group, "country": "CI"}
        for sid, name, group in SUPPLIERS
    ]

    plots = []
    for index, (plot_id, supplier_id) in enumerate(_plot_ids()):
        row, col = divmod(index, GRID_COLS)
        lat, lon = geo.offset(
            *GRID_ORIGIN,
            row * GRID_STEP_M + rng.uniform(-PLOT_JITTER_M, PLOT_JITTER_M),
            col * GRID_STEP_M + rng.uniform(-PLOT_JITTER_M, PLOT_JITTER_M),
        )
        is_point = plot_id in POINT_PLOTS
        drawn = round(rng.uniform(1.2, 3.8) if is_point else rng.uniform(1.5, 5.5), 2)
        area = FIXED_AREA_HA.get(plot_id, drawn)
        plots.append({
            "plot_id": plot_id, "supplier_id": supplier_id, "lat": lat, "lon": lon,
            "polygon_wkt": "" if is_point else geo.square_wkt(lat, lon, area),
            "area_ha": area, "declared_by": "supplier",
        })

    deliveries = _deliveries(plots, rng)
    _mark_certified(deliveries)

    alerts = _alerts(rng)

    world = {
        "company": [{"company_id": COMPANY_ID, "name": "ChocoDurable SA",
                     "fiscal_year": FISCAL_YEAR, "total_tonnes": 0.0}],
        "suppliers": suppliers,
        "plots": plots,
        "deliveries": deliveries,
        "alerts": alerts,
        "report_paragraphs": base_paragraphs(),
    }
    refresh_company_total(world)
    return world


def _deliveries(plots: list[dict], rng: random.Random) -> list[dict]:
    yields = {p["plot_id"]: rng.uniform(0.35, 0.75) for p in plots}
    rows = []
    for year in YEARS:
        year_rows = []
        for plot in plots:
            tonnes = plot["area_ha"] * yields[plot["plot_id"]] * rng.uniform(0.9, 1.1)
            if plot["plot_id"] == "P-014":
                tonnes = P014_TONNES
            year_rows.append({"supplier_id": plot["supplier_id"], "plot_id": plot["plot_id"],
                              "year": year, "tonnes": tonnes})
        _rescale_f031(year_rows, plots)
        rows += year_rows

    return [{"delivery_id": f"D-{number:04d}", **row, "tonnes": round(row["tonnes"], 2),
             "certified": "no"}
            for number, row in enumerate(rows, start=1)]


def _rescale_f031(year_rows: list[dict], plots: list[dict]) -> None:
    """F-031 weighs exactly 12 % of the year's volume, spread by plot area (case 8)."""
    area = {p["plot_id"]: p["area_ha"] for p in plots if p["supplier_id"] == "F-031"}
    others = sum(r["tonnes"] for r in year_rows if r["supplier_id"] != "F-031")
    target = others * F031_SHARE / (1.0 - F031_SHARE)
    for row in year_rows:
        if row["supplier_id"] == "F-031":
            row["tonnes"] = target * area[row["plot_id"]] / sum(area.values())


def _mark_certified(deliveries: list[dict]) -> None:
    """Certify whole suppliers, in registry order, until about 40 % of the volume (case 17)."""
    total = sum(d["tonnes"] for d in deliveries)
    by_supplier: dict[str, float] = {}
    for d in deliveries:
        by_supplier[d["supplier_id"]] = by_supplier.get(d["supplier_id"], 0.0) + d["tonnes"]
    certified, share = set(), 0.0
    for supplier_id, _, _ in SUPPLIERS:
        next_share = share + by_supplier[supplier_id] / total
        if abs(next_share - CERTIFIED_TARGET) > abs(share - CERTIFIED_TARGET):
            break
        certified.add(supplier_id)
        share = next_share
    for d in deliveries:
        if d["supplier_id"] in certified:
            d["certified"] = "yes"


def _alerts(rng: random.Random) -> list[dict]:
    """30 alerts at midpoints between horizontally adjacent plots, never touching a plot."""
    slots = [(row, col) for row in range(GRID_ROWS) for col in range(GRID_COLS - 1)]
    chosen = sorted(rng.sample(slots, 30))
    alerts = []
    for number, (row, col) in enumerate(chosen, start=101):
        lat, lon = geo.offset(
            *GRID_ORIGIN,
            row * GRID_STEP_M + rng.uniform(-ALERT_JITTER_M, ALERT_JITTER_M),
            (col + 0.5) * GRID_STEP_M + rng.uniform(-ALERT_JITTER_M, ALERT_JITTER_M),
        )
        day = rng.randrange(0, 2830)  # 2018-01-01 .. 2025-09-30
        alerts.append({
            "alert_id": f"A-{number}", "lat": lat, "lon": lon,
            "alert_date": _date_from_offset(day),
            "confidence": rng.choice(["low", "high", "highest"]),
            "area_ha": round(rng.uniform(0.1, 2.0), 2),
            "system": rng.choice(["GLAD-L", "GLAD-S2", "RADD"]),
        })
    return alerts


def _date_from_offset(days: int) -> str:
    return (date(2018, 1, 1) + timedelta(days=days)).isoformat()


def refresh_company_total(world: dict[str, Any]) -> None:
    """The company declares what it bought in the fiscal year, traced or not."""
    company = world["company"][0]
    company["total_tonnes"] = round(
        sum(d["tonnes"] for d in world["deliveries"] if d["year"] == company["fiscal_year"]), 2
    )
