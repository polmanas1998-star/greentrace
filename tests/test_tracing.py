import io
import json

import pytest

from app.pipeline import run_case
from app.tracing import FIELDS, trace

OFFICIAL = {
    "timestamp", "run_id", "agent_name", "action", "input_summary", "output_summary",
    "target_agent", "confidence", "retry_count", "status",
}


def record(**overrides):
    base = {field: None for field in FIELDS}
    base.update(run_id="test-run", agent_name="Evaluator", action="check", retry_count=0, status="ok")
    base.update(overrides)
    return base


def test_fields_cover_official_list_plus_ours():
    # Written out by hand on purpose: removing a field from FIELDS must turn this red.
    assert set(FIELDS) == OFFICIAL | {"provider", "model", "rows"}


def test_valid_line_written_to_file_and_stream(tmp_path):
    stream = io.StringIO()
    trace(record(), log_dir=tmp_path, stream=stream)
    on_disk = (tmp_path / "test-run.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(on_disk) == 1
    assert set(json.loads(on_disk[0])) == set(FIELDS)
    assert stream.getvalue().strip() == on_disk[0]


def test_missing_field_raises(tmp_path):
    bad = record()
    del bad["rows"]
    with pytest.raises(ValueError, match="rows"):
        trace(bad, log_dir=tmp_path, stream=io.StringIO())
    assert not (tmp_path / "test-run.jsonl").exists()


def test_unknown_field_raises(tmp_path):
    with pytest.raises(ValueError, match="secret"):
        trace(record(secret="x"), log_dir=tmp_path, stream=io.StringIO())


@pytest.mark.parametrize("run_id", ["../escape", "a/b", "", "x" * 65, None])
def test_unsafe_run_id_refused(tmp_path, run_id):
    with pytest.raises(ValueError):
        trace(record(run_id=run_id), log_dir=tmp_path, stream=io.StringIO())


def test_every_line_of_a_real_run_is_complete(tmp_path):
    from app.memory.fallback import MemoryEngine

    result = run_case({"input": {"claim": "100% of our cocoa is deforestation-free since 2021."},
                       "options": {"sample_mode": True, "case": 2}},
                      log_dir=tmp_path, stream=io.StringIO(), memory_engine=MemoryEngine())
    lines = (tmp_path / f"{result['run_id']}.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) > 20
    assert all(set(json.loads(line)) == set(FIELDS) for line in lines)


def test_run_case_refuses_non_object(tmp_path):
    with pytest.raises(ValueError):
        run_case(["not", "an", "object"], log_dir=tmp_path, stream=io.StringIO())
