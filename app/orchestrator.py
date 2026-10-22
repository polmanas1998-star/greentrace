"""One investigation, start to finish (kit, sections 1 and 5).

Extractor -> Planner -> Investigator -> Evaluator, and back: an untested refutation
sends the plan to the Planner and the Investigator reads more, at most max_rounds
times. Then the rules decide and the Writer words it. Every arrow is a trace line.

Mode: "sample" runs on the synthetic world in SQLite (SAMPLE_MODE=true, no network);
"live" runs on Zetaris through the semantic layer. Same agents, same rules.
"""
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from app.adapters.llm import ModelGate
from app.agents import evaluator, extractor, investigator, planner, writer
from app.agents.context import RunContext
from app.canonical import sha256_of
from app.memory.world_model import WorldModel
from app.queries import DataGate, SQLiteEngine, ZetarisEngine
from app.rules import RULES_VERSION, UNVERIFIABLE, decide
from app.world.cases import build_case
from app.world.store import to_sqlite

RULES_FILE = Path(__file__).resolve().parent / "rules.py"
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DEFAULT_SAMPLE_CASE = 2  # the demo world, the same one scripts/load_zetaris.py loads


def sample_mode(options: dict) -> bool:
    if "sample_mode" in options:
        return bool(options["sample_mode"])
    return os.environ.get("SAMPLE_MODE", "true").lower() in ("1", "true", "yes")


def investigate(request: dict, run_id: str, *, log, memory_engine=None, zetaris=None,
                log_dir: Optional[Path] = None, stream=None) -> dict[str, Any]:
    started = time.perf_counter()
    claim, company_id, options = _read_request(request)
    sample = sample_mode(options)

    if sample:
        world = build_case(int(options.get("case", DEFAULT_SAMPLE_CASE)))
        engine = SQLiteEngine(to_sqlite(world), tuple(world["meta"]["unavailable_sources"]))
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
    ctx.step("Pipeline", "receive_case", f"claim on {company_id}",
             f"data {'sample' if sample else 'live'}; model chain {' > '.join(models.chain)}",
             target="Extractor", status="success")

    parsed, extracted = extractor.extract(ctx, claim)
    if not parsed["types"]:
        return _output(run_id, sample, request, ctx, started, too_vague=True)

    hypotheses = planner.plan(ctx, parsed["types"])
    evidence = investigator.gather(ctx, hypotheses, extracted)
    cards = investigator.write_cards(ctx, evidence)

    reviews, deliveries = {}, {}
    for _ in range(ctx.max_rounds):
        pending = [c for c in cards if c["id"] not in reviews]
        if not pending:
            break
        for card in pending:
            review = evaluator.evaluate(ctx, card, hypotheses, deliveries.get(card["id"]))
            if review["decision"] == evaluator.REVISE:
                ctx.step("Evaluator", "request_revision", f"{card['id']} {card['code']}", review["reason"],
                         target="Planner", status="degraded" if ctx.degraded else "success")
                hypotheses = planner.revise(ctx, hypotheses, review["refutation"], review["reason"])
                deliveries[card["id"]] = investigator.check_still_delivering(ctx, card)
            else:
                reviews[card["id"]] = review

    decision = decide(evidence)
    unreviewed = [c["id"] for c in cards if c["id"] not in reviews]
    if unreviewed:  # kit: no clear proof after max_rounds -> UNVERIFIABLE, saying what is missing
        decision = {**decision, "verdict": UNVERIFIABLE}
    ctx.step("Writer", "apply_verdict_rules", f"{len(cards)} card(s), rules {RULES_VERSION}",
             f"{decision['verdict']}" + (f"; unreviewed {', '.join(unreviewed)}" if unreviewed else ""),
             status="success")
    report = writer.write(ctx, decision, cards, reviews)
    report["blocked_cards"] = [r["card"] for r in reviews.values() if r["decision"] == evaluator.BLOCK]
    return _output(run_id, sample, request, ctx, started, report=report, decision=decision)


def _read_request(request: dict) -> tuple[str, str, dict]:
    body = request.get("input", request)
    claim = body.get("claim")
    if not isinstance(claim, str) or not claim.strip():
        raise ValueError("input.claim must be a non-empty string")
    company_id = body.get("company_id", "chocodurable")
    options = request.get("options")
    options = {} if options is None else options
    if not isinstance(options, dict):
        raise ValueError("options must be an object")
    return claim.strip()[:1000], company_id, options


def _output(run_id, sample, request, ctx, started, *, report=None, decision=None, too_vague=False) -> dict:
    answers = [{"query_id": a.query_id, "rows_sha256": a.rows_sha256} for a in ctx.gate.answers]
    out = {
        "run_id": run_id,
        "status": "too_vague" if too_vague else "success",
        "mode": "sample" if sample else "live",
        "degraded": ctx.degraded,
    }
    if too_vague:
        out["summary"] = "Too vague or not supported: the promise names nothing that data can check."
    else:
        out.update(report)
        sealed = {"verdict": decision["verdict"], "parts": decision["parts"],
                  "findings": [(f["code"], f["refs"]) for f in decision["findings"]]}
        out["verdict_sha256"] = sha256_of(sealed)
    out["fingerprints"] = {
        "input_sha256": sha256_of(request),
        "data_sha256": sha256_of(answers),
        "rules_version": RULES_VERSION,
        "rules_sha256": sha256_of(RULES_FILE.read_text(encoding="utf-8")),
    }
    out["log_file"] = f"logs/{run_id}.jsonl"
    out["execution_time_seconds"] = round(time.perf_counter() - started, 2)
    return out


def checked_run_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not _RUN_ID.match(value):
        raise ValueError(f"run_id must match {_RUN_ID.pattern}")
    return value
