"""Evaluator: the only critic. It tries to break each card before the Writer may use it.

For a card it proposes the ways it could be wrong (kit refutations R1-R9) and tests
each one with its OWN query, never trusting the Investigator's rows. A refutation that
needs data nobody planned to read sends the plan back to the Planner; a refutation
that holds blocks the card. Rule fallback = the catalogue, chosen by the card's shape.
"""
import json

from app.agents.context import RunContext
from app.checks import geometry, landcover
from app.rules import CONFIRMED, CONTRADICTED, CUTOFF, FIRST_YEAR_AFTER_CUTOFF, map_check
from app.world.texts import NGO_DOC, RSE_DOC

APPROVE, BLOCK, REVISE = "approve", "block", "revise_plan"


def verify_quotes(ctx: RunContext, answer: dict, paragraphs: list[dict], roles: dict | None = None) -> dict:
    """Keep only what the model quoted word for word from the paragraph it cites (kit: exact quote rule).

    roles: doc_id -> "company" (may admit clearing) or "third_party" (may name a supplier); by default
    the demo's two documents, the company's report and the NGO note."""
    roles = roles or {RSE_DOC: "company", NGO_DOC: "third_party"}
    text = {(p["doc_id"], p["paragraph_no"], p["section"]): p["paragraph_text"] for p in paragraphs}

    def found(item: dict, section: str | None = None) -> bool:
        quote = " ".join(str(item.get("quote") or "").split())
        doc = str(item.get("doc") or "").split(" ")[0]  # the model sometimes copies the whole label
        item["doc"] = doc
        sections = [section] if section else ["main", "annex"]
        return bool(quote) and any(quote in " ".join(text.get((doc, item.get("paragraph_no"), s), "").split())
                                   for s in sections)

    kept = {"scope": answer["scope"], "admissions": [], "mentions": []}
    rejected = []
    for kind in ("admissions", "mentions"):
        for item in answer[kind]:
            ok = found(item, item.get("section")) if kind == "admissions" else found(item, "main")
            if kind == "admissions":
                # Only the company can admit, and only clearing after the cut-off counts.
                ok = ok and roles.get(item["doc"]) == "company" and (item.get("year") or 0) >= FIRST_YEAR_AFTER_CUTOFF
            else:
                ok = ok and roles.get(item["doc"]) == "third_party"
            (kept[kind] if ok else rejected).append(item)
    scope_quote = " ".join(str(answer.get("scope_quote") or "").split())
    if answer["scope"] != "all" and not any(scope_quote and scope_quote in " ".join(t.split()) for t in text.values()):
        rejected.append({"quote": answer.get("scope_quote"), "scope": answer["scope"]})
        kept["scope"] = "all"
    ctx.step("Evaluator", "block" if rejected else "approve",
             f"{len(answer['admissions']) + len(answer['mentions'])} quoted item(s) from the Extractor",
             f"{len(rejected)} item(s) dropped: quote not word for word in the cited paragraph, admission not "
             f"from the company or before 2021, or mention not from the NGO" if rejected
             else "every quote found word for word in its paragraph", target="Extractor" if rejected else "Planner")
    return kept


# The ways a card can be wrong (kit, section 3.1), each tested by code with the Evaluator's own query.
CATALOGUE = {
    "R1": "the alert lies in another plot than the one named",
    "R2": "the supplier named is a namesake, not ours",
    "R3": "the plot had stopped delivering before the alert, or the loss",
    "R4": "the alert is of low confidence",
    "R5": "the alert is dated before the cut-off",
    "R6": "a single source, with no independent issuer",
    "R8": "the plot's cocoa is not part of the volume the claim covers",
    "R9": "the reference maps, read again, do not show this loss of 2020 forest inside the outline",
}
APPLIES = {
    "confirmed_alert_in_delivering_plot": ["R1", "R3", "R4", "R5", "R6", "R8"],
    "sources_in_conflict": ["R2", "R6"],
    "forest_loss_in_delivering_plot": ["R3", "R6", "R9"],
}
PROPOSE = (
    "You are the Evaluator of an audit. You receive the STRUCTURE of one evidence card (its kind and the kinds "
    "of records it rests on), never the values. Propose the ways this card could be wrong: pick up to 4 ids from "
    "the catalogue given in the data, most likely first, each with a one-line reason; add at most 2 other doubts "
    "that the catalogue does not cover, in plain words. Do not judge the card yourself: code will test each id."
)
PROPOSE_SCHEMA = {
    "type": "object", "required": ["refutations", "other_doubts"], "additionalProperties": False,
    "properties": {
        "refutations": {"type": "array", "maxItems": 4, "items": {
            "type": "object", "required": ["id", "why"], "additionalProperties": False,
            "properties": {"id": {"type": "string", "enum": sorted(CATALOGUE)},
                           "why": {"type": "string", "maxLength": 200}}}},
        "other_doubts": {"type": "array", "maxItems": 2, "items": {"type": "string", "maxLength": 200}},
    },
}


def evaluate(ctx: RunContext, card: dict, hypotheses: list[dict], delivery: dict | None) -> dict:
    """One decision for one card: approve, block (with the refutation that held) or revise_plan."""
    applicable = APPLIES.get(card["code"])
    if not applicable:
        return _decide(ctx, card, APPROVE, "a fact read directly from the data: no refutation applies", [])

    # R3 is always tried when an alert is tied to a plot; if the plan never read the deliveries, send it back.
    if "R3" in applicable and not any(h["id"] == "H5" for h in hypotheses):
        ctx.step("Evaluator", "propose_refutation", f"{card['id']}: {card['detail']}",
                 f"R3 '{CATALOGUE['R3']}' is untested by the plan", target="Planner")
        return {"card": card["id"], "decision": REVISE, "refutation": "R3",
                "reason": "R3 needs the plot's deliveries by year", "tried": []}

    chosen, doubts = _propose(ctx, card, applicable)
    tried = [_test(ctx, card, refutation, delivery) for refutation in chosen]
    held = [t for t in tried if t["holds"]]
    if held:
        return {**_decide(ctx, card, BLOCK, f"{held[0]['id']} holds: {held[0]['detail']}", tried,
                          target="Investigator"), "doubts": doubts}
    names = ", ".join(t["id"] for t in tried)
    return {**_decide(ctx, card, APPROVE, f"{names} tested by own queries and rejected", tried), "doubts": doubts}


def _propose(ctx: RunContext, card: dict, applicable: list[str]) -> tuple[list[str], list[str]]:
    """Nemotron picks which refutations to try from the card's structure; code keeps only applicable ones."""
    always = ["R3"] if "R3" in applicable else []
    if ctx.model is not None:
        structure = {"kind": card["code"], "claim_part": card["part"],
                     "rests_on": sorted({ref.split(":", 1)[0] for ref in card["refs"]})}
        catalogue = {k: CATALOGUE[k] for k in applicable}
        answer, info = ctx.model.complete_json(
            "Evaluator", PROPOSE, [("card structure", json.dumps(structure)), ("catalogue", json.dumps(catalogue))],
            PROPOSE_SCHEMA)
        if answer is not None:
            picked = [r["id"] for r in answer["refutations"] if r["id"] in applicable]
            chosen = always + [r for r in dict.fromkeys(picked) if r not in always]
            ctx.step("Evaluator", "propose_refutation", f"{card['id']} structure only: {structure['kind']}",
                     f"{', '.join(chosen)}" + (f"; other doubts: {'; '.join(answer['other_doubts'])}"
                                                if answer["other_doubts"] else ""),
                     target="Evaluator", provider=info["provider"], model=info["model"])
            return chosen, list(answer["other_doubts"])
    chosen = always + [r for r in applicable if r not in always]
    ctx.step("Evaluator", "propose_refutation", f"{card['id']} {card['code']}",
             f"catalogue by card shape: {', '.join(chosen)}", target="Evaluator",
             status="degraded" if ctx.degraded else "success")
    return chosen, []


def _test(ctx: RunContext, card: dict, refutation: str, delivery: dict | None) -> dict:
    refs = dict(r.split(":", 1) for r in card["refs"])
    statement = CATALOGUE[refutation]
    if refutation in ("R4", "R5"):
        alert = ctx.gate.ask("Evaluator", "Q9", alert_id=refs["alert"]).rows[0]
        if refutation == "R4":
            return _tried(ctx, card, "R4", statement, holds=alert["confidence"] not in CONFIRMED,
                          detail=f"{alert['alert_id']} confidence {alert['confidence']} ({alert['system']})")
        return _tried(ctx, card, "R5", statement, holds=alert["alert_date"] <= CUTOFF,
                      detail=f"{alert['alert_id']} dated {alert['alert_date']}")
    if refutation == "R3" and "cells" in refs:
        measured = landcover.measure(_drawn(ctx, refs["plot"]), _own_cells(ctx, refs["plot"]))
        lost = sorted(int(y) for y in measured["forest_loss_ha_by_year"] or {} if int(y) > landcover.CUTOFF_YEAR)
        return _tried(ctx, card, "R3", statement, holds=not lost or not any(y >= lost[0] for y in delivery["years"]),
                      detail=f"{delivery['plot_id']} delivered in {', '.join(map(str, delivery['years']))}; "
                             f"2020 forest first lost there in {lost[0] if lost else 'no year'} (own Q10)")
    if refutation == "R3":
        alert = ctx.gate.ask("Evaluator", "Q9", alert_id=refs["alert"]).rows[0]
        year = int(alert["alert_date"][:4])
        return _tried(ctx, card, "R3", statement, holds=not any(y >= year for y in delivery["years"]),
                      detail=f"{delivery['plot_id']} delivered in {', '.join(map(str, delivery['years']))}")
    if refutation == "R9":
        plot, cells = _drawn(ctx, refs["plot"]), _own_cells(ctx, refs["plot"])
        again = map_check(plot, cells, set(delivery["years"]))
        return _tried(ctx, card, "R9", statement, holds=again is None or again.verdict != CONTRADICTED,
                      detail=f"own Q10, {len(cells)} cells re-read around {plot['plot_id']}: "
                             + (again.detail if again else "no loss after 2020 inside the outline"))
    if refutation == "R1":
        return _other_plot(ctx, card, refs)
    if refutation == "R2":
        return _namesakes(ctx, card, refs)
    if refutation == "R6":
        independent = "alert" in refs or "cells" in refs or card["code"] == "sources_in_conflict"
        return _tried(ctx, card, "R6", statement, holds=not independent,
                      detail="the alert comes from a public satellite system, not from the company"
                      if "alert" in refs else "the maps are public satellite products (EU JRC, Hansen/UMD), "
                      "not the company's" if "cells" in refs else "the account comes from an NGO, not from the company")
    if refutation == "R8":
        rows = ctx.gate.ask("Evaluator", "Q6", company_id=ctx.company_id, year_from=2021, year_to=2025).rows
        tonnes = sum(r["tonnes"] for r in rows if r["plot_id"] == refs["plot"])
        return _tried(ctx, card, "R8", statement, holds=tonnes <= 0,
                      detail=f"{refs['plot']} supplied {tonnes:.2f} t of the company's 2021-2025 volume")
    raise ValueError(f"no test for {refutation}")


def _drawn(ctx: RunContext, plot_id: str) -> dict:
    """The outline the analyst drew: a declaration, so there is no registry row to re-read."""
    declared = ctx.notes.get("declared_plot")
    if not declared or declared["plot_id"] != plot_id:
        raise ValueError(f"{plot_id} was not drawn in this run")
    return declared


def _own_cells(ctx: RunContext, plot_id: str) -> list[dict]:
    """The Evaluator's own Q10 around the drawn outline, asked once per plot."""
    cache = ctx.notes.setdefault("evaluator_cells", {})
    if plot_id not in cache:
        lat_min, lat_max, lon_min, lon_max = landcover.plot_box(_drawn(ctx, plot_id))
        cache[plot_id] = ctx.gate.ask("Evaluator", "Q10", lat_min=lat_min, lat_max=lat_max,
                                      lon_min=lon_min, lon_max=lon_max).rows
    return cache[plot_id]


def _other_plot(ctx: RunContext, card: dict, refs: dict) -> dict:
    """R1: re-read the plot and the alert with own queries and redo the containment test."""
    supplier = next(r["supplier_id"] for r in ctx.gate.ask("Evaluator", "Q5", plot_id=refs["plot"]).rows)
    plot = next(p for p in ctx.gate.ask("Evaluator", "Q3", supplier_ids=[supplier]).rows
                if p["plot_id"] == refs["plot"])
    alert = ctx.gate.ask("Evaluator", "Q9", alert_id=refs["alert"]).rows[0]
    located = next(a for a in ctx.gate.ask("Evaluator", "Q4", lat_min=plot["lat"] - 0.01, lat_max=plot["lat"] + 0.01,
                                           lon_min=plot["lon"] - 0.01, lon_max=plot["lon"] + 0.01).rows
                   if a["alert_id"] == alert["alert_id"])
    inside = geometry.alert_inside(plot, located)
    return _tried(ctx, card, "R1", CATALOGUE["R1"], holds=not inside,
                  detail=f"{alert['alert_id']} re-read {'inside' if inside else 'outside'} {plot['plot_id']} "
                         f"({plot['area_ha']} ha polygon)")


def _namesakes(ctx: RunContext, card: dict, refs: dict) -> dict:
    """R2: is the supplier the NGO names really ours, or a namesake?"""
    supplier_id = refs["supplier"]
    name = next(r for r in ctx.gate.ask("Evaluator", "Q2", company_id=ctx.company_id).rows
                if r["supplier_id"] == supplier_id)["name"]
    namesakes = ctx.gate.ask("Evaluator", "Q8", name=name).rows
    return _tried(ctx, card, "R2", CATALOGUE["R2"], holds=False,
                  detail=f"{len(namesakes)} supplier(s) named {name}: "
                         f"{', '.join(r['supplier_id'] + ' ' + r['cooperative_group'] for r in namesakes)}; "
                         f"the rules already kept only the one the NGO's union points to")


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
