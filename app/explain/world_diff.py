"""Memory diff: the World Model just before the last what_if, against the World Model now.

Everything here is read from the run's own Meterless World Model, never from comparing report texts:

  invalidated  the invalidated_by edges the last what_if wrote (WorldModel.invalidate: every fact that
               depended on the changed entity, directly or through another fact)
  superseded   facts the engine marked supersededBy a fact asserted after the what_if (its
               source-priority policy: equal priority, the newer fact wins)
  changed      the value the what_if changed (its value before, replayed from the Investigator's rows
               and the earlier what_ifs, as verify.py does), then every entity attribute the World Model
               holds that differs between its snapshot at the tick before the what_if (entity_at) and now
  verdict      before and after, as the what_if reported them

The tick before each what_if is noted by app/orchestrator.py (ctx.notes["what_if_ticks"]); World Model
record times are logical ticks, so "after the what_if" is exact. Facts are named about.predicate, with
@v2, @v3... for later versions of the same fact.
"""
from typing import Any, Optional

from app import orchestrator
from app.agents import investigator
from app.memory.fallback import POLICY, SOURCE_PRIORITY
from app.memory.world_model import INVALIDATED_BY, SYSTEM, entity_id, tick_at
from app.receipt import evidence_answers


def world_diff(run: Any) -> dict[str, Any]:
    """GET /runs/{run_id}/world_diff."""
    ctx, out = run.ctx, run.output
    ticks = ctx.notes.get("what_if_ticks") or []
    if not ticks or not run.changes or "what_if" not in out:
        verdict = out.get("verdict")
        return {"run_id": ctx.run_id, "status": "no_what_if",
                "reason": f"no what_if on this run yet: POST /runs/{ctx.run_id}/what_if first",
                "changed": [], "superseded": [], "invalidated": [], "verdict": {"before": verdict, "after": verdict}}
    memory, change = ctx.memory, run.changes[-1]
    start = tick_at(ticks[-1])
    events = memory.view("timeline")
    after = [e for e in events if e["at"] > start]
    cause = f"{change['entity']}.{change['field']}"
    changed_id = entity_id(*change["entity"].split(":", 1))

    facts = _facts(memory, events)
    names = _names(facts)
    invalidated = [{"fact": names.get(e["payload"]["from"], e["payload"]["from"]),
                    "because_of": cause if e["payload"]["to"] == changed_id else short(e["payload"]["to"]),
                    "world_model_id": e["payload"]["from"]}
                   for e in after if e["kind"] == "relate" and e["payload"]["type"] == INVALIDATED_BY]
    by_id = {f["id"]: f for f in facts}
    superseded = []
    for fact in facts:
        winner = by_id.get(fact.get("supersededBy") or "")
        if winner is not None and winner["assertedAt"] > start:
            superseded.append({"fact": names[fact["id"]], "by": names[winner["id"]],
                               "reason": _why(fact, winner), "world_model_id": fact["id"]})

    changed = [{"fact": cause, "before": _value_before(run), "after": change["value"], "via": "what_if"}]
    for entity in dict.fromkeys(e["payload"]["id"] for e in after if e["kind"] == "upsertEntity"):
        # Only the attributes upsert_entity wrote: the engine also mirrors each fact's value as an attribute,
        # which its replay (entity_at) does not rebuild; facts are compared above, as facts.
        written = {a for e in events if e["kind"] == "upsertEntity" and e["payload"]["id"] == entity
                   for a in e["payload"]["attrs"]}
        then = (memory.entity_at(entity, ticks[-1]) or {}).get("attrs") or {}
        now = (memory.view("graph", entity) or {}).get("attrs") or {}
        for attr in sorted(written):
            entry = {"fact": f"{short(entity)}.{attr}", "before": then.get(attr), "after": now.get(attr),
                     "via": "world_model"}
            if entry["before"] != entry["after"] and (entry["fact"], entry["before"], entry["after"]) != (
                    cause, changed[0]["before"], changed[0]["after"]):
                changed.append(entry)

    what_if = out["what_if"]
    return {
        "run_id": ctx.run_id, "status": "success", "change": dict(change),
        "changed": changed, "superseded": superseded, "invalidated": invalidated,
        "verdict": {"before": what_if["verdict_before"], "after": what_if["verdict_after"]},
        "rechecked_cards": what_if.get("rechecked_cards", []),
        "world_model": {"engine": "meterless" if not memory.degraded else "in-memory fallback",
                        "tick_before": ticks[-1], "tick_after": memory.tick, "events_after": len(after),
                        "what_ifs": len(run.changes)},
    }


def short(node_id: str) -> str:
    """alert:eudr:A-221 -> alert:A-221; a fact id stays as it is."""
    kind, _, rest = node_id.partition(":")
    system, _, key = rest.partition(":")
    return f"{kind}:{key}" if system == SYSTEM and key else node_id


def _facts(memory: Any, events: list[dict]) -> list[dict]:
    """Every fact of the run, with the status and depends_on the World Model gives it (view "facts")."""
    abouts = dict.fromkeys(e["payload"]["about"] for e in events if e["kind"] == "assertFact")
    return [fact for about in abouts for fact in memory.view("facts", about)]


def _names(facts: list[dict]) -> dict[str, str]:
    """fact id -> about.predicate, @vN from the second version of the same about and predicate on."""
    names, seen = {}, {}
    for fact in sorted(facts, key=lambda f: (f["assertedAt"], f["id"])):
        base = f"{short(fact['about'])}.{fact['predicate']}"
        seen[base] = seen.get(base, 0) + 1
        names[fact["id"]] = base if seen[base] == 1 else f"{base}@v{seen[base]}"
    return names


def _rank(fact: dict) -> int:
    kind = fact["source"]["kind"]
    return SOURCE_PRIORITY.index(kind) if kind in SOURCE_PRIORITY else len(SOURCE_PRIORITY)


def _why(loser: dict, winner: dict) -> str:
    by = winner["source"].get("by") or winner["source"]["kind"]
    rule = (f"{POLICY}: same priority, the newer fact wins" if _rank(winner) == _rank(loser)
            else f"{POLICY}: {winner['source']['kind']} outranks {loser['source']['kind']}")
    value = ("same value" if loser["value"] == winner["value"]
             else f"value was {loser['value']!r}, now {winner['value']!r}")
    return f"re-asserted by the {by} after the what_if ({rule}); {value}"


def _value_before(run: Any) -> Optional[Any]:
    """The changed field before the last what_if: the Investigator's rows, with the earlier what_ifs replayed."""
    ctx = run.ctx
    sources = [{"agent": a.agent, "query_id": a.query_id, "rows": a.rows} for a in ctx.gate.answers]
    evidence = investigator.evidence_from_answers(evidence_answers(sources), run.evidence.extracted,
                                                  ctx.company_id, ctx.notes.get("declared_plot"))
    for earlier in run.changes[:-1]:
        kind, key = earlier["entity"].split(":", 1)
        evidence = orchestrator.apply_change(evidence, kind, key, earlier["field"], earlier["value"])
    kind, key = run.changes[-1]["entity"].split(":", 1)
    rows, id_field = (evidence.alerts or [], "alert_id") if kind == "alert" else (evidence.plots, "plot_id")
    row = next((r for r in rows if r[id_field] == key), None)
    return None if row is None else row.get(run.changes[-1]["field"])
