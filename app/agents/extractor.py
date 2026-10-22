"""Extractor: what the promise claims, and what the reports really say (kit, section 4).

The model reads the messy texts; this file holds the rule fallback the kit describes:
keywords on the promise ("100%", "certified", "since"), supplier ids and four-digit
years in the NGO note, clearing words in the annexes. It is deliberately plain: it is
also the "rules alone" baseline the model is measured against.
"""
import re

from app.agents.context import RunContext
from app.rules import Extracted
from app.world.texts import NGO_DOC, RSE_DOC

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
    # The model reading plugs in here with app/adapters/llm.py; the rules stay its fallback.
    extracted = _read_by_rules(rse, ngo)
    ctx.step("Extractor", "fallback_rules", f"{len(rse)} report and {len(ngo)} NGO paragraph(s)",
             f"scope {extracted.scope}; {len(extracted.admissions)} admission(s); "
             f"{len(extracted.mentions)} supplier mention(s)", target="Planner", status="degraded")
    return parsed, extracted


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
