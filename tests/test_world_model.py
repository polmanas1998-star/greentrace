import io
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from app.canonical import canonical_json
from app.memory.fallback import EngineError, MemoryEngine
from app.memory.world_model import (
    HttpEngine, Source, WorldModel, WorldModelUnavailable, connect, entity_id, tick_at,
)
from app.tracing import FIELDS

METERLESS = Path(__file__).resolve().parents[1] / "meterless"
TSX = METERLESS / "node_modules" / "tsx" / "dist" / "cli.mjs"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(
    not (NODE and TSX.exists()), reason="Node or meterless/node_modules missing (cd meterless && npm ci)"
)

INVESTIGATOR = Source("Investigator", "Q4")
EVALUATOR = Source("Evaluator", "Q9")
EXTRACTOR = Source("Extractor", "rse_2025")
PIPELINE = Source("Pipeline")

PLOT, ALERT, SUPPLIER, CARD = (entity_id("plot", "P-014"), entity_id("alert", "A-221"),
                               entity_id("supplier", "F-007"), entity_id("card", "E-01"))


@pytest.fixture(autouse=True)
def logs_in_tmp(tmp_path, monkeypatch):
    """Trace files of these tests go to a temporary folder, never to the repo's logs/."""
    monkeypatch.setattr("app.tracing.LOG_DIR", tmp_path / "logs")


def world(run_id="wm-test", engine=None, tmp_path=None):
    stream = io.StringIO()
    wm = WorldModel(run_id, engine=engine or MemoryEngine(), log_dir=tmp_path, stream=stream)
    return wm, stream


def lines(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def case_2(wm):
    """Kit case 2 in miniature: a confirmed 2022 alert inside a delivering plot."""
    wm.upsert_entity("supplier", "F-007", {"name": "Coop Agnibi"}, Source("Investigator", "Q2"))
    wm.upsert_entity("plot", "P-014", {"supplier": SUPPLIER, "area_ha": 3.2}, Source("Investigator", "Q3"))
    wm.upsert_entity("alert", "A-221", {"alert_date": "2022-03-14", "confidence": "high"}, INVESTIGATOR)
    wm.upsert_entity("card", "E-01", {"claim": "A2"}, INVESTIGATOR)
    inside = wm.assert_fact(PLOT, "alert_inside:A-221", True, source=INVESTIGATOR, confidence=0.9,
                            depends_on=[PLOT, ALERT])
    delivered = wm.assert_fact(PLOT, "delivered_after_cutoff", True, source=Source("Investigator", "Q5"),
                               confidence=0.95, depends_on=[PLOT])
    card = wm.assert_fact(CARD, "contradicts_claim", True, source=EVALUATOR, confidence=0.88,
                          depends_on=[inside["id"], delivered["id"]])
    named = wm.assert_fact(SUPPLIER, "named_in_ngo_report", False, source=EXTRACTOR, confidence=0.7,
                           depends_on=[SUPPLIER])
    return {"inside": inside["id"], "delivered": delivered["id"], "card": card["id"], "named": named["id"]}


def statuses(wm, node_id):
    return {fact["id"]: fact["status"] for fact in wm.view("facts", node_id)}


# ---- invalidation: change one entity, exactly what depends on it goes stale ---------

def test_changed_alert_invalidates_its_facts_and_what_was_built_on_them():
    wm, _ = world()
    ids = case_2(wm)
    wm.upsert_entity("alert", "A-221", {"alert_date": "2019-03-14"}, PIPELINE)  # try to fool it

    stale = wm.invalidate(ALERT, source=PIPELINE)

    # Guarded on purpose: drop the transitive step or the edge write and this goes red.
    assert stale == [ids["inside"], ids["card"]]  # breadth first: the fact, then the card built on it
    assert statuses(wm, PLOT) == {ids["inside"]: "invalidated", ids["delivered"]: "valid"}
    assert statuses(wm, CARD) == {ids["card"]: "invalidated"}
    assert statuses(wm, SUPPLIER) == {ids["named"]: "valid"}


def test_unrelated_change_invalidates_nothing_else():
    wm, _ = world()
    ids = case_2(wm)
    assert wm.invalidate(SUPPLIER, source=PIPELINE) == [ids["named"]]
    assert set(statuses(wm, PLOT).values()) == {"valid"}
    assert statuses(wm, CARD) == {ids["card"]: "valid"}


def test_invalidating_twice_changes_nothing_and_is_traced(tmp_path):
    wm, stream = world(tmp_path=tmp_path)
    case_2(wm)
    assert len(wm.invalidate(ALERT, source=PIPELINE)) == 2
    assert wm.invalidate(ALERT, source=PIPELINE) == []
    last = lines(stream)[-1]
    assert (last["action"], last["output_summary"]) == ("invalidate_facts", "0 fact(s) invalidated")


def test_facts_carry_their_dependencies():
    wm, _ = world()
    ids = case_2(wm)
    card = wm.view("facts", CARD)[0]
    assert card["depends_on"] == sorted([ids["inside"], ids["delivered"]])
    assert card["source"] == {"kind": "evaluator", "by": "Evaluator", "at": card["assertedAt"],
                              "runId": "wm-test", "url": "Q9"}


# ---- source-priority: Evaluator over Investigator over Extractor --------------------

def test_evaluator_refutation_supersedes_the_investigator():
    wm, _ = world()
    case_2(wm)
    claim = wm.assert_fact(PLOT, "alert_after_cutoff", True, source=INVESTIGATOR, confidence=0.8, depends_on=[ALERT])
    refutation = wm.assert_fact(PLOT, "alert_after_cutoff", False, source=EVALUATOR, confidence=0.6,
                                depends_on=[ALERT])
    late = wm.assert_fact(PLOT, "alert_after_cutoff", True, source=EXTRACTOR, confidence=1.0)

    assert refutation["status"] == "valid"
    assert late["status"] == "superseded"  # higher confidence does not beat a higher-priority source
    facts = {f["id"]: f for f in wm.view("facts", PLOT)}
    assert facts[claim["id"]]["supersededBy"] == refutation["id"]
    assert wm.view("graph", PLOT)["attrs"]["alert_after_cutoff"] is False


def test_order_of_arrival_does_not_matter():
    wm, _ = world()
    case_2(wm)
    first = wm.assert_fact(PLOT, "alert_after_cutoff", False, source=EVALUATOR, confidence=0.6)
    second = wm.assert_fact(PLOT, "alert_after_cutoff", True, source=INVESTIGATOR, confidence=0.9)
    assert (second["status"], second["supersededBy"]) == ("superseded", first["id"])


# ---- history, provenance, determinism ---------------------------------------------

def test_entity_at_replays_the_world_up_to_a_write():
    wm, _ = world()
    case_2(wm)
    before = wm.tick
    wm.upsert_entity("alert", "A-221", {"alert_date": "2019-03-14"}, PIPELINE)
    assert wm.entity_at(ALERT, before)["attrs"]["alert_date"] == "2022-03-14"
    assert wm.entity_at(ALERT, wm.tick)["attrs"]["alert_date"] == "2019-03-14"
    assert wm.entity_at(ALERT, 0) is None
    with pytest.raises(ValueError):
        wm.entity_at(ALERT, wm.tick + 1)


def test_every_write_is_audited_in_order():
    wm, _ = world()
    case_2(wm)
    audit = wm.audit()
    assert [entry["seq"] for entry in audit] == list(range(1, len(audit) + 1))
    assert audit[0] == {"seq": 1, "at": tick_at(1), "kind": "upsertEntity", "actor": "Investigator",
                        "summary": f"{SUPPLIER} (supplier)", "eventId": "evt_1", "runId": "wm-test"}


def test_same_case_twice_builds_the_same_world():
    first, _ = world()
    second, _ = world()
    case_2(first)
    case_2(second)
    assert canonical_json(first.view("timeline")) == canonical_json(second.view("timeline"))
    other_run, _ = world(run_id="another-run")
    assert case_2(other_run) == case_2(world()[0])  # fact ids never depend on the run or the clock


def test_writes_need_a_known_agent_and_known_dependencies():
    wm, _ = world()
    case_2(wm)
    events = len(wm.view("timeline"))
    with pytest.raises(ValueError, match="agent"):
        Source("Intruder")
    with pytest.raises(ValueError, match="never wrote"):
        wm.assert_fact(PLOT, "x", 1, source=INVESTIGATOR, confidence=0.5, depends_on=["plot:eudr:P-999"])
    with pytest.raises(ValueError, match="confidence"):
        wm.assert_fact(PLOT, "x", 1, source=INVESTIGATOR, confidence=1.5)
    with pytest.raises(ValueError, match="based_on"):
        wm.relate(PLOT, "based_on", ALERT, source=INVESTIGATOR)
    with pytest.raises(ValueError, match="entity key"):
        entity_id("plot", "P:014")
    assert len(wm.view("timeline")) == events  # nothing half-written


# ---- the trace: one complete line per call, failures included -----------------------

def test_every_call_writes_one_complete_trace_line(tmp_path):
    wm, stream = world(tmp_path=tmp_path)
    case_2(wm)
    wm.relate(PLOT, "supplied_by", SUPPLIER, source=INVESTIGATOR)
    wm.view("graph", PLOT, by="Evaluator")
    wm.view("facts", PLOT)
    wm.view("timeline")
    wm.audit()
    wm.entity_at(PLOT, 1)
    wm.invalidate(ALERT, source=PIPELINE)

    traced = lines(stream)
    assert [line["action"] for line in traced] == (
        ["connect_world_model"] + ["upsert_entity"] * 4 + ["assert_fact"] * 4
        + ["relate", "view", "view", "view", "audit", "entity_at", "invalidate_facts"]
    )
    on_disk = (tmp_path / "wm-test.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in on_disk] == traced
    for line in traced:
        assert set(line) == set(FIELDS)
        assert (line["provider"], line["model"], line["status"]) == ("memory", "world-model-fallback", "degraded")
    assert traced[5]["confidence"] == 0.9 and traced[5]["agent_name"] == "Investigator"
    assert (traced[10]["action"], traced[10]["agent_name"]) == ("view", "Evaluator")


class BrokenEngine:
    provider, name = "meterless", "world-model-reference"

    def call(self, namespace, op, args):
        raise WorldModelUnavailable("http://127.0.0.1:8787: connection refused")


def test_a_failing_engine_is_traced_then_raised():
    wm, stream = world(engine=BrokenEngine())
    with pytest.raises(WorldModelUnavailable):
        wm.upsert_entity("plot", "P-014", {}, INVESTIGATOR)
    last = lines(stream)[-1]
    assert (last["action"], last["status"]) == ("upsert_entity", "error")
    assert "connection refused" in last["output_summary"]


def test_a_refused_write_is_traced_then_raised():
    wm, stream = world()
    with pytest.raises(EngineError, match="plain JSON"):
        wm.upsert_entity("plot", "P-014", {"tags": {"a", "b"}}, INVESTIGATOR)
    assert lines(stream)[-1]["status"] == "error"


# ---- fallback: no server, same interface, every line says degraded ------------------

def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_no_server_means_in_memory_fallback_marked_degraded(monkeypatch, tmp_path):
    monkeypatch.setenv("WORLD_MODEL_URL", f"http://127.0.0.1:{free_port()}")
    stream = io.StringIO()
    wm = WorldModel("wm-fallback", log_dir=tmp_path, stream=stream)
    assert wm.degraded
    first = lines(stream)[0]
    assert first["status"] == "degraded" and "server unreachable" in first["output_summary"]


def test_a_server_with_another_policy_is_not_trusted():
    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"ok": True, "conflictPolicy": "most-recent-wins", "sourcePriority": []}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        engine, reason = connect(f"http://127.0.0.1:{server.server_address[1]}")
    finally:
        server.shutdown()
    assert isinstance(engine, MemoryEngine)
    assert "most-recent-wins" in reason


# ---- the real Meterless engine (skipped, and said so, when Node is not installed) ----

@pytest.fixture(scope="module")
def meterless_url():
    port = free_port()
    process = subprocess.Popen(
        [NODE, str(TSX), "server.ts"], cwd=METERLESS, env={**os.environ, "WORLD_MODEL_PORT": str(port)},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while True:
        try:
            HttpEngine(url).health()
            break
        except WorldModelUnavailable:
            if process.poll() is not None or time.monotonic() > deadline:
                process.kill()
                pytest.fail(f"World Model server did not start: {process.stdout.read()}")
            time.sleep(0.1)
    yield url
    process.terminate()
    process.wait(timeout=10)


@needs_node
def test_meterless_conformance_suite_passes():
    result = subprocess.run([NODE, str(TSX), "conformance/runner.ts"], cwd=METERLESS,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "8/8 checks passed" in result.stdout


@needs_node
def test_real_engine_and_fallback_build_the_same_world(meterless_url, monkeypatch, tmp_path):
    monkeypatch.setenv("WORLD_MODEL_URL", meterless_url)
    stream = io.StringIO()
    real = WorldModel("wm-parity", log_dir=tmp_path, stream=stream)
    twin, _ = world(run_id="wm-parity")
    assert not real.degraded
    assert lines(stream)[0]["output_summary"] == f"Meterless World Model at {meterless_url}, source-priority"

    for wm in (real, twin):
        case_2(wm)
        wm.assert_fact(PLOT, "alert_after_cutoff", True, source=INVESTIGATOR, confidence=0.8)
        wm.assert_fact(PLOT, "alert_after_cutoff", False, source=EVALUATOR, confidence=0.6)
        wm.upsert_entity("alert", "A-221", {"alert_date": "2019-03-14"}, PIPELINE)
        wm.invalidate(ALERT, source=PIPELINE)

    for read in (lambda wm: wm.view("timeline"), lambda wm: wm.audit(),
                 lambda wm: wm.view("facts", PLOT), lambda wm: wm.view("facts", CARD),
                 lambda wm: wm.view("graph", PLOT), lambda wm: wm.entity_at(ALERT, 3)):
        assert canonical_json(read(real)) == canonical_json(read(twin))
    assert {line["provider"] for line in lines(stream)} == {"meterless"}
    assert {line["status"] for line in lines(stream)} == {"success"}
