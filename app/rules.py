"""Every verdict rule, and only here (kit, section 6). The model proposes, this file decides.

Input: the evidence the agents gathered (rows read from Zetaris, plus what the
Extractor quoted from the reports). Output: a verdict per claim part, the rule that
decided it and the findings behind it, with ids that point back to the rows.
verify.py replays this file on a receipt, offline, so it must stay pure: no model,
no clock, no network, same input same output.

Rules, in the kit's order:
  1  a source is unavailable, or a datum is invalid (outside Cote d'Ivoire,
     missing polygon above 4 ha)                                         UNVERIFIABLE
  2  direct evidence that held: a confirmed alert inside a polygon that kept
     delivering after it, after 31/12/2020, or the company's own admission   CONTRADICTED
  3  untraced volume, impossible yield, plot declared twice, unconfirmed alert,
     alert near a point-declared plot, sources in conflict, claim narrower than
     announced, no independent source at all                             UNVERIFIABLE
  4  none of the above                                                   PROVEN
A part is CONTRADICTED as soon as one rule-2 finding holds: a defect on one plot
does not hide a contradiction proven on another. The promise is CONTRADICTED if one
part is, otherwise UNVERIFIABLE if one part is, otherwise PROVEN.
"""
from dataclasses import dataclass, field
from typing import Optional

from app.checks import geometry, registry

RULES_VERSION = "2026-10-22.1"

PROVEN, CONTRADICTED, UNVERIFIABLE = "PROVEN", "CONTRADICTED", "UNVERIFIABLE"
CUTOFF = "2020-12-31"
FIRST_YEAR_AFTER_CUTOFF = 2021
CONFIRMED = {"high", "highest"}
PARTS = ("coverage", "no_deforestation", "plausibility", "scope")


@dataclass(frozen=True)
class Extracted:
    """What the Extractor read in the reports (the model, or its rule fallback)."""
    scope: str = "all"                       # "all", or the narrower scope the report really claims
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


def _conflicting_mentions(e: Evidence, already_contradicted: set[str]) -> list[Finding]:
    """An NGO names a delivering supplier for a year after the cut-off, and no confirmed alert backs it."""
    delivering = registry.delivering_suppliers(e.deliveries, since=FIRST_YEAR_AFTER_CUTOFF)
    found = []
    for mention in e.extracted.mentions:
        if not mention.get("supplier_name") or (mention.get("year") or 0) < FIRST_YEAR_AFTER_CUTOFF:
            continue
        matches = [s for s in e.suppliers if s["name"] == mention["supplier_name"]
                   and mention.get("cooperative_group") in (None, s["cooperative_group"])]
        named = sorted(s["supplier_id"] for s in matches if s["supplier_id"] in delivering)
        if named and not set(named) & already_contradicted:
            found.append(Finding("no_deforestation", 3, UNVERIFIABLE, "sources_in_conflict",
                                 f"{mention['doc']} paragraph {mention['paragraph_no']} reports clearing by "
                                 f"{mention['supplier_name']} in {mention['year']}; no confirmed alert backs it",
                                 tuple(f"supplier:{s}" for s in named)
                                 + (f"doc:{mention['doc']}:main:{mention['paragraph_no']}",)))
    return found


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
                    f"the report only claims {e.extracted.scope} cocoa, not all cocoa", ("doc:rse_2025",))]
