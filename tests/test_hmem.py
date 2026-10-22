"""H-MEM, the second Meterless engine: notes between investigations, and memory never decides.

These tests run the real, unmodified H-MEM reference behind meterless/server.ts, with its
clock pinned (HMEM_CLOCK_MS) and GreenTrace's date pinned, on a fresh directory each time:
same runs, same notes. Skipped, and said so, when Node is not installed.
"""
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import time
from datetime import date
from pathlib import Path

import pytest

import verify
from app import orchestrator
from app.memory import hmem as notes
from app.memory.fallback import MemoryEngine
from app.pipeline import run_case
from app.receipt import build_receipt

ROOT = Path(__file__).resolve().parents[1]
METERLESS = ROOT / "meterless"
HMEM_DIR = METERLESS / "hmem"
TSX = METERLESS / "node_modules" / "tsx" / "dist" / "cli.mjs"
NODE = shutil.which("node")
CLAIM = "100% of our cocoa comes from deforestation-free farms since 2021."
TODAY = date(2026, 10, 22)
CLOCK_MS = "1792627200000"  # 2026-10-22T00:00:00Z

needs_node = pytest.mark.skipif(not (NODE and TSX.exists()),
                                reason="Node or meterless/node_modules missing (cd meterless && npm ci)")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(directory: Path):
    port = free_port()
    process = subprocess.Popen([NODE, str(TSX), "server.ts"], cwd=METERLESS, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True,
                               env={**os.environ, "WORLD_MODEL_PORT": str(port), "HMEM_DIR": str(directory),
                                    "HMEM_CLOCK_MS": CLOCK_MS})
    memory = notes.HMem(f"http://127.0.0.1:{port}/hmem", today=lambda: TODAY)
    deadline = time.monotonic() + 30
    while True:
        try:
            memory.call("query", {"query": "ping", "threshold": 1})
            return process, memory
        except notes.HMemUnavailable:
            if process.poll() is not None or time.monotonic() > deadline:
                process.kill()
                pytest.fail(f"Meterless server did not start: {process.stdout.read()}")
            time.sleep(0.1)


@pytest.fixture
def memory(tmp_path):
    process, client = start(tmp_path / "memory")
    yield client
    process.terminate()
    process.wait(timeout=10)


def run(case, run_id, hmem, lines=None):
    stream = io.StringIO()
    out = run_case({"run_id": run_id, "input": {"claim": CLAIM}, "options": {"sample_mode": True, "case": case}},
                   stream=stream, memory_engine=MemoryEngine(), hmem=hmem)
    if lines is not None:
        lines += [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]
    return out


# ---- the engine: unmodified, and conformant ----------------------------------------------------------

def test_the_hmem_reference_is_the_upstream_one_byte_for_byte():
    listed = [line.split("  ", 1) for line in (HMEM_DIR / "UPSTREAM.sha256").read_text().splitlines()]
    assert len(listed) == 20
    for digest, name in listed:
        assert hashlib.sha256((HMEM_DIR / name).read_bytes()).hexdigest() == digest, name


@needs_node
def test_the_meterless_conformance_suite_passes_13_of_13():
    result = subprocess.run([NODE, str(TSX), "hmem/conformance/runner.ts"], cwd=METERLESS, capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0 and "13/13 checks passed" in result.stdout


@needs_node
def test_a_record_without_source_is_rejected_by_the_reference_itself(memory):
    with pytest.raises(notes.HMemUnavailable, match="rejected by construction"):
        memory.call("add", {"content": "a note from nowhere", "source": ""})


# ---- the one use: a note per supplier, read first next time -----------------------------------------------

@needs_node
def test_case_2_leaves_one_note_on_f007_on_disk(memory, tmp_path):
    out = run(2, "hm-first", memory)
    (note,), stale = memory.recall("chocodurable")
    assert stale == []
    assert (note.supplier_id, note.verdict, note.because, note.run_id) == ("F-007", "CONTRADICTED", "A-221", "hm-first")
    assert (note.run_date, note.expires, note.seal) == ("2026-10-22", "2027-01-20", out["verdict_sha256"])
    assert note.findings == ("confirmed_alert_in_delivering_plot|alert:A-221,plot:P-014",)
    assert (tmp_path / "memory" / "state.json").exists() and (tmp_path / "memory" / "ledger.jsonl").exists()


@needs_node
def test_the_next_investigation_reads_memory_before_any_query(memory):
    run(2, "hm-then", memory)
    lines = []
    out = run(2, "hm-now", memory, lines)
    investigator = [line for line in lines if line["agent_name"] == "Investigator"]
    assert investigator[0]["action"] == "recall_memory"
    assert investigator[0]["output_summary"] == "remembered from run hm-then on 2026-10-22: F-007 contradicted by A-221"
    first_query = next(i for i, line in enumerate(lines) if line["action"] == "query_zetaris"
                       and line["agent_name"] == "Investigator")
    assert lines.index(investigator[0]) < first_query
    (seen,) = out["seen_before"]
    assert (seen["supplier_id"], seen["run_id"], seen["changed"]) == ("F-007", "hm-then", "the same finding(s) as then")
    assert "## Seen before" in orchestrator.exports.evidence_markdown(out)


@needs_node
def test_memory_never_decides(memory):
    """Memory says F-007 was contradicted; today's data (case 1) has no alert: PROVEN, same seal as without memory."""
    run(2, "hm-old", memory)
    memory.remember(notes.Note("F-002", "chocodurable", "hm-forged", "2026-10-22", "2027-01-20", "CONTRADICTED",
                               "A-999", "a note that no data supports", ("forged|alert:A-999",), "0" * 64))
    with_memory = run(1, "hm-today", memory)
    without = run(1, "hm-plain", None)
    assert with_memory["verdict"] == without["verdict"] == "PROVEN"
    assert with_memory["verdict_sha256"] == without["verdict_sha256"]
    assert with_memory["fingerprints"]["data_sha256"] == without["fingerprints"]["data_sha256"]
    changes = {s["supplier_id"]: s["changed"] for s in with_memory["seen_before"]}
    gone = "no finding concerns this supplier today"
    assert changes == {"F-002": gone, "F-007": gone}

    receipt = json.loads(json.dumps(build_receipt(orchestrator.RUNS["hm-today"])))
    assert "A-999" not in json.dumps(receipt) and "seen_before" not in receipt
    assert verify.check(receipt)["status"] == "VALID"


@needs_node
def test_a_what_if_leaves_no_note(memory):
    run(2, "hm-wi", memory)
    after = orchestrator.what_if("hm-wi", "alert:A-221", "confidence", "low")  # still a finding on F-007's plot
    assert after["verdict"] == "UNVERIFIABLE"
    remembered, _ = memory.recall("chocodurable")
    assert [(n.run_id, n.verdict) for n in remembered] == [("hm-wi", "CONTRADICTED")]


@needs_node
def test_stale_notes_are_forgotten_and_the_ledger_keeps_why(memory):
    run(2, "hm-stale", memory)
    (note,), _ = memory.recall("chocodurable")
    later = notes.HMem(memory.url, today=lambda: date(2027, 1, 21))
    assert later.recall("chocodurable") == ([], [note.memory_id])
    deleted = [e for e in memory.history(note.memory_id) if e["action"] == "delete"]
    assert deleted[0]["details"] == {"reason": "stale since 2027-01-20"}


@needs_node
def test_memory_is_erasable_and_stays_on_disk_until_then(tmp_path):
    process, memory = start(tmp_path / "memory")
    run(2, "hm-disk", memory)
    process.terminate()
    process.wait(timeout=10)
    process, memory = start(tmp_path / "memory")  # a new server reads the same directory
    try:
        assert [n.run_id for n in memory.recall("chocodurable")[0]] == ["hm-disk"]
        assert memory.forget("chocodurable") == 1
        assert memory.recall("chocodurable") == ([], [])
    finally:
        process.terminate()
        process.wait(timeout=10)


# ---- off, or down: the investigation goes on ---------------------------------------------------------------

def test_off_unless_hmem_url_is_set():
    assert notes.from_env({}) is None
    lines = []
    out = run(2, "hm-off", None, lines)
    assert out["verdict"] == "CONTRADICTED" and "seen_before" not in out
    assert not any(line["action"] in ("recall_memory", "remember_findings") for line in lines)


def test_memory_down_is_said_and_changes_nothing():
    lines = []
    down = notes.HMem("http://127.0.0.1:9/hmem", today=lambda: TODAY)
    out = run(2, "hm-down", down, lines)
    assert (out["verdict"], out["verdict_sha256"]) == (run(2, "hm-up", None)["verdict"],
                                                       run(2, "hm-up2", None)["verdict_sha256"])
    (line,) = [line for line in lines if line["action"] == "recall_memory"]
    assert line["status"] == "degraded" and "investigating without memory" in line["output_summary"]
    assert not any(line["action"] == "remember_findings" for line in lines)


def test_notes_name_every_supplier_a_finding_concerns():
    """Case 8: F-031's volume has no plot; the finding refers to the company, the note goes to F-031."""
    run(8, "hm-case8", None)
    investigation = orchestrator.RUNS["hm-case8"]
    decision = orchestrator.decision_for(investigation.evidence, [])
    (note,) = notes.notes_for(investigation.evidence, decision, "hm-case8", "s" * 64, TODAY)
    assert (note.supplier_id, note.verdict, note.because) == ("F-031", "UNVERIFIABLE", "chocodurable")
