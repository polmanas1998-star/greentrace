"""The pieces of one finished investigation, as the court may cite them. Nothing is read again.

A piece is a row the run already read through the data gate (the registry, the deliveries, the
alerts), a paragraph of the company's report or of the NGO note (Q1), or the plot the analyst
drew. struck() is the code that admits or strikes a citation: a row id must name a row of this
run, a field it quotes must have exactly that value, and a quote from a document must be found
word for word in the paragraph it names.

The queries behind a card, or behind one of the Evaluator's refutations, are looked up among the
answers the run already holds: same SQL, same rows as the receipt.
"""
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.queries import Answer, render
from app.tracing import LOG_DIR
from app.world.texts import NGO_DOC, RSE_DOC

DOCUMENTS = {"csr_report": RSE_DOC, "ngo_report": NGO_DOC}
ROW_SOURCES = ("registry", "deliveries", "alerts", "declared_plot")
SOURCES = tuple(DOCUMENTS) + ROW_SOURCES
CUTOFF_YEAR = 2020
# The Investigator's query behind each kind of ref (the receipt's derived_from reads the same ones).
REF_QUERY = {"alert": "Q4", "plot": "Q3", "supplier": "Q2", "company": "Q2", "delivery": "Q6", "doc": "Q1",
             "cells": "Q10", "source": "Q4"}

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Pieces:
    rows: dict[tuple[str, str], list[dict]]  # (source, "plot:P-014") -> the rows read with that id
    paragraphs: dict[tuple[str, str], str]   # (source, "§2" or "annex §1") -> paragraph text


def paragraph_ref(row: dict) -> str:
    return f"§{row['paragraph_no']}" if row["section"] == "main" else f"{row['section']} §{row['paragraph_no']}"


def pieces_of(ctx: Any) -> Pieces:
    """Every piece the run read: its answers, plus the plot the analyst drew and its deliveries."""
    rows: dict[tuple[str, str], list[dict]] = defaultdict(list)
    paragraphs: dict[tuple[str, str], str] = {}

    def add(source: str, ref: str, row: dict) -> None:
        if row not in rows[(source, ref)]:
            rows[(source, ref)].append(row)

    for answer in ctx.gate.answers:
        for row in answer.rows:
            if answer.query_id == "Q1":
                source = next((s for s, doc in DOCUMENTS.items() if doc == row["doc_id"]), None)
                if source:
                    paragraphs[(source, paragraph_ref(row))] = row["paragraph_text"]
            elif answer.query_id in ("Q2", "Q8"):
                add("registry", f"supplier:{row['supplier_id']}", row)
                if answer.query_id == "Q2":
                    add("registry", f"company:{ctx.company_id}", {"company_id": ctx.company_id,
                                                                   "total_tonnes": row["total_tonnes"],
                                                                   "fiscal_year": row["fiscal_year"]})
            elif answer.query_id == "Q3":
                add("registry", f"plot:{row['plot_id']}", row)
            elif answer.query_id in ("Q5", "Q6"):
                add("deliveries", f"delivery:{row['plot_id']}:{row['year']}", row)
            elif answer.query_id in ("Q4", "Q9"):
                add("alerts", f"alert:{row['alert_id']}", row)
    declared = ctx.notes.get("declared_plot")
    if declared:
        add("declared_plot", f"plot:{declared['plot_id']}", declared)
        for delivery in ctx.notes.get("declared_deliveries", []):
            add("declared_plot", f"delivery:{delivery['plot_id']}:{delivery['year']}", delivery)
    return Pieces(dict(rows), paragraphs)


# ---- citations ------------------------------------------------------------------------------

def text_of(value: Any) -> str:
    return str(value)


def _spaces(text: Any) -> str:
    return " ".join(str(text or "").split())


def _same(value: Any, quoted: str) -> bool:
    if text_of(value) == quoted:
        return True
    try:
        return not isinstance(value, bool) and float(value) == float(quoted)
    except (TypeError, ValueError):
        return False


def struck(pieces: Pieces, cite: Any) -> Optional[str]:
    """Why a citation is struck by the code, or None when it names a real piece of this run."""
    if not isinstance(cite, dict):
        return "a citation is an object {source, ref, quote}"
    source, ref, quote = cite.get("source"), str(cite.get("ref") or ""), _spaces(cite.get("quote"))
    if source in DOCUMENTS:
        text = pieces.paragraphs.get((source, ref))
        if text is None:
            return f"{source} has no paragraph {ref or '(none)'} in this run"
        if len(quote.split()) < 2:
            return f"a quote from {source} {ref} needs at least two words"
        if quote not in _spaces(text):
            return f"the quote is not word for word in {source} {ref}"
        return None
    if source not in ROW_SOURCES:
        return f"unknown source {source!r}"
    rows = pieces.rows.get((source, ref))
    if not rows:
        return f"no {source} row {ref or '(none)'} was read in this run"
    if quote:
        field, colon, value = quote.partition(":")
        if not colon or not any(field.strip() in row and _same(row[field.strip()], value.strip()) for row in rows):
            return f"{source} {ref} has no value '{quote}'"
    return None


def sentence(pieces: Pieces, source: str, *patterns: str, skip: tuple = ()) -> Optional[dict]:
    """The first sentence of a document that matches a pattern, as a citation: patterns in order, main pages
    first, leaving out the passages in skip ([source, ref, quote], as plaintiff.piece() gives them)."""
    found = sorted((ref.startswith("annex"), int(ref.rsplit("§", 1)[1]), ref, text)
                   for (src, ref), text in pieces.paragraphs.items() if src == source)
    for pattern in patterns:
        regex = re.compile(pattern, re.I)
        for _, _, ref, text in found:
            for part in _SENTENCE.split(text):
                if regex.search(part) and [source, ref, _spaces(part)] not in skip:
                    return {"source": source, "ref": ref, "quote": part}
    return None


def row_cite(pieces: Pieces, source: str, ref: str, field: Optional[str] = None) -> Optional[dict]:
    """A row of this run as a citation, quoting one of its fields when asked."""
    rows = pieces.rows.get((source, ref))
    if not rows:
        return None
    quote = f"{field}: {text_of(rows[0][field])}" if field and field in rows[0] else None
    return {"source": source, "ref": ref, "quote": quote}


# ---- the queries behind a card, and behind a refutation ----------------------------------------

def find(ctx: Any, query_id: str, agent: Optional[str] = None, *, sql: Optional[str] = None,
         has: Optional[Callable[[dict], bool]] = None) -> Optional[Answer]:
    """The latest answer of this run to query_id (asked by agent, with this SQL, holding such a row)."""
    for answer in reversed(ctx.gate.answers):
        if answer.query_id != query_id or (agent and answer.agent != agent) or (sql and answer.sql != sql):
            continue
        if has is None or any(has(row) for row in answer.rows):
            return answer
    return None


def lost_after_cutoff(row: dict) -> bool:
    return (row.get("loss_year") or 0) > CUTOFF_YEAR


def _matcher(kind: str, key: str) -> Callable[[dict], bool]:
    if kind == "alert":
        return lambda row: row.get("alert_id") == key
    if kind == "plot":
        return lambda row: row.get("plot_id") == key
    if kind == "supplier":
        return lambda row: row.get("supplier_id") == key
    if kind == "delivery":
        plot, _, year = key.rpartition(":")
        return lambda row: row.get("plot_id") == plot and text_of(row.get("year")) == year
    if kind == "doc":
        doc, *where = key.split(":")
        return lambda row: row.get("doc_id") == doc and (len(where) < 2 or (
            row.get("section") == where[0] and text_of(row.get("paragraph_no")) == where[1]))
    if kind == "cells":
        return lost_after_cutoff
    return lambda row: True


def card_evidence(ctx: Any, card: dict) -> tuple[Optional[str], Optional[Answer], list[dict]]:
    """The query a card rests on and its rows about the card: the first ref that has one.

    The plot the analyst drew is a declaration, not a row: its cells (Q10) stand for it. A source
    ref (source:alerts) is the answer itself: no row at all, or no answer when the source was down."""
    declared = ctx.notes.get("declared_plot")
    for ref in card["refs"]:
        kind, _, key = ref.partition(":")
        query_id = REF_QUERY.get(kind)
        if query_id is None or (kind == "plot" and declared and key == declared["plot_id"]):
            continue
        match = _matcher(kind, key)
        if kind in ("source", "cells"):
            answer = find(ctx, query_id, "Investigator")
            return query_id, answer, [row for row in answer.rows if match(row)] if answer and kind == "cells" else []
        answer = find(ctx, query_id, "Extractor" if kind == "doc" else "Investigator", has=match)
        if answer is not None:
            rows = [row for row in answer.rows if match(row)]
            return query_id, answer, rows[:1] if kind == "company" else rows
    return None, None, []


def refutation_evidence(ctx: Any, card: dict, refutation: str) -> tuple[Optional[str], Optional[Answer], list[dict]]:
    """The query that tested one refutation of a card, as app/agents/evaluator.py asked it, and its rows.

    R3 rests on the plot's deliveries by year (Q5, read by the Investigator at the Evaluator's
    request) or, for a drawn plot, on the Evaluator's own Q10. R6 asks no query: the Evaluator
    judges the kind of source the card rests on."""
    refs = dict(ref.split(":", 1) for ref in card["refs"] if ":" in ref)
    alert = _matcher("alert", refs.get("alert", ""))
    if refutation in ("R4", "R5"):
        return _kept("Q9", find(ctx, "Q9", "Evaluator", has=alert), None)
    if refutation == "R1":
        return _kept("Q4", find(ctx, "Q4", "Evaluator", has=alert), alert)
    if refutation in ("R3", "R9") and "cells" in refs:
        return _kept("Q10", find(ctx, "Q10", "Evaluator"), lost_after_cutoff)
    if refutation == "R3":
        sql = render("Q5", ctx.gate.engine.prefix, plot_id=refs["plot"])
        return _kept("Q5", find(ctx, "Q5", "Investigator", sql=sql), None)
    if refutation == "R8":
        return _kept("Q6", find(ctx, "Q6", "Evaluator"), _matcher("plot", refs.get("plot", "")))
    if refutation == "R2":
        return _kept("Q8", find(ctx, "Q8", "Evaluator", has=_matcher("supplier", refs.get("supplier", ""))), None)
    return None, None, []


def _kept(query_id: str, answer: Optional[Answer], keep: Optional[Callable[[dict], bool]]):
    if answer is None:
        return query_id, None, []
    return query_id, answer, [row for row in answer.rows if keep is None or keep(row)]


# ---- the trace -------------------------------------------------------------------------------

def trace_of(run: Any) -> Optional[list[dict]]:
    """The run's trace lines from logs/<run_id>.jsonl, from its last receive_case on; None if the file is gone."""
    path = (run.log_dir or LOG_DIR) / f"{run.ctx.run_id}.jsonl"
    if not path.exists():
        return None
    lines = []
    for text in path.read_text(encoding="utf-8").splitlines():
        try:
            lines.append(json.loads(text))
        except ValueError:
            continue  # a line cut by a crash is not a step
    starts = [i for i, line in enumerate(lines)
              if line.get("agent_name") == "Pipeline" and line.get("action") == "receive_case"]
    return lines[starts[-1]:] if starts else lines
