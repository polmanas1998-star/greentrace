"""Evaluator: the only critic. It tries to break each card before the Writer may use it.

For a card it proposes the ways it could be wrong (kit refutations R1-R9) and tests
each one with its OWN query, never trusting the Investigator's rows. A refutation that
needs data nobody planned to read sends the plan back to the Planner; a refutation
that holds blocks the card. Rule fallback = the catalogue, chosen by the card's shape.
"""
from app.agents.context import RunContext
from app.rules import CONFIRMED, CUTOFF

APPROVE, BLOCK, REVISE = "approve", "block", "revise_plan"


def evaluate(ctx: RunContext, card: dict, hypotheses: list[dict], delivery: dict | None) -> dict:
    """One decision for one card: approve, block (with the refutation that held) or revise_plan."""
    if card["code"] != "confirmed_alert_in_delivering_plot":
        if card["code"] == "sources_in_conflict":
            return _namesakes(ctx, card)
        return _decide(ctx, card, APPROVE, "no refutation applies to this kind of finding", [])

    alert_id = next(r.split(":", 1)[1] for r in card["refs"] if r.startswith("alert:"))
    tried = []

    # R3, always tried when an alert is tied to a plot: was the plot still delivering?
    if not any(h["id"] == "H5" for h in hypotheses):
        ctx.step("Evaluator", "propose_refutation", f"{card['id']}: {card['detail']}",
                 "R3 'the supplier had stopped delivering from this plot' is untested by the plan",
                 target="Planner")
        return {"card": card["id"], "decision": REVISE, "refutation": "R3",
                "reason": "R3 needs the plot's deliveries by year", "tried": tried}

    alert = ctx.gate.ask("Evaluator", "Q9", alert_id=alert_id).rows[0]
    year = int(alert["alert_date"][:4])
    tried.append(_tried(ctx, card, "R3", "supplier stopped delivering",
                        holds=not any(y >= year for y in delivery["years"]),
                        detail=f"{delivery['plot_id']} delivered in {', '.join(map(str, delivery['years']))}"))
    tried.append(_tried(ctx, card, "R4", "low-confidence alert", holds=alert["confidence"] not in CONFIRMED,
                        detail=f"{alert_id} confidence {alert['confidence']} ({alert['system']})"))
    tried.append(_tried(ctx, card, "R5", "alert date before the cut-off", holds=alert["alert_date"] <= CUTOFF,
                        detail=f"{alert_id} dated {alert['alert_date']}"))

    held = [t for t in tried if t["holds"]]
    if held:
        return _decide(ctx, card, BLOCK, f"{held[0]['id']} holds: {held[0]['detail']}", tried, target="Investigator")
    return _decide(ctx, card, APPROVE, "R3, R4 and R5 tested by own queries and rejected", tried)


def _namesakes(ctx: RunContext, card: dict) -> dict:
    """R2: is the supplier the NGO names really ours, or a namesake?"""
    supplier_id = next(r.split(":", 1)[1] for r in card["refs"] if r.startswith("supplier:"))
    name = next(r for r in ctx.gate.ask("Evaluator", "Q2", company_id=ctx.company_id).rows
                if r["supplier_id"] == supplier_id)["name"]
    namesakes = ctx.gate.ask("Evaluator", "Q8", name=name).rows
    tried = [_tried(ctx, card, "R2", "a namesake cooperative", holds=False,
                    detail=f"{len(namesakes)} supplier(s) named {name}: "
                           f"{', '.join(r['supplier_id'] + ' ' + r['cooperative_group'] for r in namesakes)}")]
    return _decide(ctx, card, APPROVE, "the conflict stands: the claim cannot be settled without the supplier",
                   tried)


def _tried(ctx: RunContext, card: dict, refutation: str, statement: str, *, holds: bool, detail: str) -> dict:
    ctx.step("Evaluator", "refute_hypothesis", f"{card['id']}: {refutation} '{statement}'",
             f"{'HOLDS' if holds else 'rejected'}: {detail}", target="Evaluator",
             status="degraded" if ctx.degraded else "success")
    return {"id": refutation, "statement": statement, "holds": holds, "detail": detail}


def _decide(ctx: RunContext, card: dict, decision: str, reason: str, tried: list[dict],
            target: str = "Writer") -> dict:
    action = {APPROVE: "approve", BLOCK: "block"}[decision]
    ctx.step("Evaluator", action, f"{card['id']} {card['code']}", reason, target=target,
             confidence=0.9 if decision == APPROVE else 0.6, status="degraded" if ctx.degraded else "success")
    return {"card": card["id"], "decision": decision, "reason": reason, "tried": tried}
