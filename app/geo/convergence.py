"""Do the sources converge on one plot? Was forest standing in 2020 lost after 2020 inside it?

Each source answers the part it measures, inside the plot's outline, with the threshold of the rules
(app/rules.py: 0.5 ha or 10 % of the plot, whichever is smaller):
  JRC GFC2020 v4        forest in 2020 (ha)            data/reference_cells.csv.gz, offline
  Hansen GFC v1.12      tree cover lost after 2020 (ha) the same committed cells, offline
  GFW tiles             tree cover lost after 2020 (ha) the Hansen/UMD layer as Global Forest Watch serves it
                                                       (app/checks/gfw.py): live mode only. The SAME layer as
                                                       Hansen read another way, not an independent observation
  Whisp                 tree cover in 2020 and disturbance after 2020 (its own combination of datasets):
                        live mode with WHISP_API_KEY only (app/geo/whisp.py)
A source is "queried" (it answered), "not_queried" (not asked: sample mode, no key; the reason is given) or
"unavailable" (asked, no answer: no map here, an error). Each queried source supports the question, or not;
the answer is "n of m sources queried agree", never a percentage of certainty. Nothing here is a verdict:
the verdict stays with app/rules.py.
"""
from typing import Any, Optional

from app.checks import gfw, landcover
from app.geo import settings, whisp
from app.rules import MAP_MIN_HA, MAP_MIN_SHARE

QUESTION = "was forest standing in 2020 lost after 2020 inside this plot?"
JRC, HANSEN, GFW = "JRC GFC2020", "Hansen GFC v1.12", "GFW tiles"
SAYS = {
    "forest_2020": "forest in 2020",
    "not_forest": "not forest in 2020",
    "loss_after_2020": "tree cover loss after 2020",
    "no_loss_after_2020": "no tree cover loss after 2020",
    "tree_cover_2020_and_disturbance_after_2020": "tree cover in 2020 and disturbance after 2020",
    "tree_cover_2020_no_disturbance_after_2020": "tree cover in 2020, no disturbance after 2020",
    "no_tree_cover_2020": "no tree cover in 2020",
}
FOREST_PART = {"forest_2020", "tree_cover_2020_and_disturbance_after_2020"}
LOSS_PART = {"loss_after_2020", "tree_cover_2020_and_disturbance_after_2020"}
NOT_IN_SAMPLE = "sample mode makes no network call; live mode reads it"


def converge(plot: dict, *, sample: bool, remote: Optional[gfw.Tiles] = None, whisp_key: Optional[str] = None,
             whisp_transport: Optional[whisp.Transport] = None, sleep=None,
             cells: Optional[list[dict]] = None) -> dict[str, Any]:
    threshold = round(min(MAP_MIN_HA, MAP_MIN_SHARE * plot["area_ha"]), 4)
    if cells is None:
        cells = landcover.read_cells(*landcover.plot_box(plot))
    measured = landcover.measure(plot, cells)
    sources = [_jrc(measured, threshold), _hansen(measured, threshold),
               _gfw(plot, threshold, sample, remote), _whisp(plot, sample, whisp_key, whisp_transport, sleep)]
    agree, queried, conclusion = _conclude(sources)
    return {
        "plot": {k: plot[k] for k in ("plot_id", "lat", "lon", "area_ha")} | {"source": plot.get("source")},
        "question": QUESTION,
        "threshold_ha": threshold,
        "sources": sources,
        "agree": agree,
        "queried": queried,
        "conclusion": conclusion,
        "mode": "sample" if sample else "live",
        "note": "n of m sources queried agree: no percentage of certainty. GFW tiles serve the same Hansen/UMD "
                "layer as Hansen GFC v1.12; JRC GFC2020 and Hansen come from the same committed cells file.",
    }


def _entry(name: str, says: str, value: Optional[float], status: str, supports: Optional[bool], **more) -> dict:
    return {"name": name, "says": says, "value": value, "unit": "ha" if value is not None else None,
            "status": status, "supports": supports, **more}


def _jrc(measured: dict, threshold: float) -> dict:
    if not measured["covered"]:
        return _entry(JRC, "no_data", None, "unavailable", None, reason=measured["reason"])
    forest = measured["forest_2020_ha"]
    says = "forest_2020" if forest >= threshold else "not_forest"
    return _entry(JRC, says, forest, "queried", says == "forest_2020", measures="forest in 2020 inside the plot",
                  version="v4", read_from="data/reference_cells.csv.gz")


def _loss_after(measured: dict) -> float:
    return round(sum(ha for y, ha in (measured["loss_ha_by_year"] or {}).items()
                     if int(y) > landcover.CUTOFF_YEAR), 4)


def _hansen(measured: dict, threshold: float) -> dict:
    if not measured["covered"]:
        return _entry(HANSEN, "no_data", None, "unavailable", None, reason=measured["reason"])
    lost = _loss_after(measured)
    says = "loss_after_2020" if lost >= threshold else "no_loss_after_2020"
    return _entry(HANSEN, says, lost, "queried", says == "loss_after_2020",
                  measures="tree cover lost after 2020 inside the plot (loss years 2021-2024)",
                  version="v1.12", read_from="data/reference_cells.csv.gz")


def _gfw(plot: dict, threshold: float, sample: bool, remote: Optional[gfw.Tiles]) -> dict:
    if sample or remote is None:
        return _entry(GFW, "no_data", None, "not_queried", None, reason=f"{NOT_IN_SAMPLE} ({gfw.SOURCE})")
    try:
        reading = remote.cells(*landcover.plot_box(plot))
    except gfw.GfwUnavailable as error:
        return _entry(GFW, "no_data", None, "unavailable", None, reason=str(error))
    measured = landcover.measure(plot, reading.cells)
    if not measured["covered"]:
        return _entry(GFW, "no_data", None, "unavailable", None, reason=measured["reason"])
    lost = _loss_after(measured)
    says = "loss_after_2020" if lost >= threshold else "no_loss_after_2020"
    return _entry(GFW, says, lost, "queried", says == "loss_after_2020",
                  measures="tree cover lost after 2020 inside the plot, tree cover at least 30 % in 2000",
                  read_from=gfw.SOURCE, tiles=len(reading.tiles), same_layer_as=HANSEN)


def _whisp(plot: dict, sample: bool, key: Optional[str], transport, sleep) -> dict:
    if key is None:
        return _entry(whisp.NAME, "no_data", None, "not_queried", None, reason="WHISP_API_KEY is not set")
    if sample:
        return _entry(whisp.NAME, "no_data", None, "not_queried", None, reason=NOT_IN_SAMPLE)
    try:
        options = {k: v for k, v in (("transport", transport), ("sleep", sleep)) if v is not None}
        properties = whisp.analyse(plot, key, **options)
    except whisp.WhispUnavailable as error:
        return _entry(whisp.NAME, "no_data", None, "unavailable", None, reason=str(error))
    says, supports, detail = whisp.reading(properties)
    if supports is None:
        return _entry(whisp.NAME, "no_data", None, "unavailable", None,
                      reason=f"the result holds no tree cover indicator ({detail})")
    return _entry(whisp.NAME, says, None, "queried", supports, detail=detail,
                  measures="Ind_01_treecover and Ind_04_disturbance_after_2020", read_from=settings.WHISP_URL)


def _conclude(sources: list[dict]) -> tuple[int, int, str]:
    queried = [s for s in sources if s["status"] == "queried"]
    if not queried:
        reasons = "; ".join(f"{s['name']}: {s.get('reason')}" for s in sources)
        return 0, 0, f"no source could be queried for this plot ({reasons})"
    yes = [s for s in queried if s["supports"]]
    no = [s for s in queried if not s["supports"]]
    if len(yes) == len(no):
        return len(yes), len(queried), (f"the {len(queried)} sources queried do not agree: "
                                        + "; ".join(_said(s) for s in queried))
    side, other = (yes, no) if len(yes) > len(no) else (no, yes)
    if side is yes:
        forest = any(s["says"] in FOREST_PART for s in yes)
        loss = any(s["says"] in LOSS_PART for s in yes)
        statement = ("forest in 2020 and loss after 2020" if forest and loss
                     else "loss after 2020 (no source queried maps forest in 2020)" if loss
                     else "forest in 2020 (no source queried maps loss after 2020)")
    else:
        statement = ("no forest of 2020 lost after 2020 ("
                     + "; ".join(sorted({SAYS[s["says"]] for s in no})) + ")")
    dissent = f"; {', '.join(_said(s) for s in other)}" if other else ""
    return len(side), len(queried), f"{len(side)} of {len(queried)} sources queried agree: {statement}{dissent}"


def _said(source: dict) -> str:
    return f"{source['name']} says {SAYS[source['says']]}"
