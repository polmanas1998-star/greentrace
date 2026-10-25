"""Writer: the evidence file, from approved cards only, every sentence with its sources.

The verdict itself comes from app/rules.py; the Writer only words it. Rule fallback =
sentence templates filled with the cards' values (kit, section 4). Its review checks
are code: a sentence without a source, or a verdict the rules did not give, is refused.
"""
from app.agents.context import RunContext
from app.rules import CONTRADICTED, PROVEN, UNVERIFIABLE

SUMMARY = {
    PROVEN: "The claim holds on every point checked: traced volume, alerts, yields and scope.",
    CONTRADICTED: "The claim does not hold: {detail}.",
    UNVERIFIABLE: "The claim cannot be confirmed or refuted from the data: {detail}.",
}
UNTESTED = {
    "confirmed_alert_in_delivering_plot": "No field visit of {plot}; a satellite alert can be a false detection.",
    "alert_near_point_plot": "{plot} has no polygon: its real outline may or may not include the alert.",
    "sources_in_conflict": "The NGO account has not been checked on the ground.",
    "forest_loss_in_delivering_plot": "No field visit of {plot}; 30 m satellite maps can misread a few cells.",
    "forest_loss_below_threshold": "No field visit of {plot}; the loss is too small for the maps to decide.",
    "tree_cover_loss_outside_2020_forest": "No field visit of {plot}; the EU 2020 map may have missed forest there.",
}
ACTIONS = {
    "confirmed_alert_in_delivering_plot": "Ask {supplier} for a field report on {plot}, and hold its deliveries.",
    "alert_near_point_plot": "Ask the supplier for the polygon of {plot}.",
    "polygon_missing": "Ask the supplier for the polygon of {plot}.",
    "invalid_coordinates": "Ask the supplier to correct the coordinates of {plot}.",
    "volume_not_traced": "Ask every supplier without plots for its plot registry.",
    "impossible_yield": "Ask the supplier which other plots the volume of {plot} came from.",
    "plot_declared_twice": "Ask both suppliers which one farms {plot}.",
    "sources_in_conflict": "Ask {supplier} for its plot records for the year the NGO cites.",
    "narrower_scope": "Ask the company to restate the claim for the scope its report covers.",
    "company_admission": "Read the annex with the company and exclude the plots it names.",
    "no_independent_source": "Get alert data covering the supply area before relying on the claim.",
    "source_unavailable": "Run the check again when the alert source answers.",
    "forest_loss_in_delivering_plot": "Ask {supplier} for a field report on {plot}, and hold its deliveries.",
    "forest_loss_below_threshold": "Ask {supplier} for a field report on {plot}.",
    "tree_cover_loss_outside_2020_forest": "Ask {supplier} for a field report on {plot}.",
    "no_reference_data": "Load reference maps that cover {plot} before relying on the claim.",
}
LIMITATIONS = [
    "Synthetic company and alerts, placed on real places. A satellite alert is not legal proof.",
    "The verdict is computed by fixed rules from the rows listed; a human must review it before any decision.",
]
# What the EUDR asks of the operator after each verdict (Regulation (EU) 2023/1115: risk assessment and
# mitigation, Articles 10 and 11; due-diligence records kept for at least five years). Fixed words: they
# follow the verdict and never decide it, so they are not part of the seal.
EUDR_NEXT = {
    "CONTRADICTED": "Under the EUDR this cocoa may not be placed on the EU market or exported: the evidence shows "
                    "deforestation after 31 December 2020. Hold the deliveries from the plots named, and keep the "
                    "record of this check for at least five years.",
    "UNVERIFIABLE": "Under the EUDR, unverifiable is not a resting state: until the risk is shown to be negligible, "
                    "this cocoa may not be placed on the EU market (Articles 10 and 11). Mitigate first (ask for the "
                    "polygon, a field visit or an audit), check again, and keep what was done and its outcome for at "
                    "least five years.",
    "PROVEN": "No issue found on what was checked. It is one input to the operator's EUDR risk assessment, not the "
              "decision: a human reviews it, and the record is kept for at least five years.",
}
DRAWN_LIMITATIONS = [
    "Plot {plot} was drawn by the analyst and declared for {supplier}: it is not a row of the registry.",
    "Reference maps: forest in 2020 from the EU JRC GFC2020 v4, tree cover loss by year from "
    "Hansen/UMD/Google/USGS/NASA (GFC v1.12), on 30 m cells around Soubre.",
]


def write(ctx: RunContext, decision: dict, cards: list[dict], reviews: dict[str, dict]) -> dict:
    deciding = [c for c in cards if c["verdict"] == decision["verdict"]]
    detail = deciding[0]["detail"] if deciding else ""
    findings = [_finding(c, reviews.get(c["id"])) for c in cards]
    report = {
        "verdict": decision["verdict"],
        "summary": SUMMARY[decision["verdict"]].format(detail=detail),
        "parts": decision["parts"],
        "findings": findings,
        "untested_factors": _filled(UNTESTED, cards),
        "recommended_actions": _filled(ACTIONS, cards),
        "limitations": LIMITATIONS + _drawn_limitations(ctx),
        "human_review": "Required before any decision.",
        "eudr_next": EUDR_NEXT[decision["verdict"]],
    }
    _review(report)
    ctx.step("Writer", "write_report", f"{len(cards)} card(s), verdict {decision['verdict']} from the rules",
             report["summary"], status="degraded" if ctx.degraded else "success")
    ctx.step("Writer", "review_report", "every finding sourced, verdict copied from the rules",
             f"{len(findings)} finding(s) checked", status="success")
    return report


def _finding(card: dict, review: dict | None) -> dict:
    return {
        "claim_part": card["part"],
        "code": card["code"],
        "finding": card["detail"],
        "verdict": card["verdict"],
        "rule": card["rule"],
        "evidence": [{"source": ref.split(":", 1)[0], "id": ref.split(":", 1)[1]} for ref in card["refs"]],
        "checked_by": ["investigator"] + (["evaluator:own_query"] if review and review["tried"] else
                                          ["evaluator"] if review else []),
        "refutations_tried": [{"id": t["id"], "result": "held" if t["holds"] else "rejected", "detail": t["detail"]}
                              for t in (review or {}).get("tried", [])],
    }


def _filled(templates: dict[str, str], cards: list[dict]) -> list[str]:
    lines = []
    for card in cards:
        refs = dict(r.split(":", 1) for r in card["refs"] if ":" in r)
        template = templates.get(card["code"])
        if template:
            line = template.format(plot=refs.get("plot", "the plot"), supplier=refs.get("supplier", "the supplier"))
            if line not in lines:
                lines.append(line)
    return lines


def _drawn_limitations(ctx: RunContext) -> list[str]:
    plot = ctx.notes.get("declared_plot")
    if not plot:
        return []
    return [line.format(plot=plot["plot_id"], supplier=plot["supplier_id"]) for line in DRAWN_LIMITATIONS]


def _review(report: dict) -> None:
    """Code review of the text: no finding without evidence, no verdict the rules did not give."""
    for finding in report["findings"]:
        if not finding["evidence"]:
            raise ValueError(f"finding without a source: {finding['finding']}")
    if report["verdict"] not in (PROVEN, CONTRADICTED, UNVERIFIABLE):
        raise ValueError(f"unknown verdict {report['verdict']}")
