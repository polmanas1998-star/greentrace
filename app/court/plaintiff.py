"""Plaintiff: the best defense of the supplier's promise, from the pieces the run already read.

A new agent on the fast lane (Nemotron), with a rule fallback. Round 1: one argument per claim
part the rules checked, citing the company's report, the registry and the deliveries. Later
rounds: one answer to each counter-argument that still holds, with another piece: one of the
refutations the Evaluator tested on that card (R1-R9), or a passage of the company's own report.
Every argument cites a piece; app/court/pieces.struck() strikes a citation that is not a row of
this run or not word for word, and the argument goes with it ("withdrawn"). The Plaintiff never
decides anything: app/rules.py does.
"""
import json
from dataclasses import dataclass
from typing import Any, Optional

from app.court import pieces as P
from app.rules import CUTOFF, PARTS

CUTOFF_TEXT = "31 December 2020"
assert CUTOFF == "2020-12-31", "CUTOFF_TEXT says the rules' cut-off in words"

# The refutations the Plaintiff may raise against a card, each with the passage of the company's
# report that backs it (a pattern) and the Plaintiff's contention. The company's own procedures first.
ANGLES = {
    "R3": (r"suspends deliveries", "{plot} had stopped delivering by the time of the {event}: the company suspends "
                                   "deliveries from a plot with a confirmed alert."),
    "R4": (r"high or highest confidence", "Alert {alert} is no proof of clearing: the company treats only alerts of "
                                          "high or highest confidence as confirmed."),
    "R5": (r"thirty-first of December|reference date", "Alert {alert} falls on or before the cut-off the company "
                                                       f"applies, {CUTOFF_TEXT}."),
    "R1": (None, "Alert {alert} lies outside {plot}: it may fall in a neighbouring plot."),
    "R8": (None, "The cocoa of {plot} is not part of the volume the promise covers."),
    "R9": (None, "The reference maps, read again, do not show a loss of 2020 forest inside {plot}."),
    "R2": (None, "The cooperative the NGO names is a namesake, not the supplier {supplier}."),
    "R6": (None, "The finding rests on a single source, with no independent issuer to confirm it."),
}
ORDER = tuple(ANGLES)
DOCUMENT = "document"
# A passage of the company's report against a card that no refutation applies to (a fact read
# directly from the data). The Judge tests it by code: does the rule still find the same thing?
DOCUMENT_ANSWERS = {
    "polygon_missing": (r"single point", "{plot} may be declared by a point: the company's method geolocates small "
                                         "plots by a single point, as the Regulation allows."),
    "alert_near_point_plot": (r"single point", "{plot} is rightly declared by a point, and alert {alert} lies outside "
                                               "that point."),
    "unconfirmed_alert_in_plot": (r"high or highest confidence", "Alert {alert} is not confirmed, and the company acts "
                                                                 "only on confirmed alerts."),
    "source_unavailable": (r"satellite deforestation alerts|alert systems",
                           "The company reads every plot against public deforestation alert systems: the claim does "
                           "not rest on its word alone."),
    "no_independent_source": (r"satellite deforestation alerts|alert systems",
                              "The company reads every plot against public deforestation alert systems: the claim "
                              "does not rest on its word alone."),
}

_CITE = {"type": "object", "required": ["source", "ref", "quote"], "additionalProperties": False,
         "properties": {"source": {"type": "string", "enum": list(P.SOURCES)},
                        "ref": {"type": "string", "maxLength": 80},
                        "quote": {"type": ["string", "null"], "maxLength": 400}}}
_CITES = {"type": "array", "minItems": 1, "maxItems": 3, "items": _CITE}
_CLAIM = {"type": "string", "minLength": 3, "maxLength": 300}
OPEN_SCHEMA = {
    "type": "object", "required": ["arguments"], "additionalProperties": False,
    "properties": {"arguments": {"type": "array", "maxItems": len(PARTS), "items": {
        "type": "object", "required": ["part", "claim", "cites"], "additionalProperties": False,
        "properties": {"part": {"type": "string", "enum": list(PARTS)}, "claim": _CLAIM, "cites": _CITES}}}},
}
ANSWER_SCHEMA = {
    "type": "object", "required": ["answers"], "additionalProperties": False,
    "properties": {"answers": {"type": "array", "maxItems": 12, "items": {
        "type": "object", "required": ["counter", "angle", "claim", "cites"], "additionalProperties": False,
        "properties": {"counter": {"type": "string", "maxLength": 8},
                       "angle": {"type": "string", "enum": list(ORDER) + [DOCUMENT]},
                       "claim": _CLAIM, "cites": _CITES}}}},
}
_HOW_TO_CITE = (
    "Every argument cites one to three pieces from the data: for a report paragraph, its source (csr_report for "
    "the company's report, ngo_report for the NGO note), its ref exactly as labelled (for example '§2' or "
    "'annex §3') and a quote copied word for word from it; for a row, its source (registry, deliveries, alerts or "
    "declared_plot), its id exactly as given (for example 'plot:P-014') and either null or a quote 'field: value' "
    "with the row's exact value. Code strikes any argument with a citation it cannot find. You never decide the "
    "verdict: fixed rules do."
)
OPEN_SYSTEM = (
    "You are the Plaintiff in a court of inquiry on a company's deforestation-free cocoa promise. Build the "
    "strongest honest defense of the promise from the pieces in the data: the company's report, its registry, its "
    "deliveries and the alerts read. Give at most one argument per claim part listed, in one sentence each. "
    + _HOW_TO_CITE
)
ANSWER_SYSTEM = (
    "You are the Plaintiff in a court of inquiry on a company's deforestation-free cocoa promise. The Defense "
    "answered your arguments with counter-arguments, each resting on a query and its rows. Answer each "
    "counter-argument listed at most once, with a piece you have not cited yet in that line of defense: choose one "
    "of the angles allowed for it (the id of a refutation the Evaluator tested, or 'document' for a passage of the "
    "company's report), state your contention in one sentence and cite it. Leave out a counter-argument you cannot "
    "answer honestly. " + _HOW_TO_CITE
)


@dataclass(frozen=True)
class Docket:
    """What the Plaintiff reads besides the company's report: the promise, and the rows and NGO paragraphs at stake."""
    claim: str
    refs: frozenset[str]            # row ids: the company, the highest yield, the cards' entities and deliveries
    ngo_paragraphs: frozenset[str]  # "§3": the NGO paragraphs a card cites


def docket(ctx: Any, claim: str, cards: list[dict], pieces: P.Pieces) -> Docket:
    refs, ngo = {f"company:{ctx.company_id}"}, set()
    best = _highest_yield(ctx, pieces)
    if best:
        delivery, plot = best
        refs.update({f"plot:{plot['plot_id']}", f"delivery:{plot['plot_id']}:{delivery['year']}"})
    for card in cards:
        for ref in card["refs"]:
            kind, _, key = ref.partition(":")
            if kind in ("alert", "plot", "supplier", "delivery"):
                refs.add(ref)
            if kind == "plot":
                refs.update(r for _, r in pieces.rows if r.startswith(f"delivery:{key}:"))
            if kind == "doc" and key.startswith(f"{P.DOCUMENTS['ngo_report']}:main:"):
                ngo.add(f"§{key.rsplit(':', 1)[1]}")
    return Docket(claim, frozenset(refs), frozenset(ngo))


# ---- round 1: one argument per claim part -----------------------------------------------------

def opening(ctx: Any, parts: list[str], pieces: P.Pieces, case: Docket) -> tuple[list[dict], dict]:
    """The Plaintiff's first arguments, [{part, claim, cites, struck}], and how they were written."""
    rules = {part: argument for part in parts if (argument := _opening_by_rules(ctx, part, pieces))}
    answer, info = _ask(ctx, OPEN_SYSTEM, [*_brief(case, pieces), ("claim parts", json.dumps(parts))], OPEN_SCHEMA)
    if answer is None:
        return [rules[part] for part in parts if part in rules], info
    by_model: dict[str, dict] = {}
    for item in answer["arguments"]:
        if item["part"] in parts and item["part"] not in by_model:
            by_model[item["part"]] = _checked({"part": item["part"], "claim": item["claim"].strip(),
                                               "cites": item["cites"]}, pieces)
    info["filled_by_rules"] = [part for part in parts if part not in by_model and part in rules]
    return [by_model.get(part) or rules[part] for part in parts if part in by_model or part in rules], info


def _opening_by_rules(ctx: Any, part: str, pieces: P.Pieces) -> Optional[dict]:
    company = P.row_cite(pieces, "registry", f"company:{ctx.company_id}", "total_tonnes")
    if part == "coverage":
        report = P.sentence(pieces, "csr_report", r"\btraced\b")
        row = pieces.rows.get(("registry", f"company:{ctx.company_id}"), [{}])[0]
        claim = (f"Every tonne of the {row.get('fiscal_year')} fiscal year is traced to a plot of the supplier "
                 f"registry, as the company's report states; the registry declares {row.get('total_tonnes')} t.")
        cites = [report, company]
    elif part == "no_deforestation":
        screened = P.sentence(pieces, "csr_report", r"satellite deforestation alerts")
        claim = (f"No forest was cleared after {CUTOFF_TEXT} for the cocoa the company buys, as its report states"
                 + (", and every plot is screened against public satellite alerts." if screened else "."))
        cites = [P.sentence(pieces, "csr_report", r"not one hectare", r"deforestation-free"), screened]
    elif part == "plausibility":
        best = _highest_yield(ctx, pieces)
        if best is None:
            return None
        delivery, plot = best
        claim = (f"Each plot can produce what it declares: the highest yield in the registry is "
                 f"{delivery['tonnes'] / plot['area_ha']:.2f} t/ha ({plot['plot_id']} in {delivery['year']}: "
                 f"{delivery['tonnes']} t on {plot['area_ha']} ha).")
        cites = [P.row_cite(pieces, "deliveries", f"delivery:{plot['plot_id']}:{delivery['year']}", "tonnes"),
                 P.row_cite(pieces, "registry", f"plot:{plot['plot_id']}", "area_ha")]
    elif part == "scope":
        claim = "The promise covers all of the cocoa the company buys, with no qualifier."
        cites = [P.sentence(pieces, "csr_report", r"100 ?%|\ball of our cocoa\b")]
    else:
        return None
    cites = [cite for cite in cites if cite]
    return {"part": part, "claim": claim, "cites": cites, "struck": None, "by": "rules"} if cites else None


def _highest_yield(ctx: Any, pieces: P.Pieces) -> Optional[tuple[dict, dict]]:
    """The delivery with the most tonnes per hectare among the rows the Investigator read (Q6, Q3)."""
    deliveries, plots = P.find(ctx, "Q6", "Investigator"), P.find(ctx, "Q3", "Investigator")
    if deliveries is None or plots is None:
        return None
    area = {}
    for plot in plots.rows:
        if (plot.get("area_ha") or 0) > 0:
            area.setdefault(plot["plot_id"], plot)
    ranked = [(d["tonnes"] / area[d["plot_id"]]["area_ha"], d["plot_id"], d["year"], d) for d in deliveries.rows
              if d["plot_id"] in area and d["tonnes"] > 0]
    if not ranked:
        return None
    _, plot_id, _, delivery = max(ranked, key=lambda r: (r[0], r[1], r[2]))
    return delivery, area[plot_id]


# ---- later rounds: an answer to each counter-argument that holds ----------------------------------

def answers(ctx: Any, counters: list[dict], lines: dict[str, dict], cards: dict[str, dict],
            pieces: P.Pieces, case: Docket) -> tuple[list[dict], dict]:
    """One answer per open counter-argument, [{counter, card, angle, claim, cites, struck}], and how.

    counters: the counter-arguments that still hold, each {id, card, detail, query_id, rows}.
    cards: card id -> the card with the refutations the Evaluator tried on it ("tried").
    lines: card id -> {"raised": angles already raised, "cited": the pieces already cited against it (piece())}."""
    allowed = {c["id"]: allowed_angles(cards[c["card"]], lines[c["card"]], pieces) for c in counters}
    rules = {c["id"]: answer for c in counters
             if (answer := _answer_by_rules(c, cards[c["card"]], allowed[c["id"]], lines[c["card"]], pieces))}
    open_counters = [c for c in counters if allowed[c["id"]]]
    if not open_counters:
        return [], {"provider": "rules", "model": None, "reason": "no angle left to raise"}
    brief = [{"counter": c["id"], "answers": c["answers"], "card": c["card"], "says": c["detail"],
              "query_id": c["query_id"], "rows": c["rows"][:5], "angles_allowed": allowed[c["id"]],
              "already_cited": lines[c["card"]]["cited"]} for c in open_counters]
    answer, info = _ask(ctx, ANSWER_SYSTEM, [*_brief(case, pieces), ("counter-arguments", json.dumps(brief))],
                        ANSWER_SCHEMA)
    if answer is None:
        return [rules[c["id"]] for c in open_counters if c["id"] in rules], info
    by_model: dict[str, dict] = {}
    for item in answer["answers"]:
        counter = next((c for c in open_counters if c["id"] == item["counter"]), None)
        if counter is None or counter["id"] in by_model or item["angle"] not in allowed[counter["id"]]:
            continue
        argument = _checked({"counter": counter["id"], "card": counter["card"], "angle": item["angle"],
                             "claim": item["claim"].strip(), "cites": item["cites"]}, pieces)
        if not argument["struck"] and all(piece(cite) in lines[counter["card"]]["cited"] for cite in argument["cites"]):
            argument["struck"] = "it cites no piece that was not cited already in this line of defense"
        by_model[counter["id"]] = argument
    info["filled_by_rules"] = [c["id"] for c in open_counters if c["id"] not in by_model and c["id"] in rules]
    return [by_model.get(c["id"]) or rules[c["id"]] for c in open_counters
            if c["id"] in by_model or c["id"] in rules], info


def allowed_angles(card: dict, line: dict, pieces: P.Pieces) -> list[str]:
    """The refutations the Evaluator tested on the card and not raised yet, then a report passage once."""
    tried = [t["id"] for t in card.get("tried", [])]
    angles = [angle for angle in ORDER if angle in tried and angle not in line["raised"]]
    if DOCUMENT not in line["raised"] and pieces.paragraphs:
        angles.append(DOCUMENT)
    return angles


def _answer_by_rules(counter: dict, card: dict, allowed: list[str], line: dict, pieces: P.Pieces) -> Optional[dict]:
    """The first allowed angle with a piece not cited yet: the company's report where it backs the angle, else
    the row the angle is about."""
    names, cited = _names(card), line["cited"]
    for angle in allowed:
        pattern, claim = DOCUMENT_ANSWERS.get(card["code"], (None, None)) if angle == DOCUMENT else ANGLES[angle]
        report = P.sentence(pieces, "csr_report", pattern, skip=tuple(cited)) if pattern else None
        rows = [] if report or angle == DOCUMENT else _rows_for(angle, card, pieces)
        cites = [cite for cite in [report, *rows] if cite and piece(cite) not in cited]
        if cites and claim:
            return {"counter": counter["id"], "card": card["id"], "angle": angle, "claim": claim.format(**names),
                    "cites": cites, "struck": None, "by": "rules"}
    return None


def _rows_for(angle: str, card: dict, pieces: P.Pieces) -> list[Optional[dict]]:
    """The row an angle is about, cited by id (and one field where it backs the contention)."""
    refs = dict(ref.split(":", 1) for ref in card["refs"] if ":" in ref)
    plot = refs.get("plot")
    plot_source = "declared_plot" if ("declared_plot", f"plot:{plot}") in pieces.rows else "registry"
    if angle in ("R1", "R9", "R3", "R8") and plot:
        return [P.row_cite(pieces, plot_source, f"plot:{plot}", "area_ha" if angle in ("R1", "R9") else None)]
    if angle in ("R4", "R5") and "alert" in refs:
        return [P.row_cite(pieces, "alerts", f"alert:{refs['alert']}")]
    if angle == "R6" and "alert" in refs:
        return [P.row_cite(pieces, "alerts", f"alert:{refs['alert']}", "system")]
    if angle == "R6" and plot:
        return [P.row_cite(pieces, plot_source, f"plot:{plot}")]
    if angle in ("R2", "R6") and "supplier" in refs:
        return [P.row_cite(pieces, "registry", f"supplier:{refs['supplier']}", "cooperative_group")]
    return []


def _names(card: dict) -> dict[str, str]:
    refs = dict(ref.split(":", 1) for ref in card["refs"] if ":" in ref)
    alert = refs.get("alert")
    return {"plot": refs.get("plot", "the plot"), "alert": alert or "the alert",
            "supplier": refs.get("supplier", "the supplier"),
            "event": f"alert {alert}" if alert else "loss of forest"}


# ---- shared ------------------------------------------------------------------------------------

def piece(cite: dict) -> list:
    """What a citation brings: a row whatever field it quotes, [source, ref]; a passage, [source, ref, quote]."""
    if cite.get("source") in P.DOCUMENTS:
        return [cite.get("source"), cite.get("ref"), " ".join(str(cite.get("quote") or "").split())]
    return [cite.get("source"), cite.get("ref")]


def _checked(argument: dict, pieces: P.Pieces) -> dict:
    """A model argument as the code admits it: struck as soon as one citation is not found."""
    reasons = [reason for cite in argument["cites"] if (reason := P.struck(pieces, cite))]
    cites = [{"source": c.get("source"), "ref": c.get("ref"), "quote": c.get("quote")} for c in argument["cites"]
             if isinstance(c, dict)]
    return {**argument, "cites": cites, "struck": reasons[0] if reasons else None, "by": "model"}


def _ask(ctx: Any, system: str, blocks: list[tuple[str, str]], schema: dict) -> tuple[Optional[dict], dict]:
    if ctx.model is None:
        return None, {"provider": "rules", "model": None, "reason": "no model provider for this run"}
    answer, info = ctx.model.complete_json("Plaintiff", system, blocks, schema)
    return answer, info


def _brief(case: Docket, pieces: P.Pieces) -> list[tuple[str, str]]:
    """What the model may cite: the company's report, the NGO paragraphs the cards cite, the rows they rest on."""
    def paragraphs(source: str, keep=lambda ref: True) -> str:
        return "\n".join(f"{ref}: {text}" for (src, ref), text in sorted(pieces.paragraphs.items())
                         if src == source and keep(ref))
    rows = {f"{source} {ref}": found[0] for (source, ref), found in sorted(pieces.rows.items()) if ref in case.refs}
    blocks = [("promise", case.claim), ("csr_report paragraphs", paragraphs("csr_report"))]
    if case.ngo_paragraphs:
        blocks.append(("ngo_report paragraphs", paragraphs("ngo_report", lambda ref: ref in case.ngo_paragraphs)))
    blocks.append(("rows", json.dumps(rows, sort_keys=True, default=str)))
    return blocks
