"""What the supplier registry says about volumes, yields and who declared what."""
from collections import defaultdict

MAX_PLAUSIBLE_T_PER_HA = 3.0  # cocoa gives 0.3-0.7 t/ha; above 3 t/ha the volume cannot come from the plot
FULL_COVERAGE = 0.99


def delivery_years(deliveries: list[dict]) -> dict[str, set[int]]:
    """plot_id -> years in which it delivered something."""
    years: dict[str, set[int]] = defaultdict(set)
    for d in deliveries:
        if d["plot_id"] is not None and d["tonnes"] > 0:
            years[d["plot_id"]].add(d["year"])
    return years


def delivering_suppliers(deliveries: list[dict], since: int) -> set[str]:
    return {d["supplier_id"] for d in deliveries if d["year"] >= since and d["tonnes"] > 0}


def traced_share(company: dict, deliveries: list[dict], plots: list[dict]) -> float:
    """Share of the fiscal year's declared volume traced to a plot of the registry."""
    known = {p["plot_id"] for p in plots}
    traced = sum(d["tonnes"] for d in deliveries
                 if d["year"] == company["fiscal_year"] and d["plot_id"] in known)
    return traced / company["total_tonnes"] if company["total_tonnes"] else 0.0


def impossible_yields(plots: list[dict], deliveries: list[dict]) -> list[tuple[str, int, float]]:
    """(plot_id, year, t/ha) for every delivery a plot of that size cannot have produced."""
    area = {p["plot_id"]: p["area_ha"] for p in plots if p["area_ha"]}
    found = []
    for d in deliveries:
        if d["plot_id"] in area and d["tonnes"] / area[d["plot_id"]] > MAX_PLAUSIBLE_T_PER_HA:
            found.append((d["plot_id"], d["year"], round(d["tonnes"] / area[d["plot_id"]], 2)))
    return sorted(found)


def declared_twice(plots: list[dict]) -> dict[str, list[str]]:
    """plot_id -> suppliers, for every plot declared by more than one supplier."""
    owners: dict[str, list[str]] = defaultdict(list)
    for p in plots:
        owners[p["plot_id"]].append(p["supplier_id"])
    return {plot_id: sorted(s) for plot_id, s in sorted(owners.items()) if len(s) > 1}
