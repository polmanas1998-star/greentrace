"""Deep Trace: the report of a finished run cut into sentences, each with the query rows it rests on.

Read from the run as it stands (after any what_if), never re-run: the Writer's report, its cards,
the Planner's hypotheses, the data gate's answers (SQL and rows, on SQLite or Zetaris) and, for a
query that never answered, its failed attempt in the trace. Which rows back a sentence comes from
what the Writer cites, not from reading the words:

  verdict    the summary: the card it quotes (the first card with the verdict, as the Writer picks
             it), or for PROVEN every answer the plan read
  finding    the refs of that finding (its "evidence" list)
  flip       the entities of the change that flips it, or of every change tried
  untested,  the cards whose template wrote that line (app/agents/writer.py, same templates)
  action
  limitation and human_review: general statements that no query can back; they come back with
             "evidence": [] and "unsupported": true, as any sentence without a source would.

A ref finds its rows by id in the answers that hold that kind of row (REF_ROWS); the whole answer
when the ref names a source, a company or the cells around a plot. At most MAX_ROWS rows are
shown per answer; row_count is always the full count.
"""
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from app.agents import writer
from app.explain.flip import NOTHING_AGAINST, NOTHING_TO_CHANGE
from app.queries import SOURCE_OF, render
from app.rules import PROVEN
from app.tracing import LOG_DIR

MAX_ROWS = 50
ANALYST = "input.plot"  # the plot the analyst drew: a declaration, not a query
SECTIONS = ("verdict", "finding", "flip", "untested", "action", "limitation", "human_review")
UNSOURCED = ("limitation", "human_review")  # general statements: no query can back them

# ref kind -> (queries whose rows hold it, does this row belong to it)
REF_ROWS: dict[str, tuple[tuple[str, ...], Callable[[dict, str], bool]]] = {
    "alert": (("Q4", "Q9"), lambda row, key: row.get("alert_id") == key),
    "plot": (("Q3", "Q5", "Q6", "Q7"), lambda row, key: row.get("plot_id") == key),
    "supplier": (("Q2", "Q6", "Q8"), lambda row, key: row.get("supplier_id") == key),
    "delivery": (("Q5", "Q6"), lambda row, key: f"{row.get('plot_id')}:{row.get('year')}" == key),
    "doc": (("Q1",), lambda row, key: f"{row.get('doc_id')}:{row.get('section')}:{row.get('paragraph_no')}" == key),
    "cells": (("Q10",), lambda row, key: True),  # the cells read around that plot, all of them
}
COMPANY_QUERIES = ("Q2", "Q3", "Q6")  # what the traced share reads: suppliers and total, plots, deliveries

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[])")
_CALL = re.compile(r"^(?P<query>Q\d+)\((?P<params>.*)\)$")


class RunFacts:
    """What a finished run kept, read once: the report, the cards, the plan, the answers and the engine."""

    def __init__(self, run: Any) -> None:
        self.run_id: str = run.ctx.run_id
        self.output: dict = run.output
        self.cards: list[dict] = list(run.cards)
        self.reviews: dict = dict(run.reviews)
        self.hypotheses: list[dict] = list(run.hypotheses)
        self.answers = list(run.ctx.gate.answers)
        self.engine: str = run.ctx.gate.engine.name
        self.prefix: str = run.ctx.gate.engine.prefix
        self.declared: Optional[dict] = run.ctx.notes.get("declared_plot")
        self.evidence = run.evidence
        self.trace_file: Path = (run.log_dir or LOG_DIR) / f"{self.run_id}.jsonl"


# ---- sentences --------------------------------------------------------------------------------

def sentences(run: Any) -> dict[str, Any]:
    """GET /runs/{run_id}/sentences."""
    facts = RunFacts(run)
    out = []
    for n, piece in enumerate(pieces(facts), start=1):
        evidence = evidence_for(facts, piece["refs"], piece["queries"])
        out.append({"n": n, "text": piece["text"], "section": piece["section"], "cites": piece["refs"],
                    "evidence": evidence, "unsupported": not evidence})
    return {"run_id": facts.run_id, "verdict": facts.output.get("verdict"), "engine": facts.engine,
            "sentences": out, "unsupported": sum(1 for s in out if s["unsupported"])}


def pieces(facts: RunFacts) -> list[dict[str, Any]]:
    """Every sentence of the report, in reading order, with the refs (or whole queries) the Writer rests it on."""
    out = facts.output
    verdict = out["verdict"]
    everything = _plan_queries(facts)
    deciding = [c for c in facts.cards if c["verdict"] == verdict]  # the Writer quotes deciding[0]
    found: list[dict] = []

    def add(section: str, text: str, refs: Iterable[str] = (), queries: Iterable[str] = ()) -> None:
        for sentence in split(text):
            found.append({"section": section, "text": sentence, "refs": list(dict.fromkeys(refs)),
                          "queries": list(dict.fromkeys(queries))})

    if verdict == PROVEN:
        add("verdict", out["summary"], queries=everything)
    else:
        add("verdict", out["summary"], refs=(deciding[0]["refs"] if deciding else _all_refs(facts.cards)))
    for finding in out.get("findings", []):
        add("finding", finding["finding"], refs=[f"{e['source']}:{e['id']}" for e in finding["evidence"]])
    add("flip", out.get("flip", ""), **_flip_basis(out, facts.cards, everything))
    for section, key, templates in (("untested", "untested_factors", writer.UNTESTED),
                                    ("action", "recommended_actions", writer.ACTIONS)):
        for line in out.get(key, []):
            cards = [c for c in facts.cards if line in writer._filled(templates, [c])]
            add(section, line, refs=_all_refs(cards))
    for line in out.get("limitations", []):
        add("limitation", line)
    add("human_review", out.get("human_review", ""))
    return found


def split(text: str) -> list[str]:
    """Sentences of one report string: a full stop, a space, then a capital (decimals and ids stay whole)."""
    return [part.strip() for part in _SENTENCE_END.split(text or "") if part.strip()]


def _plan_queries(facts: RunFacts) -> list[str]:
    return [q for h in facts.hypotheses for q in h["queries"]]


def _all_refs(cards: list[dict]) -> list[str]:
    return [ref for card in cards for ref in card["refs"]]


def _flip_basis(out: dict, cards: list[dict], everything: list[str]) -> dict[str, list[str]]:
    tried = out.get("flip_tried") or []
    first = next((t for t in tried if t["verdict"] != out["verdict"]), None)
    if first is not None:
        return {"refs": [r for r in (first["change"]["entity"], first["change"].get("relative_to")) if r]}
    if tried:
        return {"refs": [r for t in tried for r in (t["change"]["entity"], t["change"].get("relative_to")) if r]}
    if out.get("flip") == NOTHING_AGAINST:
        return {"queries": everything}
    if out.get("flip") == NOTHING_TO_CHANGE:
        return {"refs": _all_refs([c for c in cards if c["verdict"] == out["verdict"]])}
    return {}


# ---- evidence: the rows behind refs ------------------------------------------------------------

def evidence_for(facts: RunFacts, refs: Iterable[str], queries: Iterable[str] = ()) -> list[dict[str, Any]]:
    """One entry per answer that holds a row of these refs (or per whole answer of these queries), in answer order;
    a query that never answered comes back with its failed attempt from the trace."""
    refs = list(refs)
    picked: dict[int, set[int]] = {}
    failed: list[dict[str, Any]] = []
    for ref in refs:
        for index, rows in rows_for_ref(facts, ref).items():
            picked.setdefault(index, set()).update(rows)
        failed += [e for e in _not_answered(facts, ref) if e not in failed]
    for query_id in queries:
        for index, answer in enumerate(facts.answers):
            if answer.query_id == query_id and answer.agent in ("Investigator", "Extractor"):
                picked.setdefault(index, set()).update(range(len(answer.rows)))
    drawn = []
    if facts.declared and f"plot:{facts.declared['plot_id']}" in refs:
        drawn = [{"query_id": ANALYST, "engine": "analyst", "agent": "analyst", "answer": None, "sql": None,
                  "rows": [facts.declared], "row_count": 1, "truncated": False}]
    return drawn + [_entry(facts, index, sorted(rows)) for index, rows in sorted(picked.items())] + failed


def rows_for_ref(facts: RunFacts, ref: str) -> dict[int, list[int]]:
    """answer index -> positions of the rows that belong to this ref."""
    kind, _, key = ref.partition(":")
    found: dict[int, list[int]] = {}
    if kind == "company":
        fiscal = next((a.rows[0]["fiscal_year"] for a in facts.answers if a.query_id == "Q2" and a.rows), None)
        for index, answer in enumerate(facts.answers):
            if answer.agent == "Investigator" and answer.query_id in COMPANY_QUERIES:
                found[index] = [n for n, row in enumerate(answer.rows)
                                if answer.query_id != "Q6" or row.get("year") == fiscal]
        return {i: rows for i, rows in found.items() if rows}
    if kind == "source":
        for index, answer in enumerate(facts.answers):
            if answer.agent == "Investigator" and SOURCE_OF.get(answer.query_id) == key:
                found[index] = list(range(len(answer.rows)))  # an empty answer is evidence too
        return found
    if kind not in REF_ROWS:
        return found
    queries, belongs = REF_ROWS[kind]
    for index, answer in enumerate(facts.answers):
        if answer.query_id in queries:
            rows = [n for n, row in enumerate(answer.rows) if belongs(row, key)]
            if rows:
                found[index] = rows
    return found


def _entry(facts: RunFacts, index: int, rows: list[int]) -> dict[str, Any]:
    answer = facts.answers[index]
    shown = [answer.rows[n] for n in rows[:MAX_ROWS]]
    return {"query_id": answer.query_id, "engine": facts.engine, "agent": answer.agent, "answer": index,
            "sql": answer.sql, "rows": shown, "row_count": len(rows), "truncated": len(rows) > MAX_ROWS}


def _not_answered(facts: RunFacts, ref: str) -> list[dict[str, Any]]:
    """source:X with no answer from X: the Investigator's failed attempts, read back from the trace."""
    kind, _, key = ref.partition(":")
    if kind != "source" or any(a.agent == "Investigator" and SOURCE_OF.get(a.query_id) == key for a in facts.answers):
        return []
    failed = [line for line in _this_run(facts) if line.get("action") == "error"
              and line.get("agent_name") == "Investigator" and _CALL.match(line.get("input_summary") or "")
              and SOURCE_OF.get(_CALL.match(line["input_summary"])["query"]) == key]
    if not failed:
        planned = [q for q in _plan_queries(facts) if SOURCE_OF.get(q) == key]
        return [{"query_id": q, "engine": facts.engine, "agent": "Investigator", "answer": None, "sql": None,
                 "rows": [], "row_count": 0, "truncated": False, "status": "no_answer",
                 "reason": f"planned, but no answer from source '{key}' was kept"} for q in planned[:1]]
    return [{"query_id": _CALL.match(line["input_summary"])["query"], "engine": facts.engine,
             "agent": "Investigator", "answer": None, "sql": _sql(facts, line["input_summary"]), "rows": [],
             "row_count": 0, "truncated": False, "status": "failed", "reason": line.get("output_summary"),
             "attempts": (line.get("retry_count") or 0) + 1}
            for line in failed]


def _this_run(facts: RunFacts) -> list[dict[str, Any]]:
    """The trace lines of this run: after the last receive_case of its file (a run id can be reused)."""
    try:
        lines = [json.loads(text) for text in facts.trace_file.read_text(encoding="utf-8").splitlines() if text]
    except (OSError, ValueError):
        return []
    starts = [n for n, line in enumerate(lines) if line.get("action") == "receive_case"]
    return lines[starts[-1]:] if starts else lines


def _sql(facts: RunFacts, call: str) -> Optional[str]:
    """The SQL of a failed call, rendered again from the parameters the trace kept (numbers only)."""
    match = _CALL.match(call)
    try:
        params = {k.strip(): float(v) for k, v in (p.split("=", 1) for p in match["params"].split(", "))}
        return render(match["query"], facts.prefix, **params)
    except (ValueError, KeyError, TypeError):
        return None
