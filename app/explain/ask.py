"""Ask GreenTrace: one question about a finished run, answered from that run's own record only.

POST /runs/{run_id}/ask  {"question": "..."}
-> {"run_id", "question", "answer", "lines": [{"n", "text", "section"}], "provider", "model", "status"}

The lines are what the run already wrote: the report sentence by sentence (the Deep Trace) and each
refutation the Evaluator tried. With a model, the Writer's lane words an answer that must cite line
numbers that exist, and every number in it must appear in a cited line or in the question; anything
else sends the question back to the rules. Without a model (sample mode, no key), the rules answer
with the lines that share the most words with the question, and say that no model was used.
The verdict is never re-decided here.
"""
import re
from typing import Any, Optional

from app.explain import deep_trace

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
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


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


def by_rules(question: str, numbered: list[dict[str, Any]]) -> dict[str, Any]:
    wanted = _words(question)
    # an id (R3, P-014, A-221) names one thing: it weighs twice a plain word
    overlap = {line["n"]: sum(2 if any(ch.isdigit() for ch in w) else 1 for w in wanted & _words(line["text"]))
               for line in numbered}
    if WHY.search(question):  # "why this verdict?": the verdict and its findings are the answer
        for line in numbered:
            if line["section"] in ("verdict", "finding"):
                overlap[line["n"]] = max(overlap[line["n"]], 1)
    # Among lines that share words with the question, the verdict and its findings come before the caveats.
    scored = sorted(((overlap[line["n"]] + SECTION_WEIGHT.get(line["section"], 0.0), line["n"]) for line in numbered
                     if overlap[line["n"]] > 0), key=lambda s: (-s[0], s[1]))
    picked = [n for _, n in scored][:MAX_LINES]
    if not picked:
        sections = sorted({line["section"] for line in numbered})
        answer = ("No line of this investigation answers that. It holds: " + ", ".join(sections) + ". "
                  "No model was used for this answer.")
    else:
        answer = "No model was used: here are the lines of this investigation closest to your question."
    return {"answer": answer, "cited": picked, "status": "rules"}  # most relevant first


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
    result, info = None, {"provider": "rules", "model": None}
    if model is not None:
        data = "\n".join(f"{line['n']}. {line['text']}" for line in numbered)
        reply, info = model.complete_json("Writer", SYSTEM, [("lines", data), ("question", question)], SCHEMA)
        result = _checked(reply, question, numbered)
        if result is None:
            info = {**info, "provider": "rules", "model": None}
    if result is None:
        result = by_rules(question, numbered)
    known = {line["n"]: line for line in numbered}
    return {
        "run_id": run.output.get("run_id"), "question": question, "answer": result["answer"],
        "lines": [known[n] for n in result["cited"]], "verdict": run.output.get("verdict"),
        "status": result["status"], "provider": info.get("provider"), "model": info.get("model"),
    }
