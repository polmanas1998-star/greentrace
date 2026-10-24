"""One investigation, start to finish (kit, sections 1 and 5), and "try to fool it" after.

Extractor -> Planner -> Investigator -> Evaluator, and back: an untested refutation
sends the plan to the Planner and the Investigator reads more, at most max_rounds
times. Then the rules decide, the flip is computed, and the Writer words it. Every
arrow is a trace line.

what_if() changes one value of a finished run: the World Model invalidates exactly the
facts built on it, the cards are rebuilt from the changed evidence, and only the cards
that changed go back to the Evaluator.

Data: "sample" runs on the synthetic world in SQLite (SAMPLE_MODE=true, no network);
"live" runs on Zetaris through the semantic layer. Same agents, same rules. Both read the
real reference maps (Q10) inside a plot the analyst draws (input.plot).

Memory across investigations (H-MEM, app/memory/hmem.py, on when HMEM_URL is set): the
Investigator reads it first, the Writer writes one note per supplier concerned at the end.
It never reaches the evidence, the rules, the receipt or the seal.
"""
import os
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Optional

from app.adapters.llm import ModelGate
from app.agents import evaluator, extractor, investigator, planner, writer
from app.agents.context import RunContext
from app.canonical import sha256_of
from app.checks import geofence
from app.explain import exports
from app.explain.flip import compute_flip
from app.memory import hmem as hmem_notes
from app.memory.world_model import Source, entity_id
from app.memory.world_model import WorldModel
from app.queries import DataGate, SQLiteEngine, ZetarisEngine
from app.receipt import decision_for, verdict_seal
from app.rules import RULES_VERSION, Evidence
from app.world import drawn
from app.world.cases import build_case
from app.world.store import attach_reference, to_sqlite

RULES_FILE = Path(__file__).resolve().parent / "rules.py"
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DEFAULT_SAMPLE_CASE = 2  # the demo world, the same one scripts/load_zetaris.py loads
KEPT_RUNS = 50


@dataclass
class Investigation:
    """Everything a finished run needs to be asked "what if?" later."""
    ctx: RunContext
    request: dict
    sample: bool
    hypotheses: list[dict]
    evidence: Evidence
    cards: list[dict] = field(default_factory=list)
    reviews: dict[str, dict] = field(default_factory=dict)
    deliveries: dict[str, dict] = field(default_factory=dict)
    output: dict = field(default_factory=dict)
    changes: list[dict] = field(default_factory=list)  # what_if changes, replayed by verify.py
    log_dir: Optional[Path] = None
    input_plot: Optional[dict] = None  # the GeoJSON the analyst drew, kept for the receipt


RUNS: "OrderedDict[str, Investigation]" = OrderedDict()


def sample_mode(options: dict) -> bool:
    if "sample_mode" in options:
        return bool(options["sample_mode"])
    return os.environ.get("SAMPLE_MODE", "true").lower() in ("1", "true", "yes")


def investigate(request: dict, run_id: str, *, log, memory_engine=None, zetaris=None,
                log_dir: Optional[Path] = None, stream=None, hmem=None) -> dict[str, Any]:
    started = time.perf_counter()
    claim, company_id, options, input_plot = _read_request(request)
    declared = drawn.plot_from_geojson(input_plot) if input_plot is not None else None
    sample = sample_mode(options)

    if sample:
        world = build_case(int(options.get("case", DEFAULT_SAMPLE_CASE)))
        connection = to_sqlite(world)
        attach_reference(connection)
        engine = SQLiteEngine(connection, tuple(world["meta"]["unavailable_sources"]))
    else:
        from app.adapters.zetaris import Zetaris
        engine = ZetarisEngine(zetaris or Zetaris.from_env())

    # Sample mode makes no model call, unless use_model asks for the model on the sample world (the bench).
    use_model = bool(options.get("use_model", not sample))
    models = ModelGate(run_id, env={**os.environ, "SAMPLE_MODE": "false" if use_model else "true"}, log=log)

    ctx = RunContext(
        run_id=run_id, gate=DataGate(engine, run_id, log=log), company_id=company_id, log=log,
        memory=WorldModel(run_id, engine=memory_engine, log_dir=log_dir, stream=stream),
        model=models if models.providers else None,
        max_rounds=int(options.get("max_rounds", 3)),
    )
    ctx.notes["hmem"] = hmem if hmem is not None else hmem_notes.from_env()
    ctx.step("Pipeline", "receive_case", f"claim on {company_id}",
             f"data {'sample' if sample else 'live'}; model chain {' > '.join(models.chain)}",
             target="Extractor", status="success")
    if declared:
        ctx.notes["declared_plot"] = declared
        ctx.step("Pipeline", "receive_plot", f"input.plot ({'polygon' if declared['polygon_wkt'] else 'point'})",
                 f"{declared['plot_id']}, {declared['area_ha']} ha at {declared['lat']}, {declared['lon']}, declared "
                 f"by the analyst for {declared['supplier_id']}", target="Planner", status="success")

    parsed, extracted = extractor.extract(ctx, claim)
    if parsed["out_of_scope"]:
        return _output(run_id, sample, request, ctx, started, out_of_scope=parsed["out_of_scope"])
    if not parsed["types"]:
        return _output(run_id, sample, request, ctx, started, too_vague=True)

    hypotheses = planner.plan(ctx, parsed["types"])
    run = Investigation(ctx, request, sample, hypotheses, investigator.gather(ctx, hypotheses, extracted),
                        log_dir=log_dir, input_plot=input_plot)
    run.cards = investigator.write_cards(ctx, run.evidence)
    _review(run, run.cards)
    run.output = _conclude(run, started)
    _write_notes(run)
    _remember(run)
    return run.output


def what_if(run_id: str, entity: str, field_name: str, value: Any) -> dict[str, Any]:
    """Change one value of a finished run and re-run only what depends on it (kit, section 2.6)."""
    run = RUNS.get(run_id)
    if run is None:
        raise KeyError(f"no finished run {run_id!r} in this process")
    started, ctx = time.perf_counter(), run.ctx
    kind, _, key = entity.partition(":")
    before = run.output.get("verdict")
    run.evidence = apply_change(run.evidence, kind, key, field_name, value)
    run.changes.append({"entity": entity, "field": field_name, "value": value})
    ctx.step("Pipeline", "what_if", f"{entity}.{field_name} = {value}", f"verdict before: {before}",
             target="Investigator", status="success")

    changed_id = entity_id(kind, key)
    ctx.notes.setdefault("what_if_ticks", []).append(ctx.memory.tick)  # where app/explain/world_diff.py starts
    stale = ctx.memory.invalidate(changed_id, source=Source("Pipeline")) if _written(ctx, changed_id) else []

    previous = {(c["code"], c["refs"]) for c in run.cards}
    run.cards = investigator.write_cards(ctx, run.evidence)
    # Re-check every card that is new or rests on the changed entity; the others keep their review.
    fresh = [c for c in run.cards if (c["code"], c["refs"]) not in previous or entity in c["refs"]]
    kept_reviews = {}
    for card in run.cards:
        old = next((r for r in run.reviews.values() if r.get("code_refs") == (card["code"], card["refs"])), None)
        if old and card not in fresh:
            kept_reviews[card["id"]] = old
    run.reviews = kept_reviews
    _review(run, fresh)

    run.output = _conclude(run, started)
    run.output["what_if"] = {
        "change": {"entity": entity, "field": field_name, "value": value},
        "verdict_before": before, "verdict_after": run.output["verdict"],
        "invalidated_facts": stale, "rechecked_cards": [c["id"] for c in fresh],
    }
    return run.output


# ---- stages ---------------------------------------------------------------------------------

def _review(run: Investigation, cards: list[dict]) -> None:
    """The Evaluator's loop on the given cards, sending the plan back when a refutation is untested."""
    ctx = run.ctx
    for _ in range(ctx.max_rounds):
        pending = [c for c in cards if c["id"] not in run.reviews]
        if not pending:
            return
        for card in pending:
            planned = any(h["id"] == "H5" for h in run.hypotheses)
            if planned and card["id"] not in run.deliveries and any(r.startswith("plot:") for r in card["refs"]):
                run.deliveries[card["id"]] = investigator.check_still_delivering(ctx, card)
            review = evaluator.evaluate(ctx, card, run.hypotheses, run.deliveries.get(card["id"]))
            if review["decision"] == evaluator.REVISE:
                ctx.step("Evaluator", "request_revision", f"{card['id']} {card['code']}", review["reason"],
                         target="Planner", status="degraded" if ctx.degraded else "success")
                run.hypotheses = planner.revise(ctx, run.hypotheses, review["refutation"], review["reason"])
                run.deliveries[card["id"]] = investigator.check_still_delivering(ctx, card)
            else:
                run.reviews[card["id"]] = {**review, "code_refs": (card["code"], card["refs"])}


def _conclude(run: Investigation, started: float) -> dict[str, Any]:
    ctx = run.ctx
    unreviewed = [c["id"] for c in run.cards if c["id"] not in run.reviews]
    decision = decision_for(run.evidence, unreviewed)  # kit: no clear proof after max_rounds -> UNVERIFIABLE
    ctx.step("Writer", "apply_verdict_rules", f"{len(run.cards)} card(s), rules {RULES_VERSION}",
             decision["verdict"] + (f"; unreviewed {', '.join(unreviewed)}" if unreviewed else ""),
             status="success")
    flip = compute_flip(run.evidence)
    ctx.step("Writer", "compute_flip", f"{len(flip['tried'])} single change(s) tried, no model", flip["sentence"],
             target="Writer", status="success")
    report = writer.write(ctx, decision, run.cards, run.reviews)
    report["flip"] = flip["sentence"]
    report["flip_tried"] = flip["tried"]
    report["blocked_cards"] = [r["card"] for r in run.reviews.values() if r["decision"] == evaluator.BLOCK]
    if ctx.notes.get("hmem") is not None:  # information only: never evidence, never in the receipt
        now = hmem_notes.notes_for(run.evidence, decision, ctx.run_id, verdict_seal(decision),
                                   ctx.notes["hmem"].today())
        report["seen_before"] = hmem_notes.seen_before(ctx.notes.get("remembered", []), now)
    report["eudr_export"] = exports.eudr_geojson(run.evidence, decision)
    report["supplier_letters"] = exports.supplier_letters(run.evidence, report)
    report["geofence"] = geofence.investigation(run.evidence.plots)  # a constat: never read by the rules
    ctx.step("Writer", "export_files", "plot file and supplier letters, no model",
             f"{len(report['eudr_export']['plots_no_issue_found']['features'])} delivering plot(s) with no issue "
             f"found, {len(report['eudr_export']['excluded'])} left out; "
             f"{len(report['supplier_letters'])} supplier letter draft(s)", status="success")
    return _output(ctx.run_id, run.sample, run.request, ctx, started, report=report, decision=decision)


def _write_notes(run: Investigation) -> None:
    """The Writer leaves one H-MEM note per supplier concerned. Never after a what_if: those are hypotheses."""
    ctx = run.ctx
    memory = ctx.notes.get("hmem")
    if memory is None or ctx.notes.get("hmem_down"):
        return
    decision = decision_for(run.evidence, [c["id"] for c in run.cards if c["id"] not in run.reviews])
    notes = hmem_notes.notes_for(run.evidence, decision, ctx.run_id, run.output["verdict_sha256"], memory.today())
    try:
        written = [memory.remember(note) for note in notes]
    except hmem_notes.HMemUnavailable as error:
        ctx.step("Writer", "remember_findings", f"{len(notes)} note(s)", f"H-MEM did not answer: {error}"[:300],
                 status="degraded", provider="meterless")
        return
    ctx.step("Writer", "remember_findings", f"{len(notes)} supplier(s) concerned, H-MEM store",
             "; ".join(f"{n.supplier_id} {n.verdict} by {n.because} ({m}, until {n.expires})"
                       for n, m in zip(notes, written)) or "no supplier concerned: nothing to remember",
             status="success", provider="meterless")


# ---- what_if: one value, checked -------------------------------------------------------------

EDITABLE = {
    "alert": {"alert_date": re.compile(r"^\d{4}-\d{2}-\d{2}$"), "confidence": {"low", "high", "highest"}},
    "plot": {"area_ha": float, "lat": float, "lon": float},
}


def apply_change(evidence: Evidence, kind: str, key: str, field_name: str, value: Any) -> Evidence:
    """One checked what_if change on a copy of the evidence; verify.py replays it from the receipt."""
    allowed = EDITABLE.get(kind, {}).get(field_name)
    if allowed is None:
        raise ValueError(f"can change {sorted(f'{k}.{f}' for k, fs in EDITABLE.items() for f in fs)}, "
                         f"not {kind}.{field_name}")
    if allowed is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field_name} takes a number")
        value = float(value)
    elif isinstance(allowed, set) and value not in allowed:
        raise ValueError(f"{field_name} takes one of {sorted(allowed)}")
    elif isinstance(allowed, re.Pattern) and not (isinstance(value, str) and allowed.match(value)):
        raise ValueError(f"{field_name} takes a date YYYY-MM-DD")

    rows = evidence.alerts if kind == "alert" else evidence.plots
    id_field = "alert_id" if kind == "alert" else "plot_id"
    if not any(r[id_field] == key for r in rows or []):
        raise ValueError(f"no {kind} {key} in this run's evidence")
    updated = [dict(r, **{field_name: value}) if r[id_field] == key else r for r in rows]
    return replace(evidence, **{"alerts" if kind == "alert" else "plots": updated})


def _written(ctx: RunContext, node_id: str) -> bool:
    try:
        return ctx.memory.view("graph", node_id, by="Pipeline") is not None
    except Exception:  # noqa: BLE001 - an unknown id is simply not in the World Model
        return False


# ---- request and output ---------------------------------------------------------------------

def _read_request(request: dict) -> tuple[str, str, dict, Optional[dict]]:
    body = request.get("input", request)
    claim = body.get("claim")
    if not isinstance(claim, str) or not claim.strip():
        raise ValueError("input.claim must be a non-empty string")
    company_id = body.get("company_id", "chocodurable")
    options = request.get("options")
    options = {} if options is None else options
    if not isinstance(options, dict):
        raise ValueError("options must be an object")
    return claim.strip()[:1000], company_id, options, body.get("plot")


def _output(run_id, sample, request, ctx, started, *, report=None, decision=None, too_vague=False,
            out_of_scope=None) -> dict:
    answers = [{"query_id": a.query_id, "rows_sha256": a.rows_sha256} for a in ctx.gate.answers]
    out = {
        "run_id": run_id,
        "status": "out_of_scope" if out_of_scope else "too_vague" if too_vague else "success",
        "mode": "sample" if sample else "live",
        "degraded": ctx.degraded,
    }
    if out_of_scope:
        out["summary"] = out_of_scope
    elif too_vague:
        out["summary"] = extractor.TOO_VAGUE
    else:
        out.update(report)
        out["verdict_sha256"] = verdict_seal(decision)
    out["fingerprints"] = {
        "input_sha256": sha256_of(request),
        "data_sha256": sha256_of(answers),
        "rules_version": RULES_VERSION,
        "rules_sha256": sha256_of(RULES_FILE.read_text(encoding="utf-8")),
    }
    out["log_file"] = f"logs/{run_id}.jsonl"
    out["execution_time_seconds"] = round(time.perf_counter() - started, 2)
    return out


def _remember(run: Investigation) -> None:
    RUNS[run.ctx.run_id] = run
    while len(RUNS) > KEPT_RUNS:
        RUNS.popitem(last=False)


def checked_run_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not _RUN_ID.match(value):
        raise ValueError(f"run_id must match {_RUN_ID.pattern}")
    return value
