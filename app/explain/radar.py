"""Contradiction Radar: what each extracted claim needs, and which missing document could turn the verdict.

Every affirmation the run extracted lands in one of three lists:
  checkable_now   a query exists and the data settled it (a hypothesis of the plan, with its queries;
                  a company admission or an NGO mention the rules could test)
  needs_document  a query exists, but the part stays UNVERIFIABLE until a document (or data) arrives:
                  which one is named, from the finding that holds it open (DOCUMENTS)
  out_of_scope    nothing in Q0-Q10 reads it: a clause of the promise no checkable part matches, or an
                  NGO mention dated before the EUDR cut-off

Each missing document is then tried, never guessed, against the verdict with the outcomes it could
establish, all computed by the rules on a copy of the evidence:
  flip        every single change app/explain/flip.py tried on the entities the document concerns
  confirmed   for an alert the rules hold as unconfirmed: flip.apply raises its confidence to high
  settled     the findings it answers resolved in the claim's favour (the others stand)
"would_flip" is true when one of these outcomes gives another verdict than the run's, false when none does.
"""
import re
from typing import Any, Optional

from app.agents.extractor import CHECKABLE
from app.explain.flip import apply, compute_flip
from app.receipt import decision_for
from app.rules import CONFIRMED, CONTRADICTED, FIRST_YEAR_AFTER_CUTOFF, PROVEN, UNVERIFIABLE, Evidence, decide

# finding code -> (document or data, what is missing). company_admission needs nothing: the company said it.
DOCUMENTS = {
    "confirmed_alert_in_delivering_plot": ("document", "a field report on {plot}"),
    "unconfirmed_alert_in_plot": ("document", "a field report on {plot}"),
    "forest_loss_in_delivering_plot": ("document", "a field report on {plot}"),
    "forest_loss_below_threshold": ("document", "a field report on {plot}"),
    "tree_cover_loss_outside_2020_forest": ("document", "a field report on {plot}"),
    "tree_cover_loss_no_eu_forest_map": ("document", "a field report on {plot}"),
    "alert_near_point_plot": ("document", "the polygon of {plot}"),
    "polygon_missing": ("document", "the polygon of {plot}"),
    "invalid_coordinates": ("document", "corrected coordinates of {plot}"),
    "volume_not_traced": ("document", "the plot registry of {supplier}"),
    "impossible_yield": ("document", "delivery records naming the plots the volume of {plot} came from"),
    "plot_declared_twice": ("document", "land records showing which supplier farms {plot}"),
    "sources_in_conflict": ("document", "the plot records of {supplier} for the year the NGO cites"),
    "narrower_scope": ("document", "a restated claim covering all the cocoa bought"),
    "source_unavailable": ("data", "an answer from the alert source"),
    "no_independent_source": ("data", "alert data from a source independent of the company, covering the supply area"),
    "no_reference_data": ("data", "reference maps covering {plot}"),
}
OUT_OF_REACH = ("no query in Q0-Q10 reads this: GreenTrace checks traced volume, deforestation after "
                "2020-12-31, yields and the scope of the promise")
_CLAUSES = re.compile(r"[.;!?]+\s*|,\s*(?:and|but|while|with)\s+")


def radar(run: Any) -> dict[str, Any]:
    """GET /runs/{run_id}/radar."""
    claim = (run.request.get("input") or run.request).get("claim", "")
    decision = decision_for(run.evidence, [c["id"] for c in run.cards if c["id"] not in run.reviews])
    return {"run_id": run.ctx.run_id, **build(claim, run.evidence, run.hypotheses, decision)}


def build(claim: str, evidence: Evidence, hypotheses: list[dict], decision: Optional[dict] = None) -> dict[str, Any]:
    """The radar of one claim on one evidence: pure, same input same output."""
    decision = decision or decide(evidence)
    verdict, findings = decision["verdict"], decision["findings"]
    parts = {p["claim_part"]: p for p in decision["parts"]}
    missing = missing_documents(evidence, findings, verdict)
    claims: list[dict[str, Any]] = []

    for h in hypotheses:
        part = parts.get(h["part"], {})
        needed = [m["document"] for m in missing if h["part"] in m["claim_parts"]]
        open_ = part.get("verdict") == UNVERIFIABLE and needed
        claims.append({"id": h["id"], "text": h["statement"], "from": "promise", "claim_part": h["part"],
                       "status": "needs_document" if open_ else "checkable_now", "queries": list(h["queries"]),
                       "verdict": part.get("verdict"), "needs": needed if open_ else []})
    for n, clause in enumerate(c for c in _CLAUSES.split(claim) if c.strip()):
        if not any(pattern.search(clause) for pattern in CHECKABLE.values()):
            claims.append({"id": f"P{n + 1}", "text": clause.strip(), "from": "promise", "claim_part": None,
                           "status": "out_of_scope", "queries": [], "verdict": None, "reason": OUT_OF_REACH})
    claims += _from_documents(evidence, findings, missing)

    by_status = {s: [c for c in claims if c["status"] == s] for s in ("checkable_now", "needs_document", "out_of_scope")}
    return {"verdict": verdict, **by_status, "missing_documents": missing,
            "summary": {"checkable_now": len(by_status["checkable_now"]),
                        "needs_document": len(by_status["needs_document"]),
                        "out_of_scope": len(by_status["out_of_scope"]),
                        "documents_that_would_flip": sum(1 for m in missing if m["would_flip"])}}


def _from_documents(evidence: Evidence, findings: list[dict], missing: list[dict]) -> list[dict]:
    """What the Extractor read in the reports: admissions, NGO mentions, and a narrower scope."""
    out = []
    for n, admission in enumerate(evidence.extracted.admissions, start=1):
        where = f"{admission['doc']} {admission.get('section', 'main')} paragraph {admission['paragraph_no']}"
        out.append({"id": f"D-A{n}", "text": f"{where} admits clearing in {admission.get('year')}"
                    + (f" ({', '.join(admission['plots'])})" if admission.get("plots") else ""),
                    "from": admission["doc"], "kind": "admission", "claim_part": "no_deforestation",
                    "status": "checkable_now", "queries": ["Q1"], "verdict": CONTRADICTED,
                    "quote": admission.get("quote")})
    for n, mention in enumerate(evidence.extracted.mentions, start=1):
        who = mention.get("supplier_id") or mention.get("supplier_name")
        doc_ref = f"doc:{mention['doc']}:main:{mention['paragraph_no']}"
        item = {"id": f"D-M{n}", "text": f"{mention['doc']} paragraph {mention['paragraph_no']} reports clearing"
                + (f" by {who}" if who else "") + (f" in {mention['year']}" if mention.get("year") else ""),
                "from": mention["doc"], "kind": "mention", "claim_part": "no_deforestation",
                "quote": mention.get("quote")}
        conflict = [m["document"] for m in missing if any(doc_ref in f["refs"] for f in m["findings"])]
        if (mention.get("year") or 0) < FIRST_YEAR_AFTER_CUTOFF:
            out.append({**item, "status": "out_of_scope", "queries": [], "verdict": None,
                        "reason": f"about {mention.get('year') or 'no year'}: only clearing after 2020-12-31 counts"})
        elif not who:
            out.append({**item, "status": "needs_document", "queries": ["Q2", "Q8"], "verdict": None,
                        "needs": [f"the names of the suppliers or plots {mention['doc']} refers to"]})
        elif conflict:
            out.append({**item, "status": "needs_document", "queries": ["Q2", "Q8", "Q4"], "verdict": UNVERIFIABLE,
                        "needs": conflict})
        else:
            out.append({**item, "status": "checkable_now", "queries": ["Q2", "Q8", "Q4"], "verdict": None})
    if evidence.extracted.scope != "all":
        needed = [m["document"] for m in missing if "scope" in m["claim_parts"]]
        out.append({"id": "D-S1", "text": "the report's own promise covers only part of the cocoa bought",
                    "from": "report", "kind": "scope", "claim_part": "scope", "status": "needs_document",
                    "queries": ["Q1"], "verdict": UNVERIFIABLE, "needs": needed})
    return out


# ---- the missing documents, each tried against the verdict ---------------------------------------------

def missing_documents(evidence: Evidence, findings: list[dict], verdict: str) -> list[dict[str, Any]]:
    """One entry per document (or data) a finding asks for, merged when several ask for the same, flip first."""
    flip = compute_flip(evidence)
    docs: dict[str, dict[str, Any]] = {}
    for finding in findings:
        spec = DOCUMENTS.get(finding["code"])
        if spec is None:
            continue
        kind, template = spec
        refs = [tuple(ref.split(":", 1)) for ref in finding["refs"]]
        plots = [key for k, key in refs if k == "plot"] or [None]
        suppliers = ([key for k, key in refs if k == "supplier"]
                     or (list(_untraced(evidence)) if finding["code"] == "volume_not_traced" else []) or [None])
        targets = ([(None, s) for s in suppliers] if "{supplier}" in template
                   else [(p, None) for p in plots])
        for plot, supplier in targets:
            text = template.format(plot=plot or "the plot", supplier=supplier or "the supplier")
            entry = docs.setdefault(text, {"document": text, "kind": kind, "concerns": [], "claim_parts": [],
                                           "findings": []})
            concerns = list(finding["refs"]) + ([f"supplier:{supplier}"] if supplier else [])
            entry["concerns"] = list(dict.fromkeys(entry["concerns"] + concerns))
            entry["claim_parts"] = list(dict.fromkeys(entry["claim_parts"] + [finding["part"]]))
            if finding not in entry["findings"]:
                entry["findings"].append(finding)
    out = []
    for entry in docs.values():
        tried = _outcomes(evidence, entry, findings, flip)
        flips = [t for t in tried if t["verdict"] != verdict]
        out.append({**entry, "findings": [{"code": f["code"], "refs": list(f["refs"]), "verdict": f["verdict"]}
                                          for f in entry["findings"]],
                    "would_flip": bool(flips), "verdict_if": sorted({t["verdict"] for t in flips}),
                    "tried": tried})
    return sorted(out, key=lambda m: (not m["would_flip"], m["kind"] != "document", m["document"]))


def _outcomes(evidence: Evidence, entry: dict, findings: list[dict], flip: dict) -> list[dict[str, Any]]:
    concerns = set(entry["concerns"])
    tried = [{"basis": "flip", "outcome": f"{t['change']['entity']} {t['change']['field']}: "
                                          f"{t['change']['from']} -> {t['change']['to']}", "verdict": t["verdict"]}
             for t in flip["tried"]
             if t["change"]["entity"] in concerns or t["change"].get("relative_to") in concerns]
    for alert in (a for a in evidence.alerts or [] if f"alert:{a['alert_id']}" in concerns):
        if alert["confidence"] not in CONFIRMED:
            change = {"entity": f"alert:{alert['alert_id']}", "field": "confidence", "from": alert["confidence"],
                      "to": "high"}
            tried.append({"basis": "confirmed", "outcome": f"the document confirms alert {alert['alert_id']} "
                                                           f"(confidence {alert['confidence']} -> high)",
                          "verdict": decide(apply(evidence, change))["verdict"]})
    left = [f for f in findings if f not in entry["findings"]]
    tried.append({"basis": "settled", "outcome": f"{len(entry['findings'])} finding(s) settled in the claim's favour",
                  "verdict": _overall(left)})
    return tried


def _overall(findings: list[dict]) -> str:
    """The verdict the rules give these findings: a part takes its worst finding, the claim its worst part."""
    verdicts = {f["verdict"] for f in findings}
    return CONTRADICTED if CONTRADICTED in verdicts else UNVERIFIABLE if UNVERIFIABLE in verdicts else PROVEN


def _untraced(evidence: Evidence) -> dict[str, float]:
    """supplier_id -> tonnes of the fiscal year that no plot of the registry traces."""
    known, year = {p["plot_id"] for p in evidence.plots}, evidence.company["fiscal_year"]
    totals: dict[str, float] = {}
    for d in evidence.deliveries:
        if d["year"] == year and d["plot_id"] not in known and d["tonnes"] > 0:
            totals[d["supplier_id"]] = totals.get(d["supplier_id"], 0.0) + d["tonnes"]
    return dict(sorted(totals.items()))
