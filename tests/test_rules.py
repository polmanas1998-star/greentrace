"""The verdict rules on every trap case, fed with a perfect reading of the reports.

This isolates the rules: if a case fails here, the bug is in app/rules.py or
app/checks/, not in the agents or the model.
"""
import pytest

from app.rules import CONTRADICTED, PROVEN, UNVERIFIABLE, Evidence, Extracted, decide
from app.world.cases import CASES, build_case
from app.world.store import dq_invalid_records, to_sqlite

# What a careful reader takes from each case's reports (the model's job, in the real run).
PERFECT_READING = {
    12: Extracted(mentions=({"doc": "ngo_2025", "paragraph_no": 9, "supplier_name": "Agnibi", "year": 2022},)),
    13: Extracted(mentions=({"doc": "ngo_2025", "paragraph_no": 9, "supplier_name": "Agnibi", "year": 2022},)),
    15: Extracted(mentions=({"doc": "ngo_2025", "paragraph_no": 9, "supplier_name": "Agnibi",
                             "cooperative_group": "Union Kpanbia", "year": 2022},)),
    17: Extracted(scope="certified"),
    18: Extracted(admissions=({"doc": "rse_2025", "section": "annex", "paragraph_no": 4,
                               "plots": ["P-005", "P-011", "P-017"], "year": 2022},)),
}


def evidence(number, extracted=None):
    world = build_case(number)
    alerts_down = "alerts" in world["meta"]["unavailable_sources"]
    return Evidence(
        company=world["company"][0], suppliers=world["suppliers"], plots=world["plots"],
        deliveries=world["deliveries"], alerts=None if alerts_down else world["alerts"],
        dq_issues=dq_invalid_records(to_sqlite(world)),
        extracted=extracted if extracted is not None else PERFECT_READING.get(number, Extracted()),
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.number:02d}_{c.slug}")
def test_rules_give_the_expected_verdict(case):
    assert decide(evidence(case.number))["verdict"] == case.expected


def test_same_evidence_same_decision():
    assert decide(evidence(2)) == decide(evidence(2))


def test_case_2_names_the_alert_and_the_plot():
    result = decide(evidence(2))
    part = next(p for p in result["parts"] if p["claim_part"] == "no_deforestation")
    assert (part["verdict"], part["rule"], part["codes"]) == (CONTRADICTED, 2, ["confirmed_alert_in_delivering_plot"])
    finding = next(f for f in result["findings"] if f["verdict"] == CONTRADICTED)
    assert finding["refs"] == ("alert:A-221", "plot:P-014")


def test_base_case_is_proven_by_rule_4_on_every_part():
    assert [(p["verdict"], p["rule"]) for p in decide(evidence(1))["parts"]] == [(PROVEN, 4)] * 4


# ---- each rule on its own: break the input, watch the verdict move -------------------------

def test_a_stopped_plot_is_not_contradicted_by_a_later_alert():
    e = evidence(2)
    stopped = [d for d in e.deliveries if not (d["plot_id"] == "P-014" and d["year"] >= 2022)]
    assert decide(Evidence(**{**e.__dict__, "deliveries": stopped}))["verdict"] != CONTRADICTED


def test_a_contradiction_is_not_hidden_by_a_defect_on_another_plot():
    e = evidence(2)
    swapped = [dict(p, lat=p["lon"], lon=p["lat"]) if p["plot_id"] == "P-027" else p for p in e.plots]
    assert decide(Evidence(**{**e.__dict__, "plots": swapped}))["verdict"] == CONTRADICTED


def test_an_admission_contradicts_even_when_alerts_are_down():
    e = evidence(20, PERFECT_READING[18])
    assert decide(e)["verdict"] == CONTRADICTED


def test_a_mention_without_a_name_changes_nothing():
    reading = Extracted(mentions=({"doc": "ngo_2025", "paragraph_no": 9, "supplier_name": None, "year": 2022},))
    assert decide(evidence(11, reading))["verdict"] == PROVEN


def test_a_namesake_without_its_union_is_ambiguous():
    reading = Extracted(mentions=({"doc": "ngo_2025", "paragraph_no": 9, "supplier_name": "Agnibi", "year": 2022},))
    assert decide(evidence(15, reading))["verdict"] == UNVERIFIABLE


def test_a_mention_before_the_cutoff_changes_nothing():
    reading = Extracted(mentions=({"doc": "ngo_2025", "paragraph_no": 9, "supplier_name": "Agnibi", "year": 2019},))
    assert decide(evidence(12, reading))["verdict"] == PROVEN


def test_missing_the_admission_misses_the_contradiction():
    # Rules alone cannot read the annex: this is the gap the model fills (case 18).
    assert decide(evidence(18, Extracted()))["verdict"] == PROVEN
