"""Ask GreenTrace: one question about a finished run, answered from that run's own record only.

POST /runs/{run_id}/ask  {"question": "..."}
-> {"run_id", "question", "answer", "lines": [{"n", "text", "section"}], "diagram", "scout", "provider", "model", "status"}

The lines are what the run already wrote: the report sentence by sentence (the Deep Trace) and each
refutation the Evaluator tried. With a model, the Writer's lane words an answer that must cite line
numbers that exist, and every number in it must appear in a cited line or in the question; anything
else sends the question back to the rules. Without a model (sample mode, no key), the rules answer
with the lines that share the most words with the question, and say that no model was used.
The verdict is never re-decided here.

Meterless Scout Intent reads the question first (app/memory/scout.py): it blocks an injection before any
line is read, and returns a signed execution contract that names the intent; Ask verifies the contract
before it answers, and the intent decides which kind of line comes first. When Scout does not answer,
the regex intents below stand in and the answer says so.

The diagram is the why-chain of the run, drawn from its output and never from the model: the
evidence rows, the refutations the Evaluator tried on them, the finding, the rule that decided, the
verdict, and the one change that would flip it. The nodes the cited lines name are marked "focus".
"""
import re
from typing import Any, Optional

from app.explain import deep_trace
from app.memory import scout

MAX_QUESTION = 500
MAX_LINES = 3
SECTION_WEIGHT = {"verdict": 1.5, "finding": 1.0, "flip": 0.5, "limitation": -0.5, "human_review": -0.5}
SYSTEM = (
    "You answer one question about an EUDR deforestation investigation. Use ONLY the numbered lines in "
    "<data>. Cite the line numbers you used. If the lines do not answer the question, say so plainly. "
    "Never change or argue with the verdict. Answer in the language of the question, in at most 3 sentences."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "lines": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["answer", "lines"],
}
STOP = set("""
a an and are as at be by can could did do does for from had has have how i if in into is it its may might
of on or our should so than that the their them then there these this those to was we were what when where
which who why will with would you your about any not no yes does did
le la les un une des du de et est sont que qui quoi pourquoi comment dans sur pour par pas ne plus ce cette
""".split())
_WORD = re.compile(r"[a-z0-9][a-z0-9.-]*", re.I)
WHY = re.compile(r"\b(why|verdict|reason|pourquoi|raison)\b", re.I)
# a question that names a kind of line asks for those lines, even when it shares no word with them
INTENTS = {
    "refutation": re.compile(r"\b(refut\w*|tried|attack\w*|challeng\w*|break\w*|réfut\w*|attaqu\w*)", re.I),
    "flip": re.compile(r"\b(chang\w*|flip\w*|would|otherwise|instead|bascul\w*)", re.I),
    "limitation": re.compile(r"\b(limit\w*|caveat\w*|uncertain\w*|unsure|doubt\w*|doute\w*)", re.I),
}
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_ID = re.compile(r"\b(?:[ADFP]-\d+|R\d+)\b")
MAX_FINDINGS, MAX_CHECKS = 2, 4
# what each rule of app/rules.py stands for, in a few words
RULE_LABELS = {1: "a source is down or a datum is invalid", 2: "direct evidence that held",
               3: "a doubt the evidence cannot settle", 4: "nothing against the claim"}
PART_LABELS = {"coverage": "every tonne traced to a plot", "no_deforestation": "no forest lost after 2020",
               "plausibility": "volumes a plot can grow", "scope": "the claim covers what it says"}


def lines(run: Any) -> list[dict[str, Any]]:
    """Every line the answer may use, numbered from 1, in report order then refutation order."""
    out = [{"text": s["text"], "section": s["section"]} for s in deep_trace.sentences(run)["sentences"]]
    for finding in run.output.get("findings") or []:
        for tried in finding.get("refutations_tried") or []:
            outcome = "did not hold" if tried.get("result") == "rejected" else str(tried.get("result"))
            out.append({"text": f"{tried['id']} {outcome}: {tried['detail']}", "section": "refutation"})
    return [{"n": n, **line} for n, line in enumerate(out, start=1)]


def _stem(word: str) -> str:
    """delivering, delivered, deliveries -> deliver: enough for the rules to match a question to a line."""
    for suffix in ("ies", "ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _words(text: str) -> set[str]:
    return {_stem(w.lower().strip(".-")) for w in _WORD.findall(text) if (len(w) > 2 or any(ch.isdigit() for ch in w)) and w.lower() not in STOP}


# Scout's intents (meterless/scout/greentrace_intents.json) and the kinds of line each asks for first
SECTIONS_OF_INTENT = {"ask.why": ("verdict", "finding", "refutation"), "ask.change": ("flip",),
                      "ask.refutations": ("refutation",), "ask.limits": ("limitation", "human_review", "untested"),
                      "ask.evidence": (), "GENERAL": ()}


def routed(question: str) -> dict[str, Any]:
    """Scout's decision on the question: blocked, or an intent under a contract Ask has verified."""
    try:
        decision = scout.decide(question)
    except scout.ScoutUnavailable as error:
        return {"engine": scout.ENGINE, "status": "unavailable", "reason": str(error)[:160]}
    if decision["risk"]["level"] == "block":
        return {"engine": scout.ENGINE, "status": "blocked", "reason": decision["risk"].get("reason")}
    contract = decision.get("executionContract")
    if contract is None:  # Scout asked for clarification: Ask goes on with the words of the question
        options = (decision.get("clarification") or {}).get("options") or []
        return {"engine": scout.ENGINE, "status": "clarify", "options": options}
    try:
        verified = scout.verify(contract, "run.lines")
    except scout.ScoutUnavailable as error:
        return {"engine": scout.ENGINE, "status": "unavailable", "reason": str(error)[:160]}
    intent = contract["intent"]["primary"]["id"]
    if intent != "GENERAL" and not verified.get("ok"):
        return {"engine": scout.ENGINE, "status": "refused", "reason": verified.get("reason")}
    return {"engine": scout.ENGINE, "status": "routed", "intent": intent,
            "confidence": contract["intent"]["confidence"], "contract_id": contract["contractId"],
            "verified": bool(verified.get("ok")) or intent == "GENERAL"}


def by_rules(question: str, numbered: list[dict[str, Any]], intent: Optional[str] = None) -> dict[str, Any]:
    wanted = _words(question)
    # an id (R3, P-014, A-221) names one thing: it weighs twice a plain word
    overlap = {line["n"]: sum(2 if any(ch.isdigit() for ch in w) else 1 for w in wanted & _words(line["text"]))
               for line in numbered}
    if WHY.search(question):  # "why this verdict?": the verdict, its findings and what failed to break them
        for line in numbered:
            if line["section"] in ("verdict", "finding", "refutation"):
                overlap[line["n"]] = max(overlap[line["n"]], 1)
    wanted_sections = SECTIONS_OF_INTENT.get(intent) if intent else None
    if wanted_sections is None:  # Scout did not route the question: the regex intents stand in
        wanted_sections = tuple(section for section, pattern in INTENTS.items() if pattern.search(question))
    for line in numbered:
        if line["section"] in wanted_sections:
            overlap[line["n"]] = max(overlap[line["n"]], 3)
    # Among lines that share words with the question, the verdict and its findings come before the caveats.
    scored = sorted(((overlap[line["n"]] + SECTION_WEIGHT.get(line["section"], 0.0), line["n"]) for line in numbered
                     if overlap[line["n"]] > 0), key=lambda s: (-s[0], s[1]))
    picked = _distinct([n for _, n in scored], numbered)[:MAX_LINES]
    if not picked:
        sections = sorted({line["section"] for line in numbered})
        answer = ("No line of this investigation answers that. It holds: " + ", ".join(sections) + ". "
                  "No model was used for this answer.")
    else:
        answer = "No model was used: here are the lines of this investigation closest to your question."
    return {"answer": answer, "cited": picked, "status": "rules"}  # most relevant first


def _distinct(ranked: list[int], numbered: list[dict[str, Any]]) -> list[int]:
    """Drop a line whose words another line already says (the verdict restates its finding)."""
    text = {line["n"]: _words(line["text"]) for line in numbered}
    kept: list[int] = []
    for n in ranked:
        if not any(text[n] <= text[k] or text[k] <= text[n] for k in kept):
            kept.append(n)
    return kept


def diagram(run: Any, cited_lines: list[dict[str, Any]]) -> dict[str, Any]:
    """The why-chain as nodes and edges: evidence -> checks -> finding -> rule -> verdict, and the flip."""
    out = run.output
    focus = {m for line in cited_lines for m in _ID.findall(line["text"])} | {"R0"}  # R0 names the flip
    nodes: list[dict[str, Any]] = []
    edges: list[list[str]] = []

    def node(node_id: str, kind: str, label: str, detail: str = "", status: str = "", names: str = "") -> str:
        """names: the text whose ids put the node in focus (its label and detail unless given)."""
        if all(n["id"] != node_id for n in nodes):
            nodes.append({"id": node_id, "kind": kind, "label": label, "detail": detail, "status": status,
                          "focus": bool(focus & set(_ID.findall(names or label + " " + detail)))})
        return node_id

    verdict = node("verdict", "verdict", str(out.get("verdict") or "no verdict"), out.get("summary") or "",
                   str(out.get("verdict") or ""))
    findings = [f for f in out.get("findings") or [] if f.get("verdict") == out.get("verdict")][:MAX_FINDINGS]
    for i, finding in enumerate(findings):
        rule = node(f"rule-{finding.get('rule')}", "rule", f"Rule {finding.get('rule')}",
                    RULE_LABELS.get(finding.get("rule"), ""))
        found = node(f"finding-{i}", "finding", finding.get("finding") or finding.get("code") or "finding",
                     PART_LABELS.get(finding.get("claim_part"), ""), finding.get("verdict") or "")
        for item in finding.get("evidence") or []:
            edges.append([node(f"{item['source']}:{item['id']}", "evidence", f"{item['source']} {item['id']}"), found])
        for tried in (finding.get("refutations_tried") or [])[:MAX_CHECKS]:
            held = tried.get("result") != "rejected"
            check = node(f"check-{i}-{tried['id']}", "check", f"{tried['id']} {'held' if held else 'did not hold'}",
                         tried.get("detail") or "", "held" if held else "rejected", names=tried["id"])
            edges.append([check, found])
        edges += [[found, rule], [rule, verdict]]
    if not findings:  # nothing decided against the claim: every part passed rule 4 (or another rule decided it)
        for part in out.get("parts") or []:
            part_node = node(f"part-{part['claim_part']}", "finding", PART_LABELS.get(part["claim_part"], part["claim_part"]),
                             f"rule {part.get('rule')}: {RULE_LABELS.get(part.get('rule'), '')}", part.get("verdict") or "")
            edges.append([part_node, verdict])
    if out.get("flip"):
        flip_cited = any(line["section"] == "flip" for line in cited_lines)
        edges.append([verdict, node("flip", "flip", "What would change it", out["flip"],
                                    names="R0" if flip_cited else "-")])
    return {"nodes": nodes, "edges": edges}


def _checked(answer: Any, question: str, numbered: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The model's answer if it only cites lines that exist and states only numbers they contain."""
    if not isinstance(answer, dict) or not isinstance(answer.get("answer"), str) or not answer["answer"].strip():
        return None
    known = {line["n"]: line["text"] for line in numbered}
    cited = answer.get("lines")
    if not isinstance(cited, list) or not cited or not all(isinstance(n, int) and n in known for n in cited):
        return None
    allowed = question + " " + " ".join(known[n] for n in cited)
    if any(number not in allowed for number in _NUMBER.findall(answer["answer"])):
        return None
    return {"answer": answer["answer"].strip(), "cited": sorted(set(cited)), "status": "model"}


def ask(run: Any, question: Any, model: Any = None) -> dict[str, Any]:
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty text")
    question = question.strip()
    if len(question) > MAX_QUESTION:
        raise ValueError(f"question is {len(question)} characters; {MAX_QUESTION} at most")
    numbered = lines(run)
    route = routed(question)
    head = {"run_id": run.output.get("run_id"), "question": question, "verdict": run.output.get("verdict"), "scout": route}
    if route["status"] == "blocked":  # nothing is read, no model is asked
        return {**head, "answer": f"Meterless Scout Intent blocked this question before any line was read "
                                  f"({route['reason']}). Ask about this investigation: its verdict, a plot, an alert or a refutation.",
                "lines": [], "diagram": diagram(run, []), "status": "blocked", "provider": "rules", "model": None}
    if route.get("intent") == "GENERAL":  # Scout says the question is not about this investigation
        sections = ", ".join(sorted({line["section"] for line in numbered}))
        return {**head, "answer": f"That is not a question about this investigation, so no line answers it. "
                                  f"It holds: {sections}. No model was used for this answer.",
                "lines": [], "diagram": diagram(run, []), "status": "rules", "provider": "rules", "model": None}
    result, info = None, {"provider": "rules", "model": None}
    if model is not None:
        data = "\n".join(f"{line['n']}. {line['text']}" for line in numbered)
        reply, info = model.complete_json("Writer", SYSTEM, [("lines", data), ("question", question)], SCHEMA)
        result = _checked(reply, question, numbered)
        if result is None:
            info = {**info, "provider": "rules", "model": None}
    if result is None:
        result = by_rules(question, numbered, route.get("intent"))
    known = {line["n"]: line for line in numbered}
    cited = [known[n] for n in result["cited"]]
    return {**head, "answer": result["answer"], "lines": cited, "diagram": diagram(run, cited),
            "status": result["status"], "provider": info.get("provider"), "model": info.get("model")}
