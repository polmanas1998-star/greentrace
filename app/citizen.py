"""Citizen mode: a shopper asks about a chocolate bar's promise, and gets one plain sentence.

GreenTrace judges the EVIDENCE behind a promise, never a brand. It only knows one fictional
brand of its demo supply chain: "Kalou Chocolat", whose bar "Kalou Dark 70 %" is made with
cocoa bought from ChocoDurable SA, the synthetic company every sample case is about. For it,
a real investigation runs behind (the same agents and rules as the analyst's view, case 2 by
default) and the answer is the rules' verdict in plain words:

  PROVEN        "True as far as the evidence goes"
  CONTRADICTED  "False"
  UNVERIFIABLE  "No proof found"

with two or three reasons a shopper can read and the link to the full evidence file. Any other
brand gets "No data on this brand" and no investigation: no verdict on a real brand, no grade
of any brand, ever.
"""
import re
import unicodedata
from typing import Any, Callable

from app.rules import CONTRADICTED, PROVEN, UNVERIFIABLE
from app.world.cases import CASES_BY_NUMBER

BRAND, PRODUCT, COMPANY_ID, COMPANY_NAME = "Kalou Chocolat", "Kalou Dark 70 %", "chocodurable", "ChocoDurable SA"
DEFAULT_CASE = 2
ANSWERS = {PROVEN: "True as far as the evidence goes", CONTRADICTED: "False", UNVERIFIABLE: "No proof found"}
NO_DATA_BRAND = "No data on this brand: GreenTrace only knows its demo supply chain."
NO_DATA_PRODUCT = f"No data on this product: GreenTrace only knows {PRODUCT} in its demo supply chain."
ABOUT = (f"This answer is about the evidence behind the promise, from {BRAND}'s fictional supplier {COMPANY_NAME}; "
         "it is not a rating of the brand.")
MAX_TEXT = 500

# What each finding means, in words a shopper reads. Unknown codes fall back to the finding itself.
PLAIN = {
    "confirmed_alert_in_delivering_plot": "Satellites saw forest cleared on a farm that kept selling cocoa afterwards.",
    "forest_loss_in_delivering_plot": "Forest maps show forest lost after 2020 on a farm that kept selling cocoa.",
    "company_admission": "The supplier's own report admits clearing forest for cocoa after 2020.",
    "volume_not_traced": "Part of the cocoa cannot be traced back to any farm.",
    "impossible_yield": "A farm claims more cocoa than a farm of its size can grow.",
    "plot_declared_twice": "Two suppliers declare the same farm.",
    "unconfirmed_alert_in_plot": "A satellite alert on a farm is too uncertain to conclude either way.",
    "alert_near_point_plot": "A satellite alert lies near a farm whose borders were never mapped.",
    "polygon_missing": "A large farm was registered as a single point, so its borders cannot be checked.",
    "invalid_coordinates": "A farm's GPS position is wrong: it is not where cocoa grows.",
    "sources_in_conflict": "An NGO reports clearing by a supplier, and no satellite alert confirms or rules it out.",
    "narrower_scope": "The promise in the report covers only part of the cocoa, not all of it.",
    "no_independent_source": "There is no independent satellite data for the area, only the supplier's word.",
    "source_unavailable": "The satellite alert source did not answer, so the farms could not be checked.",
    "no_reference_data": "No forest map covers a farm, so it could not be checked.",
    "forest_loss_below_threshold": "A little forest was lost on a farm, too little for the maps to decide.",
    "tree_cover_loss_outside_2020_forest":
        "Trees were lost on a farm, but the EU map did not count that land as forest.",
}
PROVEN_REASONS = {
    "coverage": "Every tonne of cocoa bought this year is traced to a known farm.",
    "no_deforestation": "No confirmed forest loss after 2020 was found on the farms that sell the cocoa.",
    "plausibility": "Each farm can grow the cocoa it declares, and no farm is declared twice.",
    "scope": "The supplier's report promises this for all of its cocoa, not just part of it.",
}


def _key(text: str) -> str:
    """Case, accents, spaces and punctuation do not matter: 'kalou-chocolat' is Kalou Chocolat."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").casefold()
    return " ".join(re.findall(r"[a-z0-9]+", plain))


def _text(body: dict, name: str, required: bool) -> str:
    value = body.get(name, "")
    if value is None:
        value = ""
    if not isinstance(value, str) or len(value) > MAX_TEXT:
        raise ValueError(f"{name} must be a string of at most {MAX_TEXT} characters")
    if required and not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def ask(body: dict, investigate: Callable[[dict], dict], run_id: str) -> dict[str, Any]:
    """The shopper's answer. investigate(request) runs the real pipeline (app.pipeline.run_case)."""
    if not isinstance(body, dict):
        raise ValueError("the question must be a JSON object")
    brand, product = _text(body, "brand", True), _text(body, "product", False)
    promise = _text(body, "promise", True)
    case = body.get("case", DEFAULT_CASE)
    if isinstance(case, bool) or not isinstance(case, int) or case not in CASES_BY_NUMBER:
        raise ValueError(f"case must be one of the sample cases 1-{len(CASES_BY_NUMBER)}")
    if _key(brand) != _key(BRAND):
        return {"answer": None, "sentence": NO_DATA_BRAND, "reasons": [], "evidence_file": None}
    if product and _key(product) != _key(PRODUCT):
        return {"answer": None, "sentence": NO_DATA_PRODUCT, "reasons": [], "evidence_file": None}

    out = investigate({"run_id": run_id, "input": {"claim": promise, "company_id": COMPANY_ID},
                       "options": {"case": case}})
    if out.get("status") == "too_vague":
        answer, reasons = ANSWERS[UNVERIFIABLE], ["The promise does not say anything that data can check.",
                                                  "Ask about deforestation, the farms or where the cocoa comes from."]
    else:
        answer, reasons = ANSWERS[out["verdict"]], _reasons(out)
    return {
        "answer": answer,
        "sentence": f"{answer}: {reasons[0][0].lower()}{reasons[0][1:]}",
        "reasons": reasons,
        "about": ABOUT,
        "brand": BRAND, "product": PRODUCT, "supplier": COMPANY_NAME,
        "run_id": out["run_id"],
        "verdict": out.get("verdict"),
        "verdict_sha256": out.get("verdict_sha256"),
        "evidence_file": f"/runs/{out['run_id']}",
        "audit_package": f"/runs/{out['run_id']}/package",
    }


def _reasons(out: dict) -> list[str]:
    """Two or three reasons: what held for a true claim; otherwise the findings that decided it, and
    how hard GreenTrace tried to prove the main one wrong."""
    verdict = out["verdict"]
    if verdict == PROVEN:
        return [PROVEN_REASONS[p["claim_part"]] for p in out["parts"] if p["claim_part"] in PROVEN_REASONS][:3]
    deciding = [f for f in out["findings"] if f["verdict"] == verdict]
    reasons = list(dict.fromkeys(_plain(f) for f in deciding))[:2]
    tried = deciding[0]["refutations_tried"] if deciding else []
    if tried and not any(t["result"] == "held" for t in tried):
        reasons.append(f"GreenTrace tried {len(tried)} ways to prove that wrong with its own checks; none held.")
    for line in (_plain(f) for f in out["findings"] if f not in deciding):
        if len(reasons) >= 3:
            break
        if line not in reasons:
            reasons.append(line)
    if len(reasons) < 2:
        reasons.append("The verdict comes from fixed rules applied to the data, and a person must review it.")
    return reasons[:3]


def _plain(finding: dict) -> str:
    return PLAIN.get(finding["code"]) or f"{finding['finding'][0].upper()}{finding['finding'][1:]}."
