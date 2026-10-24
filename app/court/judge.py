"""Judge: app/rules.py, which renders the verdict; the Writer words the judgment. The model never decides.

hold_court() runs up to three rounds on a finished run. Round 1: the Plaintiff argues each claim
part, the Defense puts the cards on that part against it, and the Judge applies the rules: an
argument for a part the rules did not find PROVEN is refuted. Rounds 2 and 3: the Plaintiff may
answer a counter-argument that holds with another piece; the Defense answers with the Evaluator's
own test, and the Judge keeps the answer standing only if that test broke the evidence. The
verdict is the run's, computed again by the rules and checked equal: the court never changes it.

Every intervention is one trace line (Plaintiff, Evaluator, Judge, Writer). The court is kept
with the run and computed again only after a what_if.
"""
import json
import re
import threading
from typing import Any, Optional

from app.court import defense, plaintiff
from app.court import pieces as P
from app.receipt import decision_for
from app.rules import CONTRADICTED, PROVEN, UNVERIFIABLE

ROUNDS = 3
VERDICTS = (PROVEN, CONTRADICTED, UNVERIFIABLE)
_LOCK = threading.Lock()  # two requests for the same court must not both write its trace lines
_NUMBER = re.compile(r"\d+(?:\.\d+)?")

JUDGMENT_SYSTEM = (
    "You are the Writer of a court of inquiry. Word the judgment in one sentence of at most 60 words, for a "
    "compliance analyst. The verdict and the rule are given and final: state them as given, never another verdict. "
    "Say which of the Plaintiff's arguments stand and which were refuted, and by what. Use only the facts and "
    "numbers in the data."
)
JUDGMENT_SCHEMA = {"type": "object", "required": ["judgment"], "additionalProperties": False,
                   "properties": {"judgment": {"type": "string", "minLength": 10, "maxLength": 500}}}


def hold_court(run: Any) -> dict:
    """The court of a finished run (an orchestrator.Investigation): its rounds, the verdict, the judgment."""
    key = (len(run.changes), run.output.get("verdict_sha256"))
    with _LOCK:
        kept = run.ctx.notes.get("court")
        if kept and kept["key"] == key:
            return kept["court"]
        court = _trial(run)
        run.ctx.notes["court"] = {"key": key, "court": court}
        return court


def _trial(run: Any) -> dict:
    ctx = run.ctx
    unreviewed = [c["id"] for c in run.cards if c["id"] not in run.reviews]
    decision = decision_for(run.evidence, unreviewed)
    verdict = run.output.get("verdict")
    if decision["verdict"] != verdict:
        raise RuntimeError(f"run {ctx.run_id}: the rules give {decision['verdict']}, the run said {verdict}")
    findings = {(f["code"], tuple(f["refs"])) for f in decision["findings"]}
    parts = {p["claim_part"]: p for p in decision["parts"]}
    rule = _rule(decision)
    cards = {c["id"]: {**c, "review": run.reviews.get(c["id"]),
                       "tried": (run.reviews.get(c["id"]) or {}).get("tried", [])} for c in run.cards}
    pieces = P.pieces_of(ctx)
    case = plaintiff.docket(ctx, (run.request.get("input") or run.request).get("claim", ""), run.cards, pieces)
    lines = {card_id: {"raised": [], "cited": []} for card_id in cards}
    providers, rounds, numbers = [], [], {"P": 0, "D": 0}

    # Round 1: one argument per claim part, the cards on that part against it.
    arguments, info = plaintiff.opening(ctx, list(parts), pieces, case)
    providers.append(info)
    said = [_said(ctx, 1, numbers, a, info) for a in arguments]
    counters = []
    for argument in (a for a in said if not a["struck"]):
        against = [c for c in cards.values() if c["part"] == argument["part"]]
        for card in against:
            lines[card["id"]]["cited"] += [plaintiff.piece(c) for c in argument["cites"]]
        found = defense.against_part(ctx, against, findings, numbers["D"] + 1)
        numbers["D"] += len(found)
        counters += [{**c, "answers": argument["id"]} for c in found]
    for argument in said:
        argument["status"] = ("withdrawn" if argument["struck"] else
                              "standing" if parts[argument["part"]]["verdict"] == PROVEN else "refuted")
    rounds.append(_judged(ctx, 1, said, counters, verdict, rule, decision))

    # Rounds 2 and 3: an answer to each counter-argument that still holds, with another piece.
    for n in range(2, ROUNDS + 1):
        holding = [c for c in counters if c["holds"]]
        if not holding:
            break
        replies, info = plaintiff.answers(ctx, holding, lines, cards, pieces, case)
        if not replies:
            ctx.step("Plaintiff", "rest", f"round {n}: {len(holding)} counter-argument(s) hold",
                     "no other piece to cite against them: the Plaintiff rests", target="Writer",
                     status="degraded" if ctx.degraded else "success")
            break
        providers.append(info)
        said = [_said(ctx, n, numbers, a, info) for a in replies]
        counters = []
        for argument in said:
            line = lines[argument["card"]]
            line["raised"].append(argument["angle"])
            if argument["struck"]:
                argument["status"] = "withdrawn"
                continue
            line["cited"] += [plaintiff.piece(c) for c in argument["cites"]]
            numbers["D"] += 1
            counter = defense.against_answer(ctx, cards[argument["card"]], argument["angle"], findings,
                                             f"D{numbers['D']}")
            counters.append({**counter, "answers": argument["id"]})
            argument["status"] = "refuted" if counter["holds"] else "standing"
        rounds.append(_judged(ctx, n, said, counters, verdict, rule, decision))

    judgment, _ = _judgment(ctx, verdict, rule, decision, rounds)
    return {
        "run_id": ctx.run_id,
        "verdict": verdict,
        "rounds": [_public(r) for r in rounds],
        "judgment": judgment,
        "provider": next((i["provider"] for i in providers if i["provider"] != "rules"), "rules"),
    }


# ---- one round -------------------------------------------------------------------------------

def _said(ctx: Any, n: int, numbers: dict, argument: dict, info: dict) -> dict:
    """Number one argument and write its trace line; a struck citation gets the Judge's line too."""
    numbers["P"] += 1
    argument = {**argument, "id": f"P{numbers['P']}", "answers": argument.get("counter")}
    by_model = argument["by"] == "model"
    cites = "; ".join(f"{c['source']} {c['ref']}" for c in argument["cites"]) or "no piece"
    ctx.step("Plaintiff", "argue" if n == 1 else "answer",
             f"round {n}: " + (f"claim part {argument['part']}" if n == 1
                               else f"answers {argument['answers']} with {argument['angle']}"),
             f"{argument['id']}: {argument['claim']} [{cites}]", target="Evaluator",
             status="success" if by_model else "degraded",
             provider=info["provider"] if by_model else "rules", model=info.get("model") if by_model else None)
    if argument["struck"]:
        ctx.step("Judge", "strike_argument", f"round {n}: {argument['id']} cites {cites}",
                 f"{argument['id']} withdrawn: {argument['struck']}", target="Plaintiff")
    return argument


def _judged(ctx: Any, n: int, said: list[dict], counters: list[dict], verdict: str, rule: int,
            decision: dict) -> dict:
    """The Defense's lines, then the Judge's: who stands, who is refuted, and by what."""
    for counter in counters:
        ctx.step("Evaluator", "counter_argument", f"round {n}: answers {counter['answers']}",
                 f"{counter['id']}: {counter['query_id'] or 'no query'}, {len(counter['rows'])} row(s), "
                 f"{'holds' if counter['holds'] else 'does not hold'}: {counter['detail']}",
                 target="Judge", rows=len(counter["rows"]))
    standing = [a["id"] for a in said if a["status"] == "standing"]
    refuted = [a["id"] for a in said if a["status"] == "refuted"]
    by = {a["id"]: [c["id"] for c in counters if c["answers"] == a["id"] and c["holds"]] for a in said}
    opening = f"{verdict} by rule {rule}: {_deciding(decision)}." if n == 1 else \
        f"The verdict stays {verdict} by rule {rule}."
    told = [f"{a} refuted by {', '.join(by[a]) or 'the rules'}" for a in refuted]
    told += [f"{', '.join(standing)} stand{'s' if len(standing) == 1 else ''}"] if standing else []
    told += [f"{a['id']} withdrawn: {a['struck']}" for a in said if a["status"] == "withdrawn"]
    finding = " ".join([opening] + (["; ".join(told) + "."] if told else []))
    more = n < ROUNDS and any(c["holds"] for c in counters)
    ctx.step("Judge", "judge_round", f"round {n}: {len(said)} argument(s), {len(counters)} counter-argument(s)",
             finding, target="Plaintiff" if more else "Writer")
    return {"n": n, "plaintiff": said, "defense": counters,
            "judge": {"rule": f"rule {rule}", "finding": finding, "standing": standing, "refuted": refuted}}


def _public(round_: dict) -> dict:
    """A round as GET /runs/{id}/court gives it."""
    return {
        "n": round_["n"],
        "plaintiff": [{"id": a["id"], "claim": a["claim"], "cites": a["cites"], "status": a["status"],
                       "answers": a["answers"]} for a in round_["plaintiff"]],
        "defense": [{"id": c["id"], "answers": c["answers"], "refutation": c["refutation"],
                     "query_id": c["query_id"], "sql": c["sql"], "rows": c["rows"], "holds": c["holds"],
                     "detail": c["detail"]} for c in round_["defense"]],
        "judge": round_["judge"],
    }


# ---- the rules' verdict, and the judgment ------------------------------------------------------

def _rule(decision: dict) -> int:
    """The rule that decides the verdict: the lowest rule among the parts that carry it (4 when PROVEN)."""
    rules = [p["rule"] for p in decision["parts"] if p["verdict"] == decision["verdict"]]
    return min(rules) if rules else min((p["rule"] for p in decision["parts"] if p["verdict"] != PROVEN), default=4)


def _deciding(decision: dict) -> str:
    deciding = [f for f in decision["findings"] if f["verdict"] == decision["verdict"]]
    if deciding:
        return deciding[0]["detail"]
    if decision["verdict"] == PROVEN:
        return "no check found anything against the promise"
    return "a card was still unreviewed after the Evaluator's last round"


def _judgment(ctx: Any, verdict: str, rule: int, decision: dict, rounds: list[dict]) -> tuple[str, dict]:
    """The Writer's sentence: the model's if it passes the code review, else the template."""
    said = [a for r in rounds for a in r["plaintiff"]]
    count = {status: sum(a["status"] == status for a in said) for status in ("standing", "refuted", "withdrawn")}
    were = {status: "was" if n == 1 else "were" for status, n in count.items()}
    template = (f"The court upholds {verdict} by rule {rule}: {_deciding(decision)}. Of the Plaintiff's "
                f"{len(said)} argument(s), {count['standing']} {'stands' if count['standing'] == 1 else 'stand'} "
                f"and {count['refuted']} {were['refuted']} refuted by the Evaluator's own queries and the rules"
                + (f"; {count['withdrawn']} {were['withdrawn']} withdrawn for a citation not found"
                   if count["withdrawn"] else "") + "; none changes the verdict.")
    if ctx.model is not None:
        record = json.dumps([{"round": r["n"], "judge": r["judge"]["finding"],
                              "arguments": [{"id": a["id"], "claim": a["claim"], "status": a["status"]}
                                            for a in r["plaintiff"]],
                              "counters": [{"id": c["id"], "answers": c["answers"], "holds": c["holds"],
                                            "detail": c["detail"]} for c in r["defense"]]} for r in rounds])
        given = json.dumps({"verdict": verdict, "rule": f"rule {rule}", "deciding_finding": _deciding(decision)})
        answer, info = ctx.model.complete_json("Writer", JUDGMENT_SYSTEM,
                                               [("verdict", given), ("court record", record)], JUDGMENT_SCHEMA)
        if answer is not None:
            text = " ".join(answer["judgment"].split())
            refused = _refused(text, verdict, [template, given, record])
            if refused is None:
                ctx.step("Writer", "write_judgment", f"{len(rounds)} round(s), verdict {verdict} from the rules",
                         text, target="Pipeline", provider=info["provider"], model=info.get("model"))
                return text, info
            ctx.step("Writer", "review_judgment", "the model's judgment", f"refused, {refused}: the template is used",
                     target="Writer", status="degraded")
    ctx.step("Writer", "write_judgment", f"{len(rounds)} round(s), verdict {verdict} from the rules", template,
             target="Pipeline", status="degraded", provider="rules")
    return template, {"provider": "rules", "model": None}


def _refused(text: str, verdict: str, sources: list[str]) -> Optional[str]:
    """Code review of the model's judgment: the rules' verdict and no other, no number missing from the record."""
    words = text.upper()
    if verdict not in words:
        return f"it does not state the verdict {verdict}"
    other = [v for v in VERDICTS if v != verdict and re.search(rf"\b{v}\b", words)]
    if other:
        return f"it names another verdict ({', '.join(other)})"
    known = {number for source in sources for number in _NUMBER.findall(source)}
    invented = [number for number in _NUMBER.findall(text) if number not in known]
    if invented:
        return f"it gives numbers that are not in the record ({', '.join(invented[:3])})"
    return None
