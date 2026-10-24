"""The time slider: what the map rule would have said about a plot at the end of a given year.

For year Y, every Hansen/UMD loss after Y is taken out of the plot's cells (as if the maps stopped at Y),
and the rule of app/rules.py (check_plot, then map_check: at least 0.5 ha or 10 % of the plot, on land the
EU map counted as forest in 2020, lost after 2020) judges what is left, with the plot producing up to Y.
The verdict is the rule's, unchanged; nothing here decides. No finding is PROVEN (rule 4), on this plot's
maps only: it is not a verdict on a whole claim.

The cells are the ones committed in data/reference_cells.csv.gz (EU JRC GFC2020 v4, Hansen GFC v1.12), read
offline in every mode; elsewhere the rule says "no reference map for this area yet". The slider runs on these
embedded maps, not on Google Earth Engine (which needs a registered Google Cloud account). Limits, said in
every answer: Hansen v1.12 maps loss up to 2024, so a later year shows the 2024 state; and a year is the 2024
map cut at that year, not the map as it was published then (Hansen reprocesses past years).
"""
from typing import Any

from app.checks import landcover
from app.geo import settings
from app.rules import MAP_MIN_HA, MAP_MIN_SHARE, PROVEN, check_plot

HANSEN_LAST_YEAR = 2024  # Hansen v1.12: loss 2001-2024 (data/reference_cells.meta.json)
CUT_NOTE = "the Hansen v1.12 map cut at the end of {year}, not the map as it was published in {year}"
AFTER_LAST = "Hansen v1.12 maps loss up to 2024: {year} shows the 2024 state"


def check_year(year: Any) -> int:
    if isinstance(year, bool) or not isinstance(year, int) or not (
            settings.SLIDER_FIRST_YEAR <= year <= settings.SLIDER_LAST_YEAR):
        raise ValueError(f"year must be between {settings.SLIDER_FIRST_YEAR} and {settings.SLIDER_LAST_YEAR}")
    return year


def cut_at(cells: list[dict], year: int) -> list[dict]:
    """The cells as a map that stopped at the end of `year` would show them: later loss is no loss yet."""
    return [c if c["loss_year"] <= year else {**c, "loss_year": 0} for c in cells]


def verdict_at(plot: dict, year: int, cells: list[dict] | None = None) -> dict[str, Any]:
    year = check_year(year)
    if cells is None:
        cells = landcover.read_cells(*landcover.plot_box(plot))
    shown = cut_at(cells, year)
    findings = check_plot(plot, shown, {year})
    finding = findings[0] if findings else None
    measured = landcover.measure(plot, shown) if plot["area_ha"] is not None else None
    lost = None
    if measured and measured["covered"] and measured["forest_map"]:
        lost = round(sum((ha for y, ha in measured["forest_loss_ha_by_year"].items()
                          if int(y) > landcover.CUTOFF_YEAR), 0.0), 4)
    notes = [CUT_NOTE.format(year=year)] + ([AFTER_LAST.format(year=year)] if year > HANSEN_LAST_YEAR else [])
    return {
        "plot_id": plot["plot_id"],
        "year": year,
        "loss_after_cutoff_ha": lost,  # EU 2020 forest lost from 2021 to `year` inside the plot (None: no map)
        "verdict": finding.verdict if finding else PROVEN,
        "rule": finding.rule if finding else 4,
        "code": finding.code if finding else None,
        "detail": finding.detail if finding else (
            f"no loss after 2020 on the reference maps inside {plot['plot_id']} up to {year}"),
        "threshold_ha": round(min(MAP_MIN_HA, MAP_MIN_SHARE * plot["area_ha"]), 4) if plot["area_ha"] else None,
        "scope": "the map rule of app/rules.py on this plot only, not a verdict on a whole claim",
        "maps": "EU JRC GFC2020 v4 + Hansen/UMD GFC v1.12, data/reference_cells.csv.gz (embedded, offline)",
        "notes": notes,
    }


def series(plot: dict) -> dict[str, Any]:
    """Every year of the slider in one answer (the cells read once), and the first year it is CONTRADICTED."""
    cells = landcover.read_cells(*landcover.plot_box(plot))
    years = [verdict_at(plot, year, cells)
             for year in range(settings.SLIDER_FIRST_YEAR, settings.SLIDER_LAST_YEAR + 1)]
    flips = [y["year"] for y in years if y["verdict"] == "CONTRADICTED"]
    return {"plot_id": plot["plot_id"], "years": years, "first_contradicted_year": flips[0] if flips else None}
