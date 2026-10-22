import os

import pytest

from app.queries import TEMPLATES, DataGate, SourceUnavailable, SQLiteEngine, ZetarisEngine, render
from app.world.cases import build_case
from app.world.store import to_sqlite


def gate_for(case_number, lines=None):
    world = build_case(case_number)
    engine = SQLiteEngine(to_sqlite(world), tuple(world["meta"]["unavailable_sources"]))
    return DataGate(engine, run_id="test-run", log=(lines.append if lines is not None else lambda _: None))


# ---- parameters never reach SQL unchecked --------------------------------------------

@pytest.mark.parametrize("bad", ["x' OR '1'='1", "P-014; DROP TABLE plots", "a\\b", "", "x" * 81, True])
def test_unsafe_parameters_are_refused(bad):
    with pytest.raises(ValueError):
        render("Q5", "", plot_id=bad)


def test_lists_and_numbers_are_rendered():
    sql = render("Q3", "", supplier_ids=["F-007", "F-019"])
    assert "IN ('F-007', 'F-019')" in sql
    assert "BETWEEN 6.8 AND 6.9" in render("Q4", "", lat_min=6.8, lat_max=6.9, lon_min=-6.6, lon_max=-6.5)


def test_every_template_uses_the_table_prefix():
    for query_id, template in TEMPLATES.items():
        assert "{t}" in template, query_id


# ---- each query answers what the cases need ------------------------------------------

def test_q1_reads_main_text_before_the_annex():
    rows = gate_for(1).ask("Extractor", "Q1", doc_id="rse_2025").rows
    assert [r["section"] for r in rows] == ["main"] * 9 + ["annex"] * 3


def test_q2_gives_suppliers_and_declared_total():
    rows = gate_for(1).ask("Investigator", "Q2", company_id="chocodurable").rows
    assert len(rows) == 12 and rows[0]["total_tonnes"] > 0


def test_q4_finds_the_case_2_alert_around_p014():
    gate = gate_for(2)
    p014 = next(r for r in gate.ask("Investigator", "Q3", supplier_ids=["F-007"]).rows if r["plot_id"] == "P-014")
    box = dict(lat_min=p014["lat"] - 0.01, lat_max=p014["lat"] + 0.01,
               lon_min=p014["lon"] - 0.01, lon_max=p014["lon"] + 0.01)
    assert [r["alert_id"] for r in gate.ask("Investigator", "Q4", **box).rows] == ["A-221"]


def test_q5_shows_p014_still_delivering_in_2022():
    rows = gate_for(2).ask("Evaluator", "Q5", plot_id="P-014").rows
    assert {r["year"]: r["tonnes"] for r in rows}[2022] == 2.4


def test_q7_finds_the_plot_declared_twice():
    rows = gate_for(10).ask("Investigator", "Q7", company_id="chocodurable").rows
    assert rows == [{"plot_id": "P-022", "supplier_id": "F-002"}, {"plot_id": "P-022", "supplier_id": "F-019"}]


def test_q8_finds_both_namesakes():
    rows = gate_for(15).ask("Evaluator", "Q8", name="Agnibi").rows
    assert [r["supplier_id"] for r in rows] == ["F-007", "F-044"]


def test_q9_reads_one_alert():
    assert gate_for(14).ask("Evaluator", "Q9", alert_id="A-221").rows[0]["confidence"] == "low"


def test_q0_reports_the_swapped_coordinates():
    rows = gate_for(16).ask("Investigator", "Q0").rows
    assert rows == [{"rule": "in_cocoa_belt", "table": "plots", "entity_id": "P-027"}]


# ---- answers are kept, fingerprinted and traced -----------------------------------

def test_same_question_same_fingerprint():
    first = gate_for(2).ask("Investigator", "Q6", company_id="chocodurable", year_from=2021, year_to=2025)
    again = gate_for(2).ask("Investigator", "Q6", company_id="chocodurable", year_from=2021, year_to=2025)
    assert first.rows_sha256 == again.rows_sha256 and len(first.rows) == 200


def test_success_writes_one_complete_trace_line():
    lines = []
    gate_for(1, lines).ask("Investigator", "Q5", plot_id="P-014")
    assert len(lines) == 1
    assert lines[0]["action"] == "query_zetaris" and lines[0]["rows"] == 5 and lines[0]["provider"] == "sqlite"


def test_source_down_retries_once_then_gives_up():
    lines, gate = [], None
    gate = gate_for(20, lines)
    with pytest.raises(SourceUnavailable):
        gate.ask("Investigator", "Q4", lat_min=6.7, lat_max=7.0, lon_min=-6.7, lon_max=-6.3)
    assert [(l["action"], l["retry_count"]) for l in lines] == [("retry", 0), ("error", 1)]
    assert gate.answers == []


def test_source_down_leaves_other_sources_working():
    assert gate_for(20).ask("Investigator", "Q5", plot_id="P-014").rows


# ---- live: Zetaris must answer exactly like SQLite (needs Zetaris loaded with case 2) --

LIVE = os.environ.get("GT_LIVE_TESTS") == "1"


@pytest.mark.skipif(not LIVE, reason="set GT_LIVE_TESTS=1 with Zetaris running and case 2 loaded")
def test_zetaris_and_sqlite_give_the_same_rows():
    from dotenv import load_dotenv

    from app.adapters.zetaris import Zetaris

    load_dotenv()
    live = DataGate(ZetarisEngine(Zetaris.from_env()), run_id="live-check", log=lambda _: None)
    sample = gate_for(2)
    questions = [("Q1", dict(doc_id="ngo_2025")), ("Q2", dict(company_id="chocodurable")),
                 ("Q3", dict(supplier_ids=["F-007", "F-031"])),
                 ("Q4", dict(lat_min=6.7, lat_max=7.0, lon_min=-6.7, lon_max=-6.3)),
                 ("Q5", dict(plot_id="P-014")),
                 ("Q6", dict(company_id="chocodurable", year_from=2021, year_to=2025)),
                 ("Q7", dict(company_id="chocodurable")),
                 ("Q8", dict(name="Agnibi")), ("Q9", dict(alert_id="A-221")), ("Q0", {})]
    for query_id, params in questions:
        assert live.ask("Investigator", query_id, **params).rows == sample.ask("Investigator", query_id, **params).rows, query_id
