"""The whole investigation in sample mode: same input, same verdict, same seal."""
import io
import json

import pytest

from app.memory.fallback import MemoryEngine
from app.pipeline import run_case
from scripts.bench import BASELINE, run_all

CLAIM = "100% of our cocoa comes from deforestation-free farms since 2021."


def run(case=2, claim=CLAIM, run_id="test-run", tmp_path=None):
    return run_case({"run_id": run_id, "input": {"claim": claim}, "options": {"sample_mode": True, "case": case}},
                    log_dir=tmp_path, stream=io.StringIO(), memory_engine=MemoryEngine())


def actions(tmp_path, run_id="test-run"):
    return [json.loads(line)["action"] for line in (tmp_path / f"{run_id}.jsonl").read_text(encoding="utf-8").splitlines()]


def test_demo_case_is_contradicted_with_its_evidence(tmp_path):
    out = run(tmp_path=tmp_path)
    assert out["verdict"] == "CONTRADICTED" and out["mode"] == "sample" and out["degraded"]
    finding = out["findings"][0]
    assert finding["evidence"] == [{"source": "alert", "id": "A-221"}, {"source": "plot", "id": "P-014"}]
    assert [t["id"] for t in finding["refutations_tried"]] == ["R3", "R4", "R5"]


def test_the_evaluator_sends_the_plan_back_before_approving(tmp_path):
    steps = actions(tmp_path) if run(tmp_path=tmp_path) else []
    loop = ["propose_refutation", "request_revision", "revise_plan"]
    assert steps[steps.index("propose_refutation"):steps.index("propose_refutation") + 3] == loop
    assert steps.index("revise_plan") < steps.index("approve") < steps.index("write_report")


def test_two_runs_give_the_same_seal(tmp_path):
    first, second = run(run_id="one", tmp_path=tmp_path), run(run_id="two", tmp_path=tmp_path)
    assert first["verdict_sha256"] == second["verdict_sha256"]
    assert first["fingerprints"]["data_sha256"] == second["fingerprints"]["data_sha256"]


def test_a_vague_promise_is_refused_before_any_query(tmp_path):
    out = run(claim="We are a sustainable company.", tmp_path=tmp_path)
    assert out["status"] == "too_vague" and "query_zetaris" not in actions(tmp_path)


@pytest.mark.parametrize("bad", [{"input": {"claim": ""}}, {"input": {}}, {"input": {"claim": "x"}, "options": []},
                                 {"run_id": "../x", "input": {"claim": "x"}}])
def test_malformed_requests_get_a_clear_error(bad):
    with pytest.raises(ValueError):
        run_case(bad, stream=io.StringIO(), memory_engine=MemoryEngine())


def test_bench_matches_the_committed_baseline():
    """Any change in a sample-mode verdict must be deliberate: rerun scripts.bench --save."""
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert [(r["case"], r["got"]) for r in run_all()] == [(r["case"], r["got"]) for r in baseline["results"]]
