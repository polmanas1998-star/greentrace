"""Extractor: what the promise claims, and what the reports really say (kit, section 4).

Nemotron reads the messy texts (scope hidden in a qualifier, admissions buried in an
annex, names and dates written in words) and must quote every item word for word; the
Evaluator drops any quote that is not in the paragraph it cites. If no model answers,
the rule fallback the kit describes takes over: keywords on the promise ("100%",
"certified"), supplier ids and four-digit years in the NGO note, clearing words in the
annexes. It is deliberately plain: it is the "rules alone" baseline.
"""
import re

from app.agents import evaluator
from app.agents.context import RunContext
from app.rules import Extracted
from app.world.texts import NGO_DOC, RSE_DOC

SYSTEM = (
    "You are the Extractor of an audit of a company's deforestation-free cocoa claim. The data blocks are "
    "paragraphs of the company's sustainability report and of an NGO note, each labelled with its document, "
    "section and paragraph number. Report three things. scope: 'all' if the report's headline promise covers "
    "all the cocoa bought, 'certified' if it only covers certified cocoa, 'subset' if it covers another part; "
    "quote the words that show it. admissions: only in the company's own report (rse_2025), every passage where "
    "the company says forest was cleared, opened or converted for cocoa after 2020, whatever the wording; a denial "
    "such as 'not one hectare was cleared' is NOT an admission, and nothing from the NGO note is an admission. "
    "mentions: every passage of the NGO note (ngo_2025) that ties forest clearing or forest loss to a named "
    "cooperative or supplier, or to one of its plots, whoever is said to have done it and even if only reported "
    "by witnesses: supplier_name is the name exactly as written, cooperative_group is the union or group "
    "written next to it (for example 'the Riverside union'), else null, and year is the year as a number even when "
    "written in words. A passage that names no cooperative is not a mention. Quote word for word, copied exactly "
    "from one paragraph. Never invent a paragraph, a name or a year. Empty lists are a valid answer."
)
READINGS, AGREEMENT = 3, 2
_QUOTE = {"type": "string", "minLength": 3, "maxLength": 400}
SCHEMA = {
    "type": "object", "required": ["scope", "scope_quote", "admissions", "mentions"], "additionalProperties": False,
    "properties": {
        "scope": {"type": "string", "enum": ["all", "certified", "subset"]},
        "scope_quote": {"type": ["string", "null"], "maxLength": 400},
        "admissions": {"type": "array", "maxItems": 10, "items": {
            "type": "object", "required": ["doc", "section", "paragraph_no", "quote"], "additionalProperties": False,
            "properties": {"doc": {"type": "string"}, "section": {"type": "string", "enum": ["main", "annex"]},
                           "paragraph_no": {"type": "integer"}, "quote": _QUOTE,
                           "plots": {"type": "array", "items": {"type": "string"}},
                           "year": {"type": ["integer", "null"]}}}},
        "mentions": {"type": "array", "maxItems": 10, "items": {
            "type": "object", "required": ["doc", "paragraph_no", "quote", "supplier_name", "year"],
            "additionalProperties": False,
            "properties": {"doc": {"type": "string"}, "paragraph_no": {"type": "integer"}, "quote": _QUOTE,
                           "supplier_name": {"type": ["string", "null"]},
                           "cooperative_group": {"type": ["string", "null"]},
                           "year": {"type": ["integer", "null"]}}}},
    },
}

CHECKABLE = {
    "coverage": re.compile(r"100 ?%|\b(all|every|entire)\b", re.I),
    "no_deforestation": re.compile(r"deforest|forest|clear", re.I),
}
SINCE = re.compile(r"\bsince (\d{4})\b", re.I)
SUPPLIER_ID = re.compile(r"\bF-\d{3}\b")
YEAR = re.compile(r"\b(20\d\d)\b")
PLOT_ID = re.compile(r"\bP-\d{3}\b")
ADMISSION_WORDS = re.compile(r"\b(clearing|cleared|remediation|deforested)\b", re.I)


def read_claim(claim: str) -> dict:
    """Split the promise into checkable parts; an empty list means too vague to check."""
    types = [name for name, pattern in CHECKABLE.items() if pattern.search(claim)]
    since = SINCE.search(claim)
    return {"types": types, "since": int(since.group(1)) if since else None}


def extract(ctx: RunContext, claim: str) -> tuple[dict, Extracted]:
    parsed = read_claim(claim)
    ctx.step("Extractor", "extract_claims", f"promise: {claim}",
             f"checkable parts: {', '.join(parsed['types']) or 'none'}; since {parsed['since']}",
             target="Planner" if parsed["types"] else None)
    if not parsed["types"]:
        return parsed, Extracted()

    rse = ctx.gate.ask("Extractor", "Q1", doc_id=RSE_DOC).rows
    ngo = ctx.gate.ask("Extractor", "Q1", doc_id=NGO_DOC).rows
    extracted = _read_by_model(ctx, rse + ngo)
    if extracted is None:
        extracted = _read_by_rules(rse, ngo)
        ctx.step("Extractor", "fallback_rules", f"{len(rse)} report and {len(ngo)} NGO paragraph(s)",
                 f"scope {extracted.scope}; {len(extracted.admissions)} admission(s); "
                 f"{len(extracted.mentions)} supplier mention(s)", target="Planner", status="degraded")
    return parsed, extracted


def _read_by_model(ctx: RunContext, paragraphs: list[dict]) -> Extracted | None:
    """Three readings, and only what at least two of them agree on (the hosted model is not
    fully deterministic even at temperature 0): the same reports give the same facts."""
    if ctx.model is None:
        return None
    blocks = [(f"{p['doc_id']} {p['section']} {p['paragraph_no']}", p["paragraph_text"]) for p in paragraphs]
    readings, info = [], {}
    for _ in range(READINGS):
        answer, info = ctx.model.complete_json("Extractor", SYSTEM, blocks, SCHEMA)
        if answer is None:
            break
        readings.append(evaluator.verify_quotes(ctx, answer, paragraphs))
    if len(readings) < AGREEMENT:
        return None
    kept = _agreed(readings)
    extracted = Extracted(
        scope=kept["scope"],
        admissions=tuple({k: a.get(k) for k in ("doc", "section", "paragraph_no", "quote", "plots", "year")}
                         for a in kept["admissions"]),
        mentions=tuple({k: m.get(k) for k in ("doc", "paragraph_no", "quote", "supplier_name",
                                              "cooperative_group", "year")} for m in kept["mentions"]),
    )
    ctx.step("Extractor", "extract_claims",
             f"{len(paragraphs)} paragraph(s) read {len(readings)} times by {info['model']}",
             f"agreed by {AGREEMENT} of {len(readings)}: scope {extracted.scope}; "
             f"{len(extracted.admissions)} admission(s); {len(extracted.mentions)} supplier mention(s), all quoted",
             target="Planner", provider=info["provider"], model=info["model"])
    return extracted


def _agreed(readings: list[dict]) -> dict:
    """Majority scope; an admission or a mention is kept when enough readings cite its paragraph."""
    scopes = [r["scope"] for r in readings]
    scope = max(sorted(set(scopes)), key=scopes.count)
    if scopes.count(scope) < AGREEMENT:
        scope = "all"  # no majority: the narrower reading is not proven
    agreed = {"scope": scope}
    for kind in ("admissions", "mentions"):
        votes: dict[tuple, list[dict]] = {}
        for reading in readings:
            once = {(i["doc"], i.get("section", "main"), i["paragraph_no"]): i for i in reading[kind]}
            for key, item in once.items():
                votes.setdefault(key, []).append(item)
        agreed[kind] = [items[0] for key, items in sorted(votes.items()) if len(items) >= AGREEMENT]
    return agreed


def _read_by_rules(rse: list[dict], ngo: list[dict]) -> Extracted:
    promise = next((p for p in rse if p["section"] == "main" and "100%" in p["paragraph_text"]), None)
    scope = "certified" if promise and re.search(r"\bcertified\b", promise["paragraph_text"], re.I) else "all"

    admissions = tuple(
        {"doc": p["doc_id"], "section": p["section"], "paragraph_no": p["paragraph_no"],
         "plots": PLOT_ID.findall(p["paragraph_text"]), "year": _first_year(p["paragraph_text"])}
        for p in rse if p["section"] == "annex" and ADMISSION_WORDS.search(p["paragraph_text"])
    )
    mentions = tuple(
        {"doc": p["doc_id"], "paragraph_no": p["paragraph_no"], "supplier_id": supplier_id,
         "year": _first_year(p["paragraph_text"])}
        for p in ngo for supplier_id in SUPPLIER_ID.findall(p["paragraph_text"])
    )
    return Extracted(scope=scope, admissions=admissions, mentions=mentions)


def _first_year(text: str) -> int | None:
    match = YEAR.search(text)
    return int(match.group(1)) if match else None
