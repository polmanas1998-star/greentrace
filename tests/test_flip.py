"""The flip on the real trap cases, fed with the same perfect reading as tests/test_rules.py."""
import dataclasses
import json
from pathlib import Path

import pytest

from app.canonical import canonical_json, sha256_of
from app.explain import flip
from app.explain.flip import NO_SINGLE_CHANGE, NOTHING_AGAINST, NOTHING_TO_CHANGE, apply, compute_flip
from app.rules import CONTRADICTED, PROVEN, UNVERIFIABLE, decide
from app.world import geo
from app.world.cases import CASES, build_case
from tests.test_rules import evidence


def changes(result):
    return [(t["change"]["entity"], t["change"]["field"], t["verdict"]) for t in result["tried"]]


# ---- the demo case and the fixed order ----------------------------------------------------

def test_case_2_flips_on_the_date_of_alert_a221():
    result = compute_flip(evidence(2))
    assert (result["verdict"], result["first_flip"]) == (CONTRADICTED, 0)
    assert result["tried"][0] == {
        "change": {"entity": "alert:A-221", "field": "alert_date", "from": "2022-03-14", "to": "2020-12-31"},
        "verdict": PROVEN,
    }
    assert result["sentence"] == "The claim would be PROVEN if alert A-221 were dated 2020-12-31."


def test_candidates_come_in_the_fixed_order():
    # Guarded on purpose: swap two kinds of change and the flip of case 2 changes, so this goes red.
    assert changes(compute_flip(evidence(2))) == [
        ("alert:A-221", "alert_date", PROVEN),
        ("alert:A-221", "confidence", UNVERIFIABLE),
        ("plot:P-014", "tonnes_after_2020", PROVEN),
        ("plot:P-014", "position", PROVEN),
    ]


def test_two_contradictions_no_single_change_and_a_stable_order():
    e = evidence(2)
    second_alert = next(a for a in build_case(19)["alerts"] if a["alert_id"] == "A-225")  # inside P-030
    e = dataclasses.replace(e, alerts=e.alerts + [second_alert])
    result = compute_flip(e)
    assert (result["verdict"], result["first_flip"], result["sentence"]) == (CONTRADICTED, None, NO_SINGLE_CHANGE)
    assert [(entity, field) for entity, field, _ in changes(result)] == [
        ("alert:A-221", "alert_date"), ("alert:A-225", "alert_date"),
        ("alert:A-221", "confidence"), ("alert:A-225", "confidence"),
        ("plot:P-014", "tonnes_after_2020"), ("plot:P-030", "tonnes_after_2020"),
        ("plot:P-014", "position"), ("plot:P-030", "position"),
    ]
    assert {verdict for _, _, verdict in changes(result)} == {CONTRADICTED}


# ---- the other cases the user named -----------------------------------------------------------

def test_case_1_proven_has_nothing_standing_against_it():
    result = compute_flip(evidence(1))
    assert result == {"verdict": PROVEN, "tried": [], "first_flip": None, "sentence": NOTHING_AGAINST}


def test_case_14_a_low_confidence_alert_is_not_lowered_again():
    result = compute_flip(evidence(14))
    assert result["verdict"] == UNVERIFIABLE
    assert [field for _, field, _ in changes(result)] == ["alert_date", "tonnes_after_2020", "position"]
    assert result["sentence"] == "The claim would be PROVEN if alert A-221 were dated 2020-12-31."


def test_case_8_leaves_out_the_volume_no_plot_traces():
    world = build_case(8)
    untraced = round(sum(d["tonnes"] for d in world["deliveries"]
                         if d["supplier_id"] == "F-031" and d["year"] == world["company"][0]["fiscal_year"]), 6)
    result = compute_flip(evidence(8))
    assert result["tried"] == [{"change": {"entity": "supplier:F-031", "field": "untraced_tonnes",
                                           "from": untraced, "to": 0}, "verdict": PROVEN}]
    assert result["sentence"] == (f"The claim would be PROVEN if the {untraced:g} t from supplier F-031 "
                                  "that no plot traces were left out of the declared volume.")


def test_case_10_nothing_single_is_enough():
    result = compute_flip(evidence(10))
    assert (result["verdict"], result["first_flip"], result["sentence"]) == (UNVERIFIABLE, None, NO_SINGLE_CHANGE)


def test_an_admission_holds_no_value_to_change():
    assert compute_flip(evidence(18))["sentence"] == NOTHING_TO_CHANGE


# ---- what verify.py relies on -----------------------------------------------------------------

def test_same_evidence_same_flip():
    first, second = compute_flip(evidence(2)), compute_flip(evidence(2))
    assert canonical_json(first) == canonical_json(second)
    assert sha256_of(first) == sha256_of(second)


@pytest.mark.parametrize("number", [2, 6, 8, 9, 13, 14, 19])
def test_replaying_each_change_from_json_gives_the_recorded_verdict(number):
    e = evidence(number)
    result = json.loads(canonical_json(compute_flip(e)))  # what a receipt holds
    for tried in result["tried"]:
        assert decide(apply(e, tried["change"]))["verdict"] == tried["verdict"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.number:02d}_{c.slug}")
def test_every_trap_case_gets_a_flip_or_says_why(case):
    e = evidence(case.number)
    before = canonical_json(dataclasses.asdict(e))
    result = compute_flip(e)
    assert canonical_json(dataclasses.asdict(e)) == before  # the input is never modified
    assert result["verdict"] == decide(e)["verdict"]
    flips = [i for i, t in enumerate(result["tried"]) if t["verdict"] != result["verdict"]]
    assert result["first_flip"] == (flips[0] if flips else None)
    assert result["sentence"].endswith(".")


def test_moving_a_plot_takes_it_600_m_away_from_the_alert():
    e = evidence(2)
    move = next(t["change"] for t in compute_flip(e)["tried"] if t["change"]["field"] == "position")
    (lat0, lon0), (lat1, lon1) = move["from"], move["to"]
    alert = next(a for a in e.alerts if a["alert_id"] == "A-221")
    assert geo.distance_m(lat0, lon0, lat1, lon1) == pytest.approx(600.0, abs=1.0)
    assert (geo.distance_m(lat1, lon1, alert["lat"], alert["lon"])
            > geo.distance_m(lat0, lon0, alert["lat"], alert["lon"]) + 590.0)
    moved = next(p for p in apply(e, move).plots if p["plot_id"] == "P-014")
    assert (moved["lat"], moved["lon"]) == (lat1, lon1)


def test_an_unknown_change_is_refused():
    with pytest.raises(ValueError, match="not a change"):
        apply(evidence(2), {"entity": "alert:A-221", "field": "area_ha", "from": 0.8, "to": 0.0})


def test_the_flip_never_reaches_a_model_or_the_network():
    source = Path(flip.__file__).read_text(encoding="utf-8")
    for forbidden in ("app.adapters", "openai", "requests", "socket", "urllib", "random", "datetime", "time"):
        assert f"import {forbidden}" not in source and f"from {forbidden}" not in source, forbidden
