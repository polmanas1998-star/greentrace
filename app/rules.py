"""Every verdict rule, and only here (kit, section 6). The model proposes, this file decides.

Input: the evidence the agents gathered (rows read from Zetaris, plus what the
Extractor quoted from the reports). Output: a verdict per claim part, the rule that
decided it and the findings behind it, with ids that point back to the rows.
verify.py replays this file on a receipt, offline, so it must stay pure: no model,
no clock, no network, same input same output.

Rules, in the kit's order:
  1  a source is unavailable, or a datum is invalid (outside Cote d'Ivoire,
     missing polygon above 4 ha), or no reference map covers a measured plot UNVERIFIABLE
  2  direct evidence that held: a confirmed alert inside a polygon that kept
     delivering after it, after 31/12/2020, the company's own admission, or
     forest lost inside a measured plot after 2020 while it delivered (at least
     0.5 ha or 10 % of the plot, on land the EU map counted as forest in 2020) CONTRADICTED
  3  untraced volume, impossible yield, plot declared twice, unconfirmed alert,
     alert near a point-declared plot, sources in conflict, claim narrower than
     announced, no independent source at all, forest lost below that threshold,
     tree cover lost on land the EU map did not count as forest, or where no
     EU 2020 forest map exists                                            UNVERIFIABLE
  4  none of the above                                                   PROVEN
A part is CONTRADICTED as soon as one rule-2 finding holds: a defect on one plot
does not hide a contradiction proven on another. The promise is CONTRADICTED if one
part is, otherwise UNVERIFIABLE if one part is, otherwise PROVEN.
"""
from dataclasses import dataclass, field
from typing import Optional

from app.checks import geometry, landcover, registry

RULES_VERSION = "2026-10-23.1"

PROVEN, CONTRADICTED, UNVERIFIABLE = "PROVEN", "CONTRADICTED", "UNVERIFIABLE"
CUTOFF = "2020-12-31"
FIRST_YEAR_AFTER_CUTOFF = 2021
CONFIRMED = {"high", "highest"}
PARTS = ("coverage", "no_deforestation", "plausibility", "scope")
MAP_MIN_HA, MAP_MIN_SHARE = 0.5, 0.10  # kit v7: forest lost inside a plot that contradicts the claim
TREE_COVER_NOT_FOREST = ("tree cover was lost, but the EU 2020 map did not count this land as forest: "
                         "confirm in the field")
NO_EU_FOREST_MAP = "EU 2020 forest map not available here: loss shown is tree cover, not EU forest"


@dataclass(frozen=True)
class Extracted:
    """What the Extractor read in the reports (the model, or its rule fallback)."""
    scope: str = "all"                       # "all", or "subset" when the report's promise is narrower
    admissions: tuple[dict, ...] = ()        # {doc, section, paragraph_no, plots: [...], year}
    mentions: tuple[dict, ...] = ()          # {doc, paragraph_no, supplier_name, cooperative_group?, year}


@dataclass(frozen=True)
class Evidence:
    company: dict
    suppliers: list[dict]
    plots: list[dict]
    deliveries: list[dict]
    alerts: Optional[list[dict]]             # None: the alert source did not answer
    dq_issues: list[dict] = field(default_factory=list)
    extracted: Extracted = Extracted()
    reference: dict = field(default_factory=dict)  # plot_id -> Q10 cells around it (None: the maps did not answer)


@dataclass(frozen=True)
class Finding:
    part: str
    rule: int
    verdict: str
    code: str
    detail: str
    refs: tuple[str, ...]


def decide(evidence: Evidence) -> dict:
    findings = sorted(
        _coverage(evidence) + _no_deforestation(evidence) + _plausibility(evidence) + _scope(evidence),
        key=lambda f: (PARTS.index(f.part), f.rule, f.code, f.refs),
    )
    parts = [_part_verdict(part, [f for f in findings if f.part == part]) for part in PARTS]
    verdicts = {p["verdict"] for p in parts}
    overall = CONTRADICTED if CONTRADICTED in verdicts else UNVERIFIABLE if UNVERIFIABLE in verdicts else PROVEN
    return {
        "verdict": overall,
        "rules_version": RULES_VERSION,
        "parts": parts,
        "findings": [f.__dict__ for f in findings],
    }


def _part_verdict(part: str, findings: list[Finding]) -> dict:
    deciding = ([f for f in findings if f.verdict == CONTRADICTED]
                or [f for f in findings if f.verdict == UNVERIFIABLE])
    if not deciding:
        return {"claim_part": part, "verdict": PROVEN, "rule": 4, "codes": []}
    return {"claim_part": part, "verdict": deciding[0].verdict, "rule": min(f.rule for f in deciding),
            "codes": sorted({f.code for f in deciding})}


# ---- coverage: is every tonne traced to a plot? ------------------------------------------

def _coverage(e: Evidence) -> list[Finding]:
    share = registry.traced_share(e.company, e.deliveries, e.plots)
    if share >= registry.FULL_COVERAGE:
        return []
    return [Finding("coverage", 3, UNVERIFIABLE, "volume_not_traced",
                    f"{share:.1%} of {e.company['fiscal_year']} volume traced to a plot (needs 99%)",
                    (f"company:{e.company['company_id']}",))]


# ---- no deforestation after the cut-off ---------------------------------------------------

def _no_deforestation(e: Evidence) -> list[Finding]:
    found: list[Finding] = []
    delivered = registry.delivery_years(e.deliveries)
    delivering = [p for p in e.plots if any(y >= FIRST_YEAR_AFTER_CUTOFF for y in delivered.get(p["plot_id"], ()))]

    invalid = {i["entity_id"] for i in e.dq_issues if i["table"] == "plots"}
    checkable = []
    for plot in delivering:
        if plot["plot_id"] in invalid or not geometry.coordinates_valid(plot):
            found.append(Finding("no_deforestation", 1, UNVERIFIABLE, "invalid_coordinates",
                                 f"{plot['plot_id']} lies outside Cote d'Ivoire as declared",
                                 (f"plot:{plot['plot_id']}",)))
        elif geometry.polygon_missing(plot):
            found.append(Finding("no_deforestation", 1, UNVERIFIABLE, "polygon_missing",
                                 f"{plot['plot_id']} covers {plot['area_ha']} ha but is declared by a point",
                                 (f"plot:{plot['plot_id']}",)))
        else:
            checkable.append(plot)

    for admission in e.extracted.admissions:
        found.append(Finding("no_deforestation", 2, CONTRADICTED, "company_admission",
                             f"the company's own report admits clearing ({admission['doc']} "
                             f"{admission['section']} paragraph {admission['paragraph_no']})",
                             (f"doc:{admission['doc']}:{admission['section']}:{admission['paragraph_no']}",)))

    for plot in checkable:
        if plot["plot_id"] in e.reference:
            finding = map_check(plot, e.reference[plot["plot_id"]], delivered[plot["plot_id"]])
            found += [finding] if finding else []

    if e.alerts is None:
        found.append(Finding("no_deforestation", 1, UNVERIFIABLE, "source_unavailable",
                             "the alert source did not answer", ("source:alerts",)))
        return found
    if not e.alerts:
        found.append(Finding("no_deforestation", 3, UNVERIFIABLE, "no_independent_source",
                             "no alert data at all in the supply area: only the company's own records remain",
                             ("source:alerts",)))

    confirmed_suppliers = set()
    for alert in (a for a in e.alerts if a["alert_date"] > CUTOFF):
        year = int(alert["alert_date"][:4])
        for plot in checkable:
            refs = (f"alert:{alert['alert_id']}", f"plot:{plot['plot_id']}")
            if geometry.alert_inside(plot, alert):
                still_delivering = any(y >= year for y in delivered[plot["plot_id"]])
                if alert["confidence"] in CONFIRMED and still_delivering:
                    confirmed_suppliers.add(plot["supplier_id"])
                    found.append(Finding("no_deforestation", 2, CONTRADICTED, "confirmed_alert_in_delivering_plot",
                                         f"alert {alert['alert_id']} ({alert['alert_date']}, {alert['confidence']}) "
                                         f"lies inside {plot['plot_id']}, which delivered in {year} or later",
                                         refs))
                elif alert["confidence"] not in CONFIRMED:
                    found.append(Finding("no_deforestation", 3, UNVERIFIABLE, "unconfirmed_alert_in_plot",
                                         f"alert {alert['alert_id']} inside {plot['plot_id']} has "
                                         f"{alert['confidence']} confidence only", refs))
            elif geometry.alert_near_point_plot(plot, alert):
                found.append(Finding("no_deforestation", 3, UNVERIFIABLE, "alert_near_point_plot",
                                     f"alert {alert['alert_id']} is "
                                     f"{geometry.gap_to_point_plot_m(plot, alert):.0f} m from {plot['plot_id']}, "
                                     f"declared by a point: a polygon is needed to decide", refs))

    found += _conflicting_mentions(e, confirmed_suppliers)
    return found


def map_check(plot: dict, cells: Optional[list[dict]], delivered_in: set[int]) -> Optional[Finding]:
    """What the reference maps say about one delivering plot (kit v7); None when they show no loss after 2020.

    Only forest lost after the cut-off and no later than the plot's last delivery can
    contradict the claim. The Evaluator calls this too, on the cells of its own Q10 (R9).
    """
    plot_id = plot["plot_id"]
    refs = (f"plot:{plot_id}", f"cells:{plot_id}")
    if cells is None:
        return Finding("no_deforestation", 1, UNVERIFIABLE, "no_reference_data",
                       f"{plot_id}: the reference maps did not answer", refs)
    measured = landcover.measure(plot, cells)
    if not measured["covered"]:
        return Finding("no_deforestation", 1, UNVERIFIABLE, "no_reference_data", f"{plot_id}: {measured['reason']}",
                       refs)
    if not measured["forest_map"]:  # tree cover loss only: it can never prove that forest was cleared
        cover = {int(y): ha for y, ha in measured["loss_ha_by_year"].items() if int(y) > landcover.CUTOFF_YEAR}
        if not cover:
            return None
        return Finding("no_deforestation", 3, UNVERIFIABLE, "tree_cover_loss_no_eu_forest_map",
                       f"{plot_id}: {sum(cover.values()):.2f} ha of tree cover lost in {_years(cover)} "
                       f"(Hansen/UMD); {NO_EU_FOREST_MAP}", refs)
    after = {int(y): ha for y, ha in measured["forest_loss_ha_by_year"].items() if int(y) > landcover.CUTOFF_YEAR}
    while_delivering = {y: ha for y, ha in after.items() if y <= max(delivered_in, default=0)}
    lost, threshold = sum(while_delivering.values()), min(MAP_MIN_HA, MAP_MIN_SHARE * plot["area_ha"])
    if lost >= threshold:
        return Finding("no_deforestation", 2, CONTRADICTED, "forest_loss_in_delivering_plot",
                       f"{lost:.2f} ha that the EU map counted as forest in 2020 were lost inside {plot_id} in "
                       f"{_years(while_delivering)} (Hansen/UMD), while it kept delivering "
                       f"(threshold {threshold:.2f} ha: 0.5 ha or 10 % of the plot)", refs)
    if after:
        return Finding("no_deforestation", 3, UNVERIFIABLE, "forest_loss_below_threshold",
                       f"{sum(after.values()):.2f} ha of 2020 forest lost inside {plot_id} in {_years(after)}, "
                       f"below 0.5 ha and 10 % of the plot while it delivered: confirm in the field", refs)
    cover = {int(y): ha for y, ha in measured["loss_ha_by_year"].items() if int(y) > landcover.CUTOFF_YEAR}
    if cover:
        return Finding("no_deforestation", 3, UNVERIFIABLE, "tree_cover_loss_outside_2020_forest",
                       f"{plot_id}: {sum(cover.values()):.2f} ha of tree cover lost in {_years(cover)}; "
                       f"{TREE_COVER_NOT_FOREST}", refs)
    return None


def check_plot(plot: dict, cells: Optional[list[dict]], delivered_in: set[int]) -> list[Finding]:
    """One plot of an analyst's own file (no registry around it): rule 1 for what cannot be measured,
    then the reference maps (rule 2 of kit v7, map_check). An empty list: no issue found."""
    plot_id = plot["plot_id"]
    if plot["area_ha"] is None and not plot["polygon_wkt"]:
        return [Finding("no_deforestation", 1, UNVERIFIABLE, "area_missing",
                        f"{plot_id} is a point with no area: its outline cannot be measured",
                        (f"plot:{plot_id}",))]
    if geometry.polygon_missing(plot):
        return [Finding("no_deforestation", 1, UNVERIFIABLE, "polygon_missing",
                        f"{plot_id} covers {plot['area_ha']} ha but is declared by a point (the EUDR asks for a "
                        f"polygon above 4 ha)", (f"plot:{plot_id}",))]
    finding = map_check(plot, cells, delivered_in)
    return [finding] if finding else []


def decide_promise(plots: list[dict], plot_findings: list[Finding], extracted: Extracted,
                   claim_types: list[str]) -> dict:
    """The analyst's promise, judged on THEIR plots and documents (no registry, no volumes).

    no_deforestation: every plot's finding, the supplier's own admissions (rule 2), and a third
    party naming a supplier of the file whose plots show nothing (sources in conflict, rule 3).
    scope: a narrower promise than announced (rule 3). coverage, when the promise says "100%" or
    "all": the file holds no volumes, so that every tonne comes from these plots is not checked
    (rule 3). Same order and same verdict per part as decide()."""
    found = list(plot_findings)
    for admission in extracted.admissions:
        found.append(Finding("no_deforestation", 2, CONTRADICTED, "company_admission",
                             f"the supplier's own document admits clearing ({admission['doc']} paragraph "
                             f"{admission['paragraph_no']})",
                             (f"doc:{admission['doc']}:{admission['section']}:{admission['paragraph_no']}",)))
    contradicted = {p["supplier_id"] for p in plots
                    for f in plot_findings if f.verdict == CONTRADICTED and f"plot:{p['plot_id']}" in f.refs}
    for mention in extracted.mentions:
        if (mention.get("year") or 0) < FIRST_YEAR_AFTER_CUTOFF or not mention.get("supplier_name"):
            continue
        named = sorted({p["supplier_id"] for p in plots if _words(p["supplier_id"])
                        and _words(p["supplier_id"]) == _words(mention["supplier_name"])})
        if named and not set(named) & contradicted:
            found.append(Finding("no_deforestation", 3, UNVERIFIABLE, "sources_in_conflict",
                                 f"{mention['doc']} paragraph {mention['paragraph_no']} reports clearing by "
                                 f"{mention['supplier_name']} in {mention['year']}; "
                                 f"none of its plots shows it on the maps",
                                 tuple(f"supplier:{s}" for s in named) + (f"doc:{mention['doc']}:main:"
                                                                         f"{mention['paragraph_no']}",)))
    if extracted.scope != "all":
        found.append(Finding("scope", 3, UNVERIFIABLE, "narrower_scope",
                             "the promise covers only part of the supplier's cocoa, not all of it", ("doc:promise",)))
    if "coverage" in claim_types:
        found.append(Finding("coverage", 3, UNVERIFIABLE, "volume_not_checked",
                             "the plot file holds no volumes: that every tonne comes from these plots is not checked",
                             ("doc:promise",)))
    parts_checked = (["coverage"] if "coverage" in claim_types else []) + ["no_deforestation", "scope"]
    findings = sorted(found, key=lambda f: (PARTS.index(f.part), f.rule, f.code, f.refs))
    parts = [_part_verdict(part, [f for f in findings if f.part == part]) for part in parts_checked]
    verdicts = {p["verdict"] for p in parts}
    overall = CONTRADICTED if CONTRADICTED in verdicts else UNVERIFIABLE if UNVERIFIABLE in verdicts else PROVEN
    return {"verdict": overall, "rules_version": RULES_VERSION, "parts": parts,
            "findings": [f.__dict__ for f in findings]}


def _years(hectares: dict[int, float]) -> str:
    return ", ".join(str(year) for year in sorted(hectares))


def _conflicting_mentions(e: Evidence, already_contradicted: set[str]) -> list[Finding]:
    """An NGO names a delivering supplier for a year after the cut-off, and no confirmed alert backs it."""
    delivering = registry.delivering_suppliers(e.deliveries, since=FIRST_YEAR_AFTER_CUTOFF)
    found = []
    for mention in e.extracted.mentions:
        named_by = mention.get("supplier_id") or mention.get("supplier_name")
        if not named_by or (mention.get("year") or 0) < FIRST_YEAR_AFTER_CUTOFF:
            continue
        matches = [s for s in e.suppliers
                   if (s["supplier_id"] == mention["supplier_id"] if mention.get("supplier_id")
                       else _words(s["name"]) == _words(mention["supplier_name"]))
                   and _same_group(mention.get("cooperative_group"), s["cooperative_group"])]
        named = sorted(s["supplier_id"] for s in matches if s["supplier_id"] in delivering)
        if named and not set(named) & already_contradicted:
            found.append(Finding("no_deforestation", 3, UNVERIFIABLE, "sources_in_conflict",
                                 f"{mention['doc']} paragraph {mention['paragraph_no']} reports clearing by "
                                 f"{named_by} in {mention['year']}; no confirmed alert backs it",
                                 tuple(f"supplier:{s}" for s in named)
                                 + (f"doc:{mention['doc']}:main:{mention['paragraph_no']}",)))
    return found


_FILLER = {"the", "union", "of", "cooperative", "coop", "group", "des", "cooperatives", "de", "la", "known", "as"}


def _words(text: str | None) -> set[str]:
    return {w for w in "".join(c.lower() if c.isalnum() else " " for c in text or "").split() if w not in _FILLER}


def _same_group(said: str | None, registered: str) -> bool:
    """No group given matches any group; a given group must name the registered one."""
    return not _words(said) or _words(said) <= _words(registered)


# ---- plausibility: could the declared plots have produced the volume? -----------------------

def _plausibility(e: Evidence) -> list[Finding]:
    found = [Finding("plausibility", 3, UNVERIFIABLE, "impossible_yield",
                     f"{plot_id} declared {t_per_ha} t/ha in {year} (above {registry.MAX_PLAUSIBLE_T_PER_HA} t/ha)",
                     (f"plot:{plot_id}", f"delivery:{plot_id}:{year}"))
             for plot_id, year, t_per_ha in registry.impossible_yields(e.plots, e.deliveries)]
    found += [Finding("plausibility", 3, UNVERIFIABLE, "plot_declared_twice",
                      f"{plot_id} is declared by {' and '.join(owners)}",
                      (f"plot:{plot_id}",) + tuple(f"supplier:{s}" for s in owners))
              for plot_id, owners in registry.declared_twice(e.plots).items()]
    return found


# ---- scope: does the report claim what the promise says? ------------------------------------

def _scope(e: Evidence) -> list[Finding]:
    if e.extracted.scope == "all":
        return []
    return [Finding("scope", 3, UNVERIFIABLE, "narrower_scope",
                    "the report's own promise covers only part of the cocoa bought, not all of it",
                    ("doc:rse_2025",))]
