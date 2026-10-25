"""The blind set: its change language fails closed, its freeze holds, and the lesson it taught stays learnt."""
import json

import pytest

from app.agents import extractor
from scripts import blind_set


def test_the_brief_shows_the_world_and_never_the_rules():
    brief = blind_set.brief()
    assert "| P-029 |" in brief and "ngo_2025" in brief and "## The change language" in brief
    assert "rules.py" not in brief and "R1" not in brief and "trap case" not in brief.lower()


def test_an_unknown_change_stops_the_case_instead_of_being_skipped():
    with pytest.raises(ValueError, match="unknown change"):
        blind_set.build({"id": "X", "changes": [{"op": "delete_the_evidence"}], "expected": "PROVEN"})


def test_a_change_on_a_plot_that_does_not_exist_stops_the_case():
    with pytest.raises(ValueError, match="unknown plot"):
        blind_set.build({"id": "X", "changes": [{"op": "declare_by_point", "plot_id": "P-999"}],
                         "expected": "PROVEN"})


def test_the_committed_cases_still_match_their_frozen_hash():
    assert blind_set.FROZEN.read_text(encoding="utf-8").split()[0] == blind_set._digest()
    assert len(json.loads(blind_set.CASES_FILE.read_text(encoding="utf-8"))) == 10


def test_run_refuses_cases_that_changed_after_the_freeze(tmp_path, monkeypatch):
    cases = tmp_path / "cases.json"
    cases.write_text(blind_set.CASES_FILE.read_text(encoding="utf-8").replace("B-01", "B-99"), encoding="utf-8")
    monkeypatch.setattr(blind_set, "CASES_FILE", cases)
    monkeypatch.setattr(blind_set, "OUT", tmp_path / "out.json")
    assert blind_set.run(use_model=False) == 2 and not (tmp_path / "out.json").exists()


# Lesson of blind case B-09: an NGO note named the supplier by its registry id, the model put "F-031"
# in supplier_name, no supplier is named "F-031", and the mention was dropped: PROVEN instead of
# UNVERIFIABLE. The id is now read by code from the checked quote, as the rule reader already did.

def test_a_mention_that_names_the_supplier_by_its_id_keeps_that_id():
    mention = {"doc": "ngo_2025", "paragraph_no": 9, "supplier_name": "F-031", "cooperative_group": None,
               "year": 2025, "quote": "plot P-029, which is registered to supplier F-031"}
    assert extractor._with_supplier_id(mention)["supplier_id"] == "F-031"


def test_a_mention_by_name_is_left_as_the_model_read_it():
    mention = {"doc": "ngo_2025", "paragraph_no": 9, "supplier_name": "Agnibi", "cooperative_group": None,
               "year": 2022, "quote": "members of the cooperative known as Agnibi"}
    assert extractor._with_supplier_id(mention) == mention
