"""The court of inquiry: Plaintiff, Defense (the Evaluator) and Judge (the rules) on a finished run.

Its one invariant: the court never changes the verdict. The model only appears here through a
fake client (tests only), to show what the code does with what a model writes.
"""
import io
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import orchestrator
from app.adapters.llm import ModelGate
from app.api import app
from app.checks.landcover import CELLS_FILE
from app.court import pieces as P
from app.court.defense import attack_log
from app.court.judge import hold_court
from app.memory.fallback import MemoryEngine
from app.pipeline import run_case
from app.receipt import build_receipt
from app.tracing import trace
from app.world.cases import CASES, DEFAULT_CLAIM

EXAMPLE_05 = "examples/05_real_plot_contradicted.json"
NOT_ONE_HECTARE = "Not one hectare of forest has been cleared to grow the cocoa we buy."
CONFIDENCE = ("Alerts are read from public deforestation alert systems; only alerts of high or highest confidence are "
              "treated as confirmed.")


def investigate(case, run_id, tmp_path, request=None):
    request = request or {"input": {"claim": DEFAULT_CLAIM}, "options": {"sample_mode": True, "case": case}}
    out = run_case({**request, "run_id": run_id}, log_dir=tmp_path, stream=io.StringIO(), memory_engine=MemoryEngine())
    return out, orchestrator.RUNS[run_id]


def lines(tmp_path, run_id):
    return [json.loads(line) for line in (tmp_path / f"{run_id}.jsonl").read_text(encoding="utf-8").splitlines()]


def without_run_id(court):
    return json.dumps({**court, "run_id": None}, sort_keys=True)


# ---- the invariant -------------------------------------------------------------------------------

@pytest.mark.parametrize("case", [c.number for c in CASES])
def test_the_court_never_changes_the_verdict_and_gives_the_same_json_twice(case, tmp_path):
    out, run = investigate(case, f"court-{case:02d}", tmp_path)
    before = (out["verdict"], out["verdict_sha256"], build_receipt(run)["verdict"], len(run.ctx.gate.answers))
    court = hold_court(run)
    assert court["verdict"] == out["verdict"]
    assert (run.output["verdict"], run.output["verdict_sha256"], build_receipt(run)["verdict"],
            len(run.ctx.gate.answers)) == before  # no verdict moved, no query asked again
    assert court["provider"] == "rules" and 1 <= len(court["rounds"]) <= 3
    _, again = investigate(case, f"court-{case:02d}-again", tmp_path)
    assert without_run_id(hold_court(again)) == without_run_id(court)


def test_every_citation_of_the_21_courts_is_admitted_by_the_code(tmp_path):
    for case in CASES:
        _, run = investigate(case.number, f"cite-{case.number:02d}", tmp_path)
        pieces = P.pieces_of(run.ctx)
        for round_ in hold_court(run)["rounds"]:
            for argument in round_["plaintiff"]:
                assert argument["cites"] and argument["status"] != "withdrawn"
                assert [P.struck(pieces, cite) for cite in argument["cites"]] == [None] * len(argument["cites"])


# ---- the debate on the demo case -----------------------------------------------------------------

def test_the_demo_case_is_argued_part_by_part_then_answered_with_the_evaluators_own_queries(tmp_path):
    _, run = investigate(2, "court-demo", tmp_path)
    court = hold_court(run)
    first, second, third = court["rounds"]
    assert [a["status"] for a in first["plaintiff"]] == ["standing", "refuted", "standing", "standing"]
    assert first["plaintiff"][1]["cites"][0] == {"source": "csr_report", "ref": "§2", "quote": NOT_ONE_HECTARE}
    (d1,) = first["defense"]
    assert (d1["answers"], d1["refutation"], d1["query_id"], d1["holds"]) == ("P2", None, "Q4", True)
    assert d1["rows"] == [r for r in d1["rows"] if r["alert_id"] == "A-221"] and d1["sql"].startswith("SELECT alert_id")
    assert first["judge"] == {"rule": "rule 2", "standing": ["P1", "P3", "P4"], "refuted": ["P2"],
                              "finding": "CONTRADICTED by rule 2: alert A-221 (2022-03-14, high) lies inside P-014, "
                                         "which delivered in 2022 or later. P2 refuted by D1; P1, P3, P4 stand."}
    (p5,), (d2,) = second["plaintiff"], second["defense"]
    assert p5["answers"] == "D1" and "suspends deliveries" in p5["cites"][0]["quote"]
    assert (d2["answers"], d2["refutation"], d2["query_id"], d2["holds"]) == ("P5", "R3", "Q5", True)
    assert [r["year"] for r in d2["rows"]] == [2021, 2022, 2023, 2024, 2025]
    (p6,), (d3,) = third["plaintiff"], third["defense"]
    assert (p6["answers"], p6["cites"][0]["quote"]) == ("D2", CONFIDENCE)
    assert (d3["refutation"], d3["query_id"], d3["rows"][0]["confidence"], d3["holds"]) == ("R4", "Q9", "high", True)
    assert court["judgment"].startswith("The court upholds CONTRADICTED by rule 2: alert A-221")


def test_a_report_passage_against_a_fact_read_from_the_data_is_tested_by_the_rules(tmp_path):
    _, run = investigate(7, "court-point", tmp_path)  # 6 ha plot declared by a point
    _, second = hold_court(run)["rounds"]
    (answer,), (counter,) = second["plaintiff"], second["defense"]
    assert answer["cites"][0]["ref"] == "annex §1" and answer["status"] == "refuted"
    assert (counter["refutation"], counter["query_id"], counter["rows"][0]["area_ha"]) == (None, "Q3", 6.0)
    assert counter["detail"] == "rule 1 still finds: P-021 covers 6.0 ha but is declared by a point"


def test_a_source_that_did_not_answer_is_said_with_no_sql(tmp_path):
    _, run = investigate(20, "court-down", tmp_path)
    (counter,) = hold_court(run)["rounds"][0]["defense"]
    assert (counter["query_id"], counter["sql"], counter["rows"], counter["holds"]) == ("Q4", None, [], True)


def test_every_intervention_is_one_trace_line_and_a_second_request_writes_none(tmp_path):
    _, run = investigate(2, "court-trace", tmp_path)
    court = hold_court(run)
    written = lines(tmp_path, "court-trace")
    court_lines = [line for line in written if line["agent_name"] in ("Plaintiff", "Judge")
                   or line["action"] in ("counter_argument", "write_judgment")]
    said = [a["id"] for r in court["rounds"] for a in r["plaintiff"]]
    countered = [d["id"] for r in court["rounds"] for d in r["defense"]]
    assert [line["output_summary"].split(":")[0] for line in court_lines if line["agent_name"] == "Plaintiff"] == said
    assert [line["output_summary"].split(":")[0] for line in court_lines
            if line["action"] == "counter_argument"] == countered
    assert all(line["agent_name"] == "Evaluator" for line in court_lines if line["action"] == "counter_argument")
    assert [line["target_agent"] for line in court_lines if line["action"] == "judge_round"] == [
        "Plaintiff", "Plaintiff", "Writer"]
    assert all(line["target_agent"] for line in court_lines)
    assert hold_court(run) is court and len(lines(tmp_path, "court-trace")) == len(written)


def test_a_what_if_calls_the_court_again_on_the_changed_run(tmp_path):
    _, run = investigate(2, "court-wi", tmp_path)
    assert hold_court(run)["verdict"] == "CONTRADICTED"
    orchestrator.what_if("court-wi", "alert:A-221", "alert_date", "2019-03-14")
    court = hold_court(run)
    assert court["verdict"] == run.output["verdict"] == "PROVEN"
    assert [a["status"] for a in court["rounds"][0]["plaintiff"]] == ["standing"] * 4


@pytest.mark.skipif(not CELLS_FILE.exists(), reason="data/reference_cells.csv.gz not built")
def test_a_drawn_plot_is_argued_against_the_reference_maps(tmp_path):
    request = json.loads(open(EXAMPLE_05, encoding="utf-8").read())
    request["options"] = {**request["options"], "sample_mode": True}
    _, run = investigate(None, "court-drawn", tmp_path, request)
    court = hold_court(run)
    assert court["verdict"] == "CONTRADICTED"
    assert [(d["refutation"], d["query_id"]) for r in court["rounds"] for d in r["defense"]] == [
        (None, "Q10"), ("R3", "Q10"), ("R9", "Q10")]
    assert all(row["loss_year"] > 2020 for row in court["rounds"][2]["defense"][0]["rows"])
    assert court["rounds"][2]["plaintiff"][0]["cites"] == [
        {"source": "declared_plot", "ref": "plot:P-042", "quote": "area_ha: 10.0"}]
    assert [(a["refutation"], a["query_id"], a["result"], a["retry_count"]) for a in attack_log(run)["attacks"]] == [
        ("R3", "Q10", "evidence_held", 1), ("R6", None, "evidence_held", 0), ("R9", "Q10", "evidence_held", 0)]


# ---- the attack log ---------------------------------------------------------------------------

def test_the_attack_log_lists_every_refutation_with_its_query_and_the_plan_revision(tmp_path):
    _, run = investigate(2, "court-attacks", tmp_path)
    log = attack_log(run)
    assert log["run_id"] == "court-attacks"
    assert [(a["refutation"], a["query_id"], a["rows"], a["retry_count"]) for a in log["attacks"]] == [
        ("R3", "Q5", 5, 1), ("R1", "Q4", 1, 0), ("R4", "Q9", 1, 0), ("R5", "Q9", 1, 0), ("R6", None, 0, 0),
        ("R8", "Q6", 200, 0)]
    assert {a["result"] for a in log["attacks"]} == {"evidence_held"}
    assert log["attacks"][0]["question"] == "E-01: had P-014 stopped delivering before the alert A-221?"


def test_a_refutation_that_held_is_refuted_evidence_and_the_answer_stands(tmp_path):
    _, run = investigate(2, "court-held", tmp_path)
    review = run.reviews["E-01"]
    review["tried"] = [dict(t, holds=True) if t["id"] == "R3" else t for t in review["tried"]]  # as if R3 broke it
    review["decision"] = "block"
    assert attack_log(run)["attacks"][0]["result"] == "refuted_evidence"
    court = hold_court(run)
    assert court["verdict"] == "CONTRADICTED"  # the rules still decide
    assert court["rounds"][0]["defense"][0]["holds"] is False and len(court["rounds"]) == 1


def test_an_attack_with_no_answer_in_the_run_is_source_unavailable(tmp_path):
    _, run = investigate(2, "court-gone", tmp_path)
    run.ctx.gate.answers[:] = [a for a in run.ctx.gate.answers if not (a.agent == "Evaluator" and a.query_id == "Q9")]
    results = {a["refutation"]: a["result"] for a in attack_log(run)["attacks"]}
    assert results["R4"] == results["R5"] == "source_unavailable" and results["R1"] == "evidence_held"


def test_the_attack_log_says_when_the_trace_is_gone(tmp_path):
    _, run = investigate(2, "court-notrace", tmp_path)
    (tmp_path / "court-notrace.jsonl").unlink()
    with pytest.raises(FileNotFoundError, match="the attack log is computed from it"):
        attack_log(run)


# ---- citations: the code admits or strikes them -------------------------------------------------

def test_a_citation_must_name_a_piece_of_this_run_word_for_word(tmp_path):
    _, run = investigate(2, "court-cite", tmp_path)
    pieces = P.pieces_of(run.ctx)
    assert P.struck(pieces, {"source": "csr_report", "ref": "§2", "quote": NOT_ONE_HECTARE}) is None
    assert P.struck(pieces, {"source": "csr_report", "ref": "§2", "quote": "  Not one hectare\nof forest "}) is None
    assert "not word for word" in P.struck(pieces, {"source": "csr_report", "ref": "§2", "quote": "No forest was cut"})
    assert "no paragraph" in P.struck(pieces, {"source": "csr_report", "ref": "§99", "quote": NOT_ONE_HECTARE})
    assert "two words" in P.struck(pieces, {"source": "csr_report", "ref": "§2", "quote": "Not"})
    assert P.struck(pieces, {"source": "alerts", "ref": "alert:A-221", "quote": "confidence: high"}) is None
    assert P.struck(pieces, {"source": "registry", "ref": "plot:P-014", "quote": "area_ha: 3.2"}) is None
    assert "no value" in P.struck(pieces, {"source": "alerts", "ref": "alert:A-221", "quote": "confidence: low"})
    assert "was read" in P.struck(pieces, {"source": "alerts", "ref": "alert:A-999", "quote": None})
    assert "unknown source" in P.struck(pieces, {"source": "press", "ref": "x", "quote": "a b"})


# ---- with a model: a fake client, in this test file only ----------------------------------------

class FakeClient:
    def __init__(self, *answers):
        self.answers, self.calls = [json.dumps(a) for a in answers], []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.calls.append(request)
        message = SimpleNamespace(content=self.answers.pop(0))
        return SimpleNamespace(model=request["model"], choices=[SimpleNamespace(message=message)], usage=None)


def with_model(run, tmp_path, *answers):
    fake = FakeClient(*answers)
    run.ctx.model = ModelGate(run.ctx.run_id, env={"MODEL_PROVIDER": "nvidia", "MODEL_API_KEY": "nvapi-test"},
                              client_factory=lambda provider: fake,
                              log=lambda record: trace(record, log_dir=tmp_path, stream=io.StringIO()))
    return fake


def test_the_model_plaintiff_is_held_to_its_citations(tmp_path):
    _, run = investigate(2, "court-model", tmp_path)
    fake = with_model(run, tmp_path, {"arguments": [
        {"part": "no_deforestation", "claim": "Nothing was cleared.",
         "cites": [{"source": "csr_report", "ref": "§2", "quote": NOT_ONE_HECTARE}]},
        {"part": "scope", "claim": "All cocoa is covered.",
         "cites": [{"source": "csr_report", "ref": "§2", "quote": "all of our cocoa, without exception"}]},
    ]}, {"answers": [
        {"counter": "D1", "angle": "R4", "claim": "A-221 is a weak alert.",
         "cites": [{"source": "csr_report", "ref": "annex §3", "quote": CONFIDENCE}]},
    ]}, {"answers": [
        {"counter": "D2", "angle": "R5", "claim": "A-221 is older than the cut-off.",
         "cites": [{"source": "csr_report", "ref": "annex §3", "quote": CONFIDENCE}]},
    ]}, {"judgment": "The court upholds CONTRADICTED by rule 2: alert A-221 lies inside P-014."})
    court = hold_court(run)
    first, second, third = court["rounds"]
    by_part = {a["id"]: a["status"] for a in first["plaintiff"]}
    assert by_part == {"P1": "standing", "P2": "refuted", "P3": "standing", "P4": "withdrawn"}
    assert first["plaintiff"][0]["claim"].startswith("Every tonne")  # coverage, left out by the model: the rules
    assert "P4 withdrawn: the quote is not word for word in csr_report §2" in first["judge"]["finding"]
    assert (second["plaintiff"][0]["status"], second["defense"][0]["refutation"]) == ("refuted", "R4")
    assert third["plaintiff"][0]["status"] == "withdrawn" and third["defense"] == []
    assert "cites no piece that was not cited already" in third["judge"]["finding"]
    assert court["verdict"] == "CONTRADICTED" and court["provider"] == "nvidia"
    assert court["judgment"] == "The court upholds CONTRADICTED by rule 2: alert A-221 lies inside P-014."
    assert [call["model"] for call in fake.calls] == ["nvidia/nemotron-3.5-lightning-30b-a3b"] * 4  # the fast lane
    plaintiff_calls = [line for line in lines(tmp_path, "court-model")
                       if line["agent_name"] == "Plaintiff" and line["action"] == "model_call"]
    assert len(plaintiff_calls) == 3 and {line["provider"] for line in plaintiff_calls} == {"nvidia"}


@pytest.mark.parametrize("judgment, why", [
    ("The court finds the promise PROVEN after all.", "does not state the verdict CONTRADICTED"),
    ("CONTRADICTED by rule 2, though nothing is PROVEN either.", "names another verdict (PROVEN)"),
    ("CONTRADICTED by rule 2: 7.5 ha were cleared inside P-014.", "numbers that are not in the record (7.5)"),
])
def test_a_judgment_the_code_review_refuses_gives_way_to_the_template(judgment, why, tmp_path):
    _, run = investigate(2, f"court-judgment-{len(why)}", tmp_path)
    with_model(run, tmp_path, {"arguments": []}, {"answers": []}, {"answers": []}, {"judgment": judgment})
    court = hold_court(run)
    assert court["judgment"].startswith("The court upholds CONTRADICTED by rule 2")
    refused = [line for line in lines(tmp_path, run.ctx.run_id) if line["action"] == "review_judgment"]
    assert len(refused) == 1 and why in refused[0]["output_summary"]


# ---- the routes ---------------------------------------------------------------------------------

def test_the_routes_answer_from_the_finished_run():
    client = TestClient(app)
    run_id = client.post("/run", json={"run_id": "court-api", "input": {"claim": DEFAULT_CLAIM},
                                       "options": {"sample_mode": True, "case": 2}}).json()["run_id"]
    court = client.get(f"/runs/{run_id}/court").json()
    assert court["run_id"] == run_id and court["verdict"] == "CONTRADICTED"
    assert set(court) == {"run_id", "verdict", "rounds", "judgment", "provider"}
    assert set(court["rounds"][0]) == {"n", "plaintiff", "defense", "judge"}
    attacks = client.get(f"/runs/{run_id}/attacks").json()
    assert attacks["run_id"] == run_id and len(attacks["attacks"]) == 6
    assert set(attacks["attacks"][0]) == {"refutation", "question", "query_id", "result", "rows", "retry_count"}
    assert client.get("/runs/nope/court").status_code == 404
    assert client.get("/runs/nope/attacks").status_code == 404
