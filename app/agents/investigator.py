"""Investigator: runs the plan through the data gate, gathers the evidence, writes the cards.

Few model calls here (kit, section 4): the queries come from the plan, the checks are
code. Every card it writes in the World Model depends on the entities it was read
from, so changing one value later invalidates exactly the cards built on it.
"""
from typing import Mapping, Optional

from app.agents.context import RunContext
from app.checks import geometry
from app.memory.world_model import Source
from app.queries import SourceUnavailable
from app.rules import Evidence, Extracted, decide

MARGIN_DEG = 0.01  # about 1 km around the supply area


def gather(ctx: RunContext, hypotheses: list[dict], extracted: Extracted) -> Evidence:
    asked = {q for h in hypotheses for q in h["queries"]}
    ask = ctx.gate.ask
    answers: dict[str, Optional[list[dict]]] = {}

    if "Q0" in asked:
        answers["Q0"] = ask("Investigator", "Q0").rows
    answers["Q2"] = ask("Investigator", "Q2", company_id=ctx.company_id).rows
    if not answers["Q2"]:
        raise ValueError(f"unknown company: {ctx.company_id}")
    fiscal_year = answers["Q2"][0]["fiscal_year"]

    plots = answers["Q3"] = ask("Investigator", "Q3",
                                supplier_ids=[r["supplier_id"] for r in answers["Q2"]]).rows
    answers["Q6"] = ask("Investigator", "Q6", company_id=ctx.company_id, year_from=2021, year_to=fiscal_year).rows
    if "Q7" in asked:
        ask("Investigator", "Q7", company_id=ctx.company_id)

    located = [p for p in plots if geometry.coordinates_valid(p)]
    if "Q4" in asked and located:
        try:
            answers["Q4"] = ask("Investigator", "Q4",
                                lat_min=round(min(p["lat"] for p in located) - MARGIN_DEG, 6),
                                lat_max=round(max(p["lat"] for p in located) + MARGIN_DEG, 6),
                                lon_min=round(min(p["lon"] for p in located) - MARGIN_DEG, 6),
                                lon_max=round(max(p["lon"] for p in located) + MARGIN_DEG, 6)).rows
        except SourceUnavailable:
            pass  # no Q4 answer: the alerts are unknown, and rule 1 says so

    return evidence_from_answers(answers, extracted, ctx.company_id)


def evidence_from_answers(answers: Mapping[str, Optional[list[dict]]], extracted: Extracted,
                          company_id: str) -> Evidence:
    """The evidence from the Investigator's rows, by query id. Pure: verify.py rebuilds it from a receipt.

    Q2 gives the company and its suppliers, Q3 the plots, Q6 the deliveries, Q0 the data-quality
    issues, Q4 the alerts (no Q4 answer: the alert source did not answer, or nothing was located).
    """
    suppliers_rows = answers.get("Q2") or []
    if not suppliers_rows:
        raise ValueError(f"unknown company: {company_id}")
    first = suppliers_rows[0]
    company = {"company_id": company_id, "fiscal_year": first["fiscal_year"], "total_tonnes": first["total_tonnes"]}
    suppliers = [{k: r[k] for k in ("supplier_id", "name", "cooperative_group")} for r in suppliers_rows]
    return Evidence(company=company, suppliers=suppliers, plots=answers.get("Q3") or [],
                    deliveries=answers.get("Q6") or [], alerts=answers.get("Q4"),
                    dq_issues=answers.get("Q0") or [], extracted=extracted)


def write_cards(ctx: RunContext, evidence: Evidence) -> list[dict]:
    """One card per finding, stored in the World Model with what it depends on."""
    findings = decide(evidence)["findings"]
    rows_by_ref = _rows_by_ref(evidence)
    cards = []
    for number, finding in enumerate(findings, start=1):
        depends = []
        for ref in finding["refs"]:
            kind, _, key = ref.partition(":")
            if kind in ("alert", "plot", "supplier"):
                attrs = rows_by_ref.get(ref, {})
                depends.append(ctx.memory.upsert_entity(kind, key, attrs, Source("Investigator", "zetaris"))["id"])
        about = depends[0] if depends else ctx.memory.upsert_entity(
            "claim", finding["part"], {"part": finding["part"]}, Source("Investigator"))["id"]
        fact = ctx.memory.assert_fact(about, finding["code"], finding["detail"], source=Source("Investigator"),
                                      confidence=_confidence(finding), depends_on=depends or [about])
        cards.append({"id": f"E-{number:02d}", "fact": fact["id"], **finding})
    ctx.step("Investigator", "write_card", f"{len(findings)} finding(s) from the checks",
             ", ".join(f"{c['id']} {c['code']}" for c in cards) or "no issue found", target="Evaluator")
    return cards


def check_still_delivering(ctx: RunContext, card: dict) -> dict:
    """H5, added by the Planner after the Evaluator's request: Q5 on the card's plot."""
    plot_id = next(r.split(":", 1)[1] for r in card["refs"] if r.startswith("plot:"))
    rows = ctx.gate.ask("Investigator", "Q5", plot_id=plot_id).rows
    return {"plot_id": plot_id, "years": sorted({r["year"] for r in rows if r["tonnes"] > 0}), "rows": rows}


def _rows_by_ref(e: Evidence) -> dict[str, dict]:
    rows = {f"plot:{p['plot_id']}": {"area_ha": p["area_ha"], "supplier": p["supplier_id"]} for p in e.plots}
    rows.update({f"alert:{a['alert_id']}": {"date": a["alert_date"], "confidence": a["confidence"]}
                 for a in e.alerts or []})
    rows.update({f"supplier:{s['supplier_id']}": {"name": s["name"]} for s in e.suppliers})
    return rows


def _confidence(finding: dict) -> float:
    return {2: 0.9, 1: 0.7, 3: 0.6}.get(finding["rule"], 0.5)
