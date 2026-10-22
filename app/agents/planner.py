"""Planner: for each claim part, the hypotheses that must hold and the queries that test them.

Rule fallback = a fixed catalogue per claim type (kit, section 4). On purpose the
first plan does not test whether a plot was still delivering when an alert fell:
the Evaluator always asks (refutation R3), so the loop back to the Planner is real
and shows in the trace, with or without a model. A plot drawn by the analyst adds H6:
the reference maps are read inside its outline (Q10).
"""
from app.agents.context import RunContext

CATALOGUE = {
    "coverage": [
        {"id": "H1", "part": "coverage", "statement": "every tonne of the fiscal year is traced to a plot",
         "queries": ["Q2", "Q6", "Q3"]},
    ],
    "no_deforestation": [
        {"id": "H2", "part": "no_deforestation",
         "statement": "no confirmed alert after 2020-12-31 inside a delivering plot",
         "queries": ["Q0", "Q3", "Q4"]},
        {"id": "H3", "part": "plausibility", "statement": "each plot can produce what it declares, once",
         "queries": ["Q6", "Q7"]},
        {"id": "H4", "part": "scope", "statement": "the report claims all cocoa, not a subset",
         "queries": ["Q1"]},
    ],
}
DRAWN_PLOT = {"id": "H6", "part": "no_deforestation",
              "statement": "no forest the EU map saw in 2020 was lost inside the drawn plot while it delivered",
              "queries": ["Q10"]}
REVISIONS = {
    "R3": {"id": "H5", "part": "no_deforestation",
           "statement": "the plot was still delivering in the year of the alert, or of the loss", "queries": ["Q5"]},
}


def plan(ctx: RunContext, claim_types: list[str]) -> list[dict]:
    hypotheses = [h for t in claim_types for h in CATALOGUE.get(t, [])]
    if ctx.notes.get("declared_plot"):
        hypotheses.append(DRAWN_PLOT)
    ctx.step("Planner", "plan_hypotheses", f"claim parts: {', '.join(claim_types)}",
             "; ".join(f"{h['id']} {h['statement']}" for h in hypotheses), target="Investigator",
             status="degraded" if ctx.degraded else "success")
    return hypotheses


def revise(ctx: RunContext, hypotheses: list[dict], refutation: str, reason: str) -> list[dict]:
    added = REVISIONS[refutation]
    if any(h["id"] == added["id"] for h in hypotheses):
        return hypotheses
    ctx.step("Planner", "revise_plan", f"Evaluator: {reason}", f"added {added['id']} {added['statement']}",
             target="Investigator", status="degraded" if ctx.degraded else "success")
    return hypotheses + [added]
