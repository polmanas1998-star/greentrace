"""Defense: the Evaluator, not a copy of it. Its record of the run becomes the counter-arguments.

Against an argument for a claim part, the Defense puts the cards the rules wrote on that part, as
the World Model holds them, with the query and the rows each rests on. Against an answer that
raises a refutation (R1-R9), it puts the Evaluator's own test of that refutation: the query it
asked, its rows, and whether the evidence held. Against an answer from the company's report, the
card's rows again: the rules still find the same thing, whatever the report says. No query is
asked again and no test is run again.

attack_log() is the Evaluator's journal of attacks (Adversarial Attack Log): every refutation it
tried on every card, the query that tested it, and how it ended.
"""
from collections import Counter
from typing import Any, Optional

from app.agents import evaluator
from app.agents.evaluator import CATALOGUE
from app.court import pieces as P
from app.memory.world_model import entity_id

HELD, REFUTED, UNAVAILABLE = "evidence_held", "refuted_evidence", "source_unavailable"
QUESTIONS = {
    "R1": "does alert {alert} lie in another plot than {plot}?",
    "R2": "is supplier {supplier} a namesake, not ours?",
    "R3": "had {plot} stopped delivering before the {event}?",
    "R4": "is alert {alert} of low confidence?",
    "R5": "is alert {alert} dated on or before the cut-off, 2020-12-31?",
    "R6": "does the card rest on a single source, with no independent issuer?",
    "R8": "is the cocoa of {plot} outside the volume the claim covers?",
    "R9": "do the reference maps, read again, show no loss of 2020 forest inside {plot}?",
}


def against_part(ctx: Any, cards: list[dict], findings: set, number: int) -> list[dict]:
    """The cards on one claim part, one counter-argument each (ids from D{number} on)."""
    return [_card_counter(ctx, card, findings, f"D{number + i}") for i, card in enumerate(cards)]


def against_answer(ctx: Any, card: dict, angle: str, findings: set, counter_id: str) -> dict:
    """The Evaluator's own test of the refutation the answer raises, or the card re-read for a report passage."""
    if angle not in CATALOGUE:
        counter = _card_counter(ctx, card, findings, counter_id)
        return {**counter, "detail": f"rule {card['rule']} still finds: {card['detail']}"}
    tried = next(t for t in card["tried"] if t["id"] == angle)
    query_id, answer, rows = P.refutation_evidence(ctx, card, angle)
    holds = not tried["holds"]
    return {"id": counter_id, "card": card["id"], "refutation": angle, "query_id": query_id,
            "sql": answer.sql if answer else None, "rows": rows, "holds": holds,
            "detail": f"{angle} {'rejected' if holds else 'holds'} by the Evaluator's own test: {tried['detail']}"}


def _card_counter(ctx: Any, card: dict, findings: set, counter_id: str) -> dict:
    query_id, answer, rows = P.card_evidence(ctx, card)
    status, said = _fact(ctx, card)
    review = card.get("review")
    blocked = bool(review) and review["decision"] == evaluator.BLOCK
    still = (card["code"], tuple(card["refs"])) in findings
    holds = still and not blocked and status in ("valid", None)
    tested = ", ".join(t["id"] for t in card.get("tried", []))
    detail = f"{card['id']} {card['code']}, rule {card['rule']}: {said}"
    if review is None:
        detail += "; not reviewed by the Evaluator"
    elif blocked:
        detail += f"; blocked by the Evaluator: {review['reason']}"
    elif tested:
        detail += f"; the Evaluator tried {tested} with its own queries and rejected them"
    if status not in ("valid", None):
        detail += f"; its World Model fact is {status}"
    return {"id": counter_id, "card": card["id"], "refutation": None, "query_id": query_id,
            "sql": answer.sql if answer else None, "rows": rows, "holds": holds, "detail": detail}


def _fact(ctx: Any, card: dict) -> tuple[Optional[str], str]:
    """The card's fact as the World Model holds it: (status, value). (None, card detail) if it does not answer."""
    about = next((entity_id(kind, key) for kind, _, key in (ref.partition(":") for ref in card["refs"])
                  if kind in ("alert", "plot", "supplier")), None) or entity_id("claim", card["part"])
    try:
        facts = ctx.memory.view("facts", about, by="Evaluator")
    except Exception:  # noqa: BLE001 - the server stopped: its trace line says so, the card speaks for itself
        return None, card["detail"]
    fact = next((f for f in facts if f["id"] == card["fact"]), None)
    if fact is None:
        return None, card["detail"]
    return fact["status"], str(fact.get("value") or card["detail"])


# ---- the journal of attacks ------------------------------------------------------------------

def attack_log(run: Any) -> dict:
    """Every refutation the Evaluator tried, card by card, from its reviews and the run's trace."""
    lines = P.trace_of(run)
    if lines is None:
        raise FileNotFoundError(f"the trace of run {run.ctx.run_id} is not in its log folder: "
                                f"the attack log is computed from it")
    # The Evaluator sends the plan back when a refutation needs data nobody read (R3 and the deliveries):
    # the attack ran again after the Planner added it.
    retries = Counter((line["input_summary"].split(" ", 1)[0], line["output_summary"].split(" ", 1)[0])
                      for line in lines if line.get("agent_name") == "Evaluator"
                      and line.get("action") == "request_revision")
    attacks = []
    for card in run.cards:
        review = run.reviews.get(card["id"])
        tried = review["tried"] if review else []
        for test in tried:
            query_id, answer, _ = P.refutation_evidence(run.ctx, card, test["id"])
            result = UNAVAILABLE if query_id and answer is None else REFUTED if test["holds"] else HELD
            attacks.append(_attack(card, test["id"], query_id, result, len(answer.rows) if answer else 0,
                                   retries[(card["id"], test["id"])]))
        if review is None:  # sent back to the Planner, and still untested after the Evaluator's last round
            attacks += [_attack(card, refutation, None, UNAVAILABLE, 0, count)
                        for (card_id, refutation), count in retries.items() if card_id == card["id"]]
    return {"run_id": run.ctx.run_id, "attacks": attacks}


def _attack(card: dict, refutation: str, query_id: Optional[str], result: str, rows: int, retry_count: int) -> dict:
    refs = dict(ref.split(":", 1) for ref in card["refs"] if ":" in ref)
    alert = refs.get("alert")
    question = QUESTIONS.get(refutation, CATALOGUE.get(refutation, refutation) + "?").format(
        alert=alert or "the alert", plot=refs.get("plot", "the plot"), supplier=refs.get("supplier", "the supplier"),
        event=f"alert {alert}" if alert else "loss of forest")
    return {"refutation": refutation, "question": f"{card['id']}: {question}", "query_id": query_id,
            "result": result, "rows": rows, "retry_count": retry_count}
