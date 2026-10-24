"""Evidence Dependency Graph: from each sentence of the report down to the rows and sources it rests on.

Nodes: sentence (the report, cut as app/explain/deep_trace.py cuts it), fact (a finding card, or a
record it rests on: an alert, a plot, a supplier, a paragraph...), query (one answer of the data gate,
by its index in the receipt), source (registry, alerts, documents, reference_maps), rule (the verdict
rule a finding falls under, or a refutation the Evaluator tried on it) and the verdict.

Every edge comes from a record of the run, never from reading the words, and says which ("via"):
  world_model  a finding card's based_on edges in the Meterless World Model (its depends_on)
  writer       the Writer's citations: a finding's evidence list, the cards a sentence quotes
  rows         the answers whose rows hold that record (by its id)
  queries      the source a query reads (app/queries.SOURCE_OF)
  rules        a finding's rule (app/rules.py), and the rules deciding a part with the overall verdict
  evaluator    a refutation the Evaluator tested with its own query: refutes if it held, supports if not
Relations: supports, refutes, derived_from (from the derived record to its origin), cites.
"""
from typing import Any, Optional

from app.agents.evaluator import CATALOGUE
from app.explain import deep_trace
from app.explain.deep_trace import RunFacts
from app.memory.world_model import SYSTEM
from app.queries import SOURCE_OF
from app.rules import PROVEN

RULES = {
    1: "Rule 1: a source did not answer, or a datum is invalid -> UNVERIFIABLE",
    2: "Rule 2: direct evidence that held -> CONTRADICTED",
    3: "Rule 3: an inconsistency, a lone source or a loss too small to decide -> UNVERIFIABLE",
    4: "Rule 4: none of the above -> PROVEN",
}
ENTITY_TYPES = ("alert", "plot", "supplier")  # what the Investigator writes in the World Model as entities


class _Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []

    def node(self, node_id: str, kind: str, label: str, source: Optional[str] = None,
             query_id: Optional[str] = None, **extra: Any) -> str:
        if node_id not in self.nodes:
            self.nodes[node_id] = {"id": node_id, "kind": kind, "label": label, "source": source,
                                   "query_id": query_id, **extra}
        return node_id

    def edge(self, frm: str, to: str, relation: str, via: str) -> None:
        edge = {"from": frm, "to": to, "relation": relation, "via": via}
        if edge not in self.edges:
            self.edges.append(edge)


def graph(run: Any) -> dict[str, Any]:
    """GET /runs/{run_id}/graph."""
    facts = RunFacts(run)
    g = _Graph()
    verdict = facts.output["verdict"]
    g.node("verdict", "verdict", verdict)

    for part in facts.output.get("parts", []):
        if part["verdict"] == verdict:
            g.edge(g.node(f"rule:{part['rule']}", "rule", RULES.get(part["rule"], f"Rule {part['rule']}")),
                   "verdict", "supports", "rules")

    statuses = _world_model_facts(run)
    for card in facts.cards:
        finding = g.node(f"fact:finding:{card['id']}", "fact", card["detail"], world_model_id=card["fact"],
                         status=statuses.get(card["fact"], {}).get("status"), claim_part=card["part"],
                         verdict=card["verdict"])
        g.edge(finding, g.node(f"rule:{card['rule']}", "rule", RULES.get(card["rule"], f"Rule {card['rule']}")),
               "supports", "rules")
        depends = statuses.get(card["fact"], {}).get("depends_on", [])
        from_model = [_entity_ref(d) for d in depends if _entity_ref(d)]
        for ref in from_model:
            g.edge(finding, _record(g, facts, ref), "derived_from", "world_model")
        for ref in card["refs"]:
            if ref not in from_model:
                g.edge(finding, _record(g, facts, ref), "derived_from", "writer")
        for tried in (facts.reviews.get(card["id"]) or {}).get("tried", []):
            refutation = g.node(f"rule:{tried['id']}@{card['id']}", "rule",
                                f"{tried['id']} {'held' if tried['holds'] else 'rejected'}: {tried['detail']}",
                                statement=CATALOGUE.get(tried["id"], tried.get("statement")))
            g.edge(refutation, finding, "refutes" if tried["holds"] else "supports", "evaluator")

    for n, piece in enumerate(deep_trace.pieces(facts), start=1):
        sentence = g.node(f"sentence:{n}", "sentence", piece["text"], section=piece["section"],
                          unsupported=not deep_trace.evidence_for(facts, piece["refs"], piece["queries"]))
        if piece["section"] == "verdict":
            g.edge(sentence, "verdict", "cites", "writer")
        for card_id in piece["cards"]:
            g.edge(sentence, f"fact:finding:{card_id}", "cites", "writer")
        for ref in piece["refs"]:
            g.edge(sentence, _record(g, facts, ref), "cites", "writer")
        for index in _whole_answers(facts, piece["queries"]):
            g.edge(sentence, _query(g, facts, index), "cites", "writer")
            if verdict == PROVEN:
                g.edge(f"query:{index}", "rule:4", "supports", "rules")

    nodes = list(g.nodes.values())
    return {"run_id": facts.run_id, "verdict": verdict, "nodes": nodes, "edges": g.edges, "verdict_node": "verdict",
            "counts": {kind: sum(1 for n in nodes if n["kind"] == kind)
                       for kind in ("sentence", "fact", "query", "source", "rule", "verdict")}}


# ---- records, queries, sources ---------------------------------------------------------------------

def _record(g: _Graph, facts: RunFacts, ref: str) -> str:
    """The fact node of one record (fact:alert:A-221), linked to every answer that holds its rows."""
    node_id = f"fact:{ref}"
    if node_id in g.nodes:
        return node_id
    found = deep_trace.rows_for_ref(facts, ref)
    first = min(found, key=lambda i: (facts.answers[i].agent != "Investigator", i)) if found else None
    row = facts.answers[first].rows[found[first][0]] if first is not None and found[first] else None
    query_id = facts.answers[first].query_id if first is not None else None
    declared = facts.declared if facts.declared and ref == f"plot:{facts.declared['plot_id']}" else None
    g.node(node_id, "fact", _label(ref, declared or row), "analyst" if declared else SOURCE_OF.get(query_id),
           None if declared else query_id)
    if declared:
        g.edge(node_id, g.node("source:analyst", "source", "plot drawn by the analyst (input.plot)"),
               "derived_from", "rows")
    for index in sorted(found):
        g.edge(node_id, _query(g, facts, index), "derived_from", "rows")
    for attempt in deep_trace.failed_attempts(facts, ref):
        query = g.node(f"query:failed:{attempt['query_id']}", "query",
                       f"{attempt['query_id']} by {attempt['agent']}: {attempt['status']} ({attempt['reason']})",
                       SOURCE_OF.get(attempt["query_id"]), attempt["query_id"], status=attempt["status"])
        g.edge(node_id, query, "derived_from", "rows")
        g.edge(query, _source(g, facts, SOURCE_OF.get(attempt["query_id"], "unknown")), "derived_from", "queries")
    return node_id


def _query(g: _Graph, facts: RunFacts, index: int) -> str:
    answer = facts.answers[index]
    source = SOURCE_OF.get(answer.query_id, "unknown")
    node_id = g.node(f"query:{index}", "query", f"{answer.query_id} by {answer.agent}: {len(answer.rows)} row(s)",
                     source, answer.query_id, agent=answer.agent, answer=index, engine=facts.engine)
    g.edge(node_id, _source(g, facts, source), "derived_from", "queries")
    return node_id


def _source(g: _Graph, facts: RunFacts, name: str) -> str:
    return g.node(f"source:{name}", "source", f"{name} ({facts.engine})", name)


def _whole_answers(facts: RunFacts, queries: list[str]) -> list[int]:
    return [i for i, a in enumerate(facts.answers) if a.query_id in queries and a.agent in ("Investigator", "Extractor")]


def _entity_ref(world_model_id: str) -> Optional[str]:
    """alert:eudr:A-221 -> alert:A-221; a claim placeholder or a fact id -> None."""
    kind, _, rest = world_model_id.partition(":")
    system, _, key = rest.partition(":")
    return f"{kind}:{key}" if kind in ENTITY_TYPES and system == SYSTEM and key else None


def _world_model_facts(run: Any) -> dict[str, dict]:
    """World Model fact id -> the fact as the World Model gives it (status, depends_on), for the run's cards."""
    memory = run.ctx.memory
    abouts = dict.fromkeys(e["payload"]["about"] for e in memory.view("timeline") if e["kind"] == "assertFact")
    return {fact["id"]: fact for about in abouts for fact in memory.view("facts", about)}


def _label(ref: str, row: Optional[dict]) -> str:
    kind, _, key = ref.partition(":")
    if row is None:
        return f"{kind} {key}" + (": no answer" if kind == "source" else "")
    if kind == "alert":
        return f"Alert {key}, {row.get('alert_date')}, {row.get('confidence')} confidence"
    if kind == "plot":
        shape = "polygon" if row.get("polygon_wkt") else "point"
        return f"Plot {key}, {row.get('area_ha')} ha, {shape}, supplier {row.get('supplier_id')}"
    if kind == "supplier":
        return f"Supplier {key} {row.get('name', '')}".strip()
    if kind == "company":
        return f"Company {key}: {row.get('total_tonnes')} t declared in {row.get('fiscal_year')}"
    if kind == "delivery":
        return f"Delivery of {row.get('plot_id')} in {row.get('year')}: {row.get('tonnes')} t"
    if kind == "doc":
        return f"{row.get('doc_id')} {row.get('section')} paragraph {row.get('paragraph_no')}: " \
               f"{str(row.get('paragraph_text', ''))[:80]}"
    if kind == "cells":
        return f"Reference cells read around {key}"
    return f"{kind} {key}"
