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
from app.memory import scout
from app.rules import Extracted
from app.world.texts import NGO_DOC, RSE_DOC

SYSTEM = (
    "You are the Extractor of an audit of a company's deforestation-free cocoa claim. The data blocks are "
    "paragraphs of the company's sustainability report and of an NGO note, each labelled with its document, "
    "section and paragraph number. Report three things. scope: read the report's headline promise; it is 'all' "
    "only if it covers every tonne the company buys with no qualifier at all; any qualifier that narrows it (a "
    "programme, a label, a certification, a region, a product line) makes it 'subset'; quote the words of the "
    "promise that decide it. admissions: only in the company's own report (rse_2025), every passage where "
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
        "scope": {"type": "string", "enum": ["all", "subset"]},
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
    return {"types": types, "since": int(since.group(1)) if since else None,
            "commodities": commodities_in(claim), "company": company_in(claim)}


# ---- what the promise is about, before any query: the sample data is one cocoa company ---------------------

DATA_COMMODITY = "cocoa"
DATA_COMPANIES = ("ChocoDurable SA", "Choco Durable", "Kalou Chocolat")  # the synthetic company and its demo brand
COMMODITIES = {  # the seven EUDR commodities, with their usual words
    "cocoa": r"cocoa|cacao|chocolates?|cocoa beans?",
    "coffee": r"coffees?|caf[eé]s?|espresso|arabica|robusta",
    "palm oil": r"palm|palm[- ]oil|oil palms?|palm kernels?",
    "soy": r"soy|soya|soybeans?|soja",
    "cattle": r"cattle|beef|leather|cows?|livestock|bovine",
    "rubber": r"rubber|latex",
    "wood": r"wood|wooden|timber|lumber|paper|pulp|plywood|charcoal|furniture",
}
_COMMODITY = {name: re.compile(rf"\b(?:{words})\b", re.I) for name, words in COMMODITIES.items()}
# Capitalised words that are not a company: places, labels, regulations, and the words a sentence starts with.
NOT_A_NAME = {
    "eu", "eudr", "europe", "european", "union", "regulation", "africa", "west", "côte", "cote", "ivory", "coast",
    "ghana", "soubré", "soubre", "brazil", "indonesia", "malaysia", "colombia", "ecuador", "cameroon", "nigeria",
    "rainforest", "alliance", "fairtrade", "utz", "rspo", "fsc", "pefc", "iso", "organic", "jrc", "gfw", "hansen",
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
    "november", "december", "i", "ok",
    *(word for words in COMMODITIES.values() for word in re.findall(r"[a-zé]+", words)),
}
STARTERS = {
    "we", "our", "ours", "us", "all", "every", "each", "everything", "the", "this", "these", "those", "since", "by",
    "from", "in", "at", "on", "for", "as", "it", "its", "no", "none", "zero", "any", "most", "fully", "only", "a",
    "an", "my", "today", "now", "there", "here", "what", "how", "both", "certified", "sustainably", "sustainable",
    "responsibly", "deforestation-free", "nous", "notre", "nos", "tout", "tous", "toutes", "le", "la", "les",
    "depuis", "100%",
}
NAME_VERBS = {"is", "are", "was", "has", "have", "guarantees", "guarantee", "sources", "source", "buys", "promises",
              "commits", "says", "claims", "ensures", "uses", "sells", "makes", "will", "does", "only", "never"}
PROGRAMME = {"programme", "program", "plan", "label", "scheme", "initiative", "standard", "certification", "project"}
OUT_OF_SCOPE = ("GreenTrace has no data on {what} here. Upload your own plots (GeoJSON) to check them, "
                "or try the example cocoa supply chain.")
TOO_VAGUE = ("Too vague to check: the promise names nothing that data can check. Say what is promised, for "
             "example \"100% of our cocoa is deforestation-free since 2021.\"")
SCOPE_SYSTEM = (
    "Read one promise about a supply chain. Report commodities: every EUDR commodity it is about (cocoa, coffee, "
    "palm oil, soy, cattle, rubber, wood), including through products made from it (chocolate is cocoa, leather is "
    "cattle, paper is wood); an empty list if it names none. company: the company or brand the promise is about, "
    "copied exactly as written, or 'none' when it names none ('we' and 'our' name no company). Never guess."
)
SCOPE_SCHEMA = {
    "type": "object", "required": ["commodities", "company"], "additionalProperties": False,
    "properties": {"commodities": {"type": "array", "maxItems": 7,
                                   "items": {"type": "string", "enum": sorted(COMMODITIES)}},
                   "company": {"type": "string", "maxLength": 120}},
}


def commodities_in(claim: str) -> list[str]:
    return [name for name, pattern in _COMMODITY.items() if pattern.search(claim)]


def company_in(claim: str) -> str | None:
    """The first company or brand the promise names, by rule: a run of capitalised words that is not a place,
    a label or a programme; at the start of a sentence, only when a verb or a possessive shows it is a name."""
    for sentence in re.split(r"(?<=[.!?])\s+", claim.strip()):
        tokens = [t.strip(".,;:!?\"()[]“”«»") for t in sentence.split()]
        i = 0
        while i < len(tokens):
            if not (tokens[i][:1].isupper() and tokens[i][:1].isalpha()):
                i += 1
                continue
            j = i
            while j < len(tokens) and tokens[j][:1].isupper():
                j += 1
            words = [re.sub(r"['’]s$", "", t) for t in tokens[i:j]]
            following = tokens[j].lower() if j < len(tokens) else ""
            if i == 0 and words[0].lower() in STARTERS:
                words = words[1:]
            elif i == 0 and len(words) == 1 and following not in NAME_VERBS and not re.search(r"['’]s$", tokens[i]):
                words = []
            names = [w for w in words if w.lower() not in NOT_A_NAME]
            if names and following not in PROGRAMME and words[-1].lower() not in PROGRAMME:
                return " ".join(words).strip()
            i = j
    return None


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _known_company(name: str) -> bool:
    key = _key(name)
    return any(key and (key.startswith(_key(known).removesuffix("sa")) or _key(known).startswith(key))
               for known in DATA_COMPANIES + ("ChocoDurable", "Kalou"))


def scope(ctx: RunContext, claim: str, parsed: dict) -> str | None:
    """Out of scope before any query: another commodity, or another company, than the data's. Rules first; the
    model, when there is one, can add a commodity or a company the rules missed (a company only if written)."""
    commodities, company = list(parsed["commodities"]), parsed["company"]
    if ctx.model is not None:
        answer, info = ctx.model.complete_json("Extractor", SCOPE_SYSTEM, [("promise", claim)], SCOPE_SCHEMA)
        if answer is not None:
            commodities += [c for c in answer["commodities"] if c not in commodities]
            named = answer["company"].strip()
            if company is None and named.lower() != "none" and named.lower() in claim.lower():
                company = named
    others = [c for c in commodities if c != DATA_COMMODITY]
    stranger = company is not None and not _known_company(company)
    if not others and not stranger:
        return None
    what = " and ".join(others + (["this company"] if stranger else []))
    ctx.step("Extractor", "check_scope", f"promise: {claim}",
             f"out of scope: commodities {', '.join(commodities) or 'none'}; company {company or 'none'}; "
             f"the data is {DATA_COMMODITY} from {DATA_COMPANIES[0]}", status="success")
    return OUT_OF_SCOPE.format(what=what)


def extract(ctx: RunContext, claim: str) -> tuple[dict, Extracted]:
    parsed = read_claim(claim)
    parsed["out_of_scope"] = scope(ctx, claim, parsed)
    if parsed["out_of_scope"]:
        return parsed, Extracted()
    ctx.step("Extractor", "extract_claims", f"promise: {claim}",
             f"checkable parts: {', '.join(parsed['types']) or 'none'}; since {parsed['since']}",
             target="Planner" if parsed["types"] else None)
    if not parsed["types"]:
        return parsed, Extracted()

    rse = ctx.gate.ask("Extractor", "Q1", doc_id=RSE_DOC).rows
    ngo = ctx.gate.ask("Extractor", "Q1", doc_id=NGO_DOC).rows
    extracted = _read_by_model(ctx, _guarded(ctx, rse + ngo))
    if extracted is None:
        extracted = _read_by_rules(rse, ngo)
        ctx.step("Extractor", "fallback_rules", f"{len(rse)} report and {len(ngo)} NGO paragraph(s)",
                 f"scope {extracted.scope}; {len(extracted.admissions)} admission(s); "
                 f"{len(extracted.mentions)} supplier mention(s)", target="Planner", status="degraded")
    return parsed, extracted


def _guarded(ctx: RunContext, paragraphs: list[dict]) -> list[dict]:
    """The paragraphs a model may read: Meterless Scout Intent quarantines any that carries an injection.
    The rules still read every row; Scout only decides what reaches the model."""
    try:
        results = scout.guard([p["paragraph_text"] for p in paragraphs])
    except scout.ScoutUnavailable as error:
        ctx.step("Extractor", "scout_guard", f"{len(paragraphs)} paragraph(s)",
                 f"Scout not reachable ({error}): the paragraphs reach the model inside <data> tags, as before",
                 status="degraded", provider="meterless", model=scout.ENGINE)
        return paragraphs
    blocked = [(p, r) for p, r in zip(paragraphs, results) if r["level"] == "block"]
    for p, r in blocked:
        ctx.step("Extractor", "scout_quarantine", f"{p['doc_id']} {p['section']} {p['paragraph_no']}",
                 f"blocked before any model reads it: {r['reason']}", status="refused",
                 provider="meterless", model=scout.ENGINE)
    ctx.step("Extractor", "scout_guard", f"{len(paragraphs)} paragraph(s)",
             f"{len(paragraphs) - len(blocked)} pass to the model, {len(blocked)} quarantined", target="Extractor",
             provider="meterless", model=scout.ENGINE)
    return [p for p, r in zip(paragraphs, results) if r["level"] != "block"]


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
        mentions=tuple(_with_supplier_id({k: m.get(k) for k in ("doc", "paragraph_no", "quote", "supplier_name",
                                                                "cooperative_group", "year")})
                       for m in kept["mentions"]),
    )
    ctx.step("Extractor", "extract_claims",
             f"{len(paragraphs)} paragraph(s) read {len(readings)} times by {info['model']}",
             f"agreed by {AGREEMENT} of {len(readings)}: scope {extracted.scope}; "
             f"{len(extracted.admissions)} admission(s); {len(extracted.mentions)} supplier mention(s), all quoted",
             target="Planner", provider=info["provider"], model=info["model"])
    return extracted


def _with_supplier_id(mention: dict) -> dict:
    """A note may name the supplier by its registry id ("F-031") rather than by its name: the id is read by
    code from the checked quote, as the rule reader does, so the mention still finds its supplier."""
    ids = SUPPLIER_ID.findall(mention.get("quote") or "") or SUPPLIER_ID.findall(mention.get("supplier_name") or "")
    return {**mention, "supplier_id": ids[0]} if ids else mention


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
    scope = "subset" if promise and re.search(r"\bcertified\b", promise["paragraph_text"], re.I) else "all"

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


# ---- the analyst's own documents (app/userdocs.py) ---------------------------------------------------------

USER_SYSTEM = (
    "You are the Extractor of an audit of a deforestation-free claim. The data blocks are paragraphs of documents "
    "the analyst pasted, each labelled with its document id and paragraph number; the first block is the claim "
    "itself (document 'promise'). The supplier's own documents are: {company}. Third-party documents are: {third}. "
    "Report three things. scope: 'all' only if the claim covers every tonne the supplier sells with no qualifier at "
    "all; any qualifier that narrows it (a programme, a label, a certification, a region, a product line) makes it "
    "'subset'; quote the words that decide it. admissions: only in the supplier's own documents, every passage "
    "where the supplier says forest was cleared, opened or converted after 2020, whatever the wording; a denial "
    "such as 'not one hectare was cleared' is NOT an admission. mentions: only in third-party documents, every "
    "passage that ties forest clearing or forest loss to a named supplier, cooperative or farm: supplier_name is "
    "the name exactly as written, cooperative_group the group written next to it or null, year the year as a "
    "number even when written in words. Use section 'main'. Quote word for word, copied exactly from one "
    "paragraph. Never invent a paragraph, a name or a year. Empty lists are a valid answer."
)
NEGATION = re.compile(r"\b(no|not|never|zero|without|free|nor)\b", re.I)


def read_documents(ctx: RunContext, paragraphs: list[dict], roles: dict[str, str],
                   supplier_names: list[str]) -> Extracted:
    """What the analyst's documents say: Nemotron with exact quotes (3 readings, 2 must agree), else rules."""
    company = [d for d, r in roles.items() if r == "company"]
    third = [d for d, r in roles.items() if r == "third_party"]
    if ctx.model is not None:
        system = USER_SYSTEM.format(company=", ".join(company) or "none", third=", ".join(third) or "none")
        blocks = [(f"{p['doc_id']} {p['section']} {p['paragraph_no']}", p["paragraph_text"]) for p in paragraphs]
        readings, info = [], {}
        for _ in range(READINGS):
            answer, info = ctx.model.complete_json("Extractor", system, blocks, SCHEMA)
            if answer is None:
                break
            readings.append(evaluator.verify_quotes(ctx, answer, paragraphs, roles))
        if len(readings) >= AGREEMENT:
            kept = _agreed(readings)
            extracted = Extracted(
                scope=kept["scope"],
                admissions=tuple({k: a.get(k) for k in ("doc", "section", "paragraph_no", "quote", "plots", "year")}
                                 for a in kept["admissions"]),
                mentions=tuple(_with_supplier_id({k: m.get(k) for k in ("doc", "paragraph_no", "quote",
                                                                        "supplier_name", "cooperative_group",
                                                                        "year")})
                               for m in kept["mentions"]))
            ctx.step("Extractor", "extract_claims", f"{len(paragraphs)} paragraph(s) of the analyst's documents, "
                     f"read {len(readings)} times by {info['model']}",
                     f"agreed by {AGREEMENT} of {len(readings)}: scope {extracted.scope}; "
                     f"{len(extracted.admissions)} admission(s); {len(extracted.mentions)} mention(s), all quoted",
                     target="Planner", provider=info["provider"], model=info["model"])
            return extracted
    extracted = _read_documents_by_rules(paragraphs, roles, supplier_names)
    ctx.step("Extractor", "fallback_rules", f"{len(paragraphs)} paragraph(s) of the analyst's documents",
             f"scope {extracted.scope}; {len(extracted.admissions)} admission(s); "
             f"{len(extracted.mentions)} mention(s) of a supplier of the file", target="Planner", status="degraded")
    return extracted


def _read_documents_by_rules(paragraphs: list[dict], roles: dict[str, str], supplier_names: list[str]) -> Extracted:
    """The plain baseline, careful never to read a denial as an admission: clearing words, a year after 2020,
    no negation in the paragraph; a third party naming a supplier of the file with a year after 2020."""
    promise = next((p for p in paragraphs if p["doc_id"] == "promise"), None)
    scope = "subset" if promise and re.search(r"\b(certified|labelled|labeled|programme|program)\b",
                                              promise["paragraph_text"], re.I) else "all"
    admissions = tuple(
        {"doc": p["doc_id"], "section": p["section"], "paragraph_no": p["paragraph_no"], "plots": [],
         "year": _first_year(p["paragraph_text"])}
        for p in paragraphs
        if roles.get(p["doc_id"]) == "company" and p["doc_id"] != "promise"
        and ADMISSION_WORDS.search(p["paragraph_text"]) and not NEGATION.search(p["paragraph_text"])
        and (_first_year(p["paragraph_text"]) or 0) > 2020
    )
    mentions = tuple(
        {"doc": p["doc_id"], "paragraph_no": p["paragraph_no"], "supplier_name": name,
         "year": _first_year(p["paragraph_text"])}
        for p in paragraphs if roles.get(p["doc_id"]) == "third_party"
        for name in supplier_names
        if re.search(rf"\b{re.escape(name)}\b", p["paragraph_text"], re.I)
        and (_first_year(p["paragraph_text"]) or 0) > 2020
    )
    return Extracted(scope=scope, admissions=admissions, mentions=mentions)
