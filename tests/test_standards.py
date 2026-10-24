"""GreenTrace for other agents: the MCP server, the A2A agent, the OpenTelemetry spans (app/standards)."""
import copy
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from app import tracing
from app.api import app
from app.checks.landcover import CELLS_FILE
from app.standards import VERSION, a2a, mcp_server, otel, tools
from app.world.cases import DEFAULT_CLAIM

ROOT = Path(__file__).resolve().parent.parent
FIVE = ["check_claim", "check_plots", "what_if", "explain", "verify_receipt"]
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def sample_world(monkeypatch, tmp_path):
    """Sample mode, no network, every log (trace lines and spans) in this test's own folder."""
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("GREENTRACE_OTEL", raising=False)
    monkeypatch.setattr(tracing, "LOG_DIR", tmp_path)
    otel.RECORDER.shutdown()
    yield tmp_path
    otel.RECORDER.shutdown()


def mcp(*calls):
    """Run calls (async functions of a connected client) against an in-memory MCP client; their results."""
    async def session():
        async with Client(mcp_server.build(stream=open(os.devnull, "w"))) as client:
            return [await call(client) for call in calls]
    return anyio.run(session)


def check_case_2(client):
    return client.call_tool("check_claim", {"claim": DEFAULT_CLAIM})


# ---- MCP -------------------------------------------------------------------------------------------

def test_mcp_lists_the_five_tools_and_check_claim_on_case_2_is_contradicted():
    listed, result = mcp(lambda c: c.list_tools(), check_case_2)
    assert [t.name for t in listed.tools] == FIVE
    assert not result.is_error
    out = result.structured_content
    assert out["verdict"] == "CONTRADICTED" and out["mode"] == "sample" and len(out["run_id"]) == 16
    assert out["sentence"].startswith("This promise is false:") and "P-014" in out["sentence"]
    assert json.loads(result.content[0].text)["verdict"] == "CONTRADICTED"  # the text content says the same


def test_mcp_explain_cites_the_rows_and_what_if_flips_the_verdict():
    async def flow(client):
        run_id = (await check_case_2(client)).structured_content["run_id"]
        explained = (await client.call_tool("explain", {"run_id": run_id})).structured_content
        changed = (await client.call_tool("what_if", {"run_id": run_id, "entity": "alert:A-221",
                                                       "field": "alert_date", "value": "2020-12-31"})).structured_content
        return explained, changed

    (explained, changed), = mcp(flow)
    finding = explained["findings"][0]
    assert explained["verdict"] == "CONTRADICTED" and finding["code"] == "confirmed_alert_in_delivering_plot"
    cited = {ref["source"]: ref["row"] for ref in finding["evidence"]}
    assert cited["alert"]["alert_id"] == "A-221" and cited["alert"]["alert_date"] == "2022-03-14"
    assert cited["plot"]["plot_id"] == "P-014"
    assert explained["flip"] == "The claim would be PROVEN if alert A-221 were dated 2020-12-31."
    assert (changed["verdict_before"], changed["verdict_after"]) == ("CONTRADICTED", "PROVEN")
    assert changed["sentence"].startswith("This promise holds:")


def test_mcp_receipt_resource_feeds_verify_receipt():
    async def flow(client):
        run_id = (await check_case_2(client)).structured_content["run_id"]
        text = (await client.read_resource(f"greentrace://runs/{run_id}/receipt")).contents[0].text
        tampered = json.loads(text)
        tampered["sources"][0]["rows"][0] = {**tampered["sources"][0]["rows"][0], "company_id": "someone-else"}
        return [(await client.call_tool("verify_receipt", {"receipt_json": r})).structured_content
                for r in (text, json.loads(text), tampered, "{}")]

    (valid_text, valid_object, tampered, unreadable), = mcp(flow)
    assert valid_text["status"] == valid_object["status"] == "VALID"
    assert valid_text["verdict"] == "CONTRADICTED" and valid_text["seal"] == valid_text["receipt_seal"]
    assert tampered["status"] == "TAMPERED" and tampered["problems"]
    assert unreadable["status"] == "UNREADABLE"


def test_mcp_says_what_is_wrong_instead_of_inventing():
    unknown, bad_case = mcp(lambda c: c.call_tool("explain", {"run_id": "nope"}),
                            lambda c: c.call_tool("check_claim", {"claim": DEFAULT_CLAIM, "case": 99}))
    assert unknown.is_error and "no finished run 'nope' in this process" in unknown.content[0].text
    assert bad_case.is_error and "case takes one of 1..21" in bad_case.content[0].text


@pytest.mark.skipif(not CELLS_FILE.exists(), reason="data/reference_cells.csv.gz not built")
def test_mcp_check_plots_is_the_api_route():
    from tests.test_plotcheck import P041, P042, collection

    result, = mcp(lambda c: c.call_tool("check_plots", {"geojson": json.dumps(collection(P042, P041))}))
    out = result.structured_content
    assert out["summary"]["plots"] == {"CONTRADICTED": 1, "UNVERIFIABLE": 1, "NO_ISSUE": 0}
    assert out["batch_id"].startswith("plots-") and out["exports"]["receipt"].endswith("/receipt")


def test_mcp_over_http_inside_the_api(monkeypatch):
    def rpc(client, id_, method, params, **headers):
        return client.post("/mcp", headers={**MCP_HEADERS, **headers},
                           json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params})

    with TestClient(app) as client:
        init = rpc(client, 1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                             "clientInfo": {"name": "test", "version": "0"}})
        assert init.status_code == 200 and init.json()["result"]["serverInfo"]["version"] == VERSION
        version = {"MCP-Protocol-Version": "2025-06-18"}
        assert [t["name"] for t in rpc(client, 2, "tools/list", {}, **version).json()["result"]["tools"]] == FIVE
        called = rpc(client, 3, "tools/call", {"name": "check_claim", "arguments": {"claim": DEFAULT_CLAIM}}, **version)
        assert called.json()["result"]["structuredContent"]["verdict"] == "CONTRADICTED"

        refused = rpc(client, 4, "tools/list", {}, Origin="http://attacker.example", **version)
        assert refused.status_code == 403 and "GREENTRACE_MCP_ALLOWED_ORIGINS" in refused.json()["detail"]
        assert rpc(client, 5, "tools/list", {}, Origin="http://localhost:6274", **version).status_code == 200
        monkeypatch.setenv("GREENTRACE_MCP_ALLOWED_ORIGINS", "https://inspector.example/")
        assert rpc(client, 6, "tools/list", {}, Origin="https://inspector.example", **version).status_code == 200
    # outside the app's lifespan the endpoint says how to start it, it does not hang
    response = TestClient(app).post("/mcp", headers=MCP_HEADERS, json={})
    assert response.status_code == 503 and "lifespan" in response.json()["detail"]


def test_allowed_origins_match_hosts_and_ports_not_lookalikes():
    assert mcp_server.allowed_origin("http://localhost") and mcp_server.allowed_origin("http://127.0.0.1:8000")
    assert not mcp_server.allowed_origin("http://localhost.attacker.example")
    assert not mcp_server.allowed_origin("http://localhost:80@attacker.example")


def test_mcp_over_stdio_keeps_stdout_for_the_protocol():
    """The real command: python -m app.standards.mcp_server; trace lines must not reach stdout."""
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "app.standards.mcp_server"], cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT), "SAMPLE_MODE": "true", "HMEM_URL": "", "GREENTRACE_OTEL": "0"})

    async def session():
        async with Client(params) as client:
            names = [t.name for t in (await client.list_tools()).tools]
            return names, (await check_case_2(client)).structured_content

    names, out = anyio.run(session)
    assert names == FIVE and out["verdict"] == "CONTRADICTED"


# ---- A2A -------------------------------------------------------------------------------------------

def send(client, method="message/send", parts=None, headers=None, **params):
    message = {"kind": "message", "role": "user", "messageId": "m-1",
               "parts": parts or [{"kind": "text", "text": DEFAULT_CLAIM}]}
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {"message": message, **params}}
    return client.post("/a2a", json=body, headers=headers or {}).json()


def test_agent_card_lists_the_five_skills_at_both_well_known_paths(monkeypatch):
    client = TestClient(app)
    card = client.get("/.well-known/agent-card.json").json()
    assert client.get("/.well-known/agent.json").json() == card
    assert card["name"] == "GreenTrace" and card["version"] == VERSION and card["description"]
    assert [s["id"] for s in card["skills"]] == FIVE and all(s["tags"] and s["description"] for s in card["skills"])
    assert card["capabilities"] == {"streaming": False, "pushNotifications": False}
    assert card["url"] == "http://testserver/a2a" and card["protocolVersion"] == "0.3.0"  # A2A 0.3 clients
    assert card["supportedInterfaces"] == [  # A2A 1.0 clients, preferred first
        {"url": "http://testserver/a2a", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"},
        {"url": "http://testserver/a2a", "protocolBinding": "JSONRPC", "protocolVersion": "0.3"}]
    monkeypatch.setenv("GREENTRACE_PUBLIC_URL", "https://greentrace.example/")
    assert client.get("/.well-known/agent-card.json").json()["url"] == "https://greentrace.example/a2a"


def test_message_send_on_case_2_returns_a_completed_task_with_the_verdict():
    out = send(TestClient(app))
    task = out["result"]
    assert out["id"] == 1 and task["kind"] == "task" and task["status"]["state"] == "completed"
    artifact = task["artifacts"][0]
    text, data = artifact["parts"]
    assert artifact["name"] == "check_claim" and text["kind"] == "text" and data["kind"] == "data"
    assert text["text"].startswith("CONTRADICTED: This promise is false:")
    assert data["data"]["verdict"] == "CONTRADICTED" and data["data"]["run_id"]
    assert task["history"][0]["role"] == "user" and task["history"][0]["parts"][0]["text"] == DEFAULT_CLAIM


def test_send_message_in_a2a_1_0_json():
    client = TestClient(app)
    out = send(client, "SendMessage", parts=[{"text": DEFAULT_CLAIM}], headers={"A2A-Version": "1.0"})
    task = out["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED" and "kind" not in task
    text, data = task["artifacts"][0]["parts"]
    assert text == {"text": text["text"]} and "CONTRADICTED" in text["text"]
    assert data["mediaType"] == "application/json" and data["data"]["verdict"] == "CONTRADICTED"
    assert task["history"][0]["role"] == "ROLE_USER"
    # without the header, the 1.0 method names are still answered as 1.0
    assert send(client, "SendMessage", parts=[{"text": DEFAULT_CLAIM}])["result"]["task"]["status"]["state"] \
        == "TASK_STATE_COMPLETED"
    got = client.post("/a2a", headers={"A2A-Version": "1.0"}, json={
        "jsonrpc": "2.0", "id": 2, "method": "GetTask", "params": {"id": task["id"], "historyLength": 0}}).json()
    assert got["result"]["id"] == task["id"] and "history" not in got["result"]
    old = client.post("/a2a", json={"jsonrpc": "2.0", "id": 3, "method": "tasks/get", "params": {"id": task["id"]}})
    assert old.json()["result"]["status"]["state"] == "completed"  # the same task, read by a 0.3 client


def test_every_skill_answers_through_a_data_part():
    client = TestClient(app)
    run_id = send(client)["result"]["artifacts"][0]["parts"][1]["data"]["run_id"]

    def skill(**data):
        return send(client, parts=[{"kind": "data", "data": data}])["result"]

    explained = skill(skill="explain", run_id=run_id)
    assert explained["artifacts"][0]["parts"][1]["data"]["findings"][0]["code"] == "confirmed_alert_in_delivering_plot"
    changed = skill(skill="what_if", run_id=run_id, entity="alert:A-221", field="confidence", value="low")
    assert changed["artifacts"][0]["parts"][0]["text"].startswith("CONTRADICTED -> UNVERIFIABLE:")
    receipt = client.get(f"/runs/{run_id}/receipt").json()
    assert skill(skill="verify_receipt", receipt_json=receipt)["artifacts"][0]["parts"][0]["text"].startswith("VALID")
    assert skill(claim=DEFAULT_CLAIM, case=1)["artifacts"][0]["parts"][1]["data"]["verdict"] == "PROVEN"
    rejected = skill(skill="explain", run_id="nope")
    assert rejected["status"]["state"] == "rejected" and rejected["artifacts"] == []
    assert "no finished run 'nope'" in rejected["status"]["message"]["parts"][0]["text"]


def test_json_rpc_errors_are_clean_and_say_why():
    client = TestClient(app)

    def error(response):
        return response["error"]["code"], response["error"]

    assert error(client.post("/a2a", content=b"{not json").json())[0] == a2a.PARSE_ERROR
    assert error(client.post("/a2a", json=[{"jsonrpc": "2.0"}]).json())[0] == a2a.INVALID_REQUEST
    code, body = error(client.post("/a2a", json={"jsonrpc": "2.0", "id": 9, "method": "tasks/cancel",
                                                 "params": {}}).json())
    assert code == a2a.METHOD_NOT_FOUND and body["data"]["method"] == "tasks/cancel"
    code, body = error(send(client, headers={"A2A-Version": "0.3"}, method="SendMessage"))
    assert code == a2a.METHOD_NOT_FOUND and "send A2A-Version: 1.0" in body["data"]["detail"]
    code, body = error(send(client, headers={"A2A-Version": "0.5"}))
    assert code == a2a.VERSION_NOT_SUPPORTED and body["data"][0]["reason"] == "VERSION_NOT_SUPPORTED"
    assert body["data"][0]["domain"] == "a2a-protocol.org"
    code, body = error(send(client, parts=[{"kind": "file", "file": {"uri": "https://x.example/a.pdf"}}]))
    assert code == a2a.CONTENT_TYPE
    code, body = error(send(client, parts=[{"kind": "data", "data": {"skill": "what_if", "run_id": "x"}}]))
    assert code == a2a.INVALID_PARAMS and "missing entity, field, value" in body["data"]["detail"]
    assert error(send(client, parts=[{"kind": "data", "data": {"skill": "delete_everything"}}]))[0] \
        == a2a.INVALID_PARAMS
    assert error(send(client, configuration={"pushNotificationConfig": {"url": "https://x.example"}}))[0] \
        == a2a.PUSH_NOT_SUPPORTED
    assert error(send(client, configuration={"acceptedOutputModes": ["image/png"]}))[0] == a2a.CONTENT_TYPE
    finished = send(client)["result"]["id"]
    body = {"jsonrpc": "2.0", "id": 4, "method": "message/send", "params": {"message": {
        "kind": "message", "role": "user", "messageId": "m-2", "taskId": finished, "parts": [{"text": "again"}]}}}
    assert error(client.post("/a2a", json=body).json())[0] == a2a.UNSUPPORTED
    body["params"]["message"]["taskId"] = "unknown-task"
    assert error(client.post("/a2a", json=body).json())[0] == a2a.TASK_NOT_FOUND
    del body["params"]["message"]["messageId"]
    assert "messageId" in error(client.post("/a2a", json=body).json())[1]["data"]["detail"]


def test_a_crash_is_still_a_json_rpc_answer(monkeypatch):
    def crash(run_id):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(tools, "explain", crash)
    out = send(TestClient(app), parts=[{"kind": "data", "data": {"skill": "explain", "run_id": "x"}}])
    assert out["error"] == {"code": a2a.INTERNAL_ERROR, "message": "Internal error", "data": {"detail": "RuntimeError"}}
    bad = {"jsonrpc": "2.0", "id": 2, "method": "message/send", "params": {"message": {
        "role": "user", "messageId": "m-3", "contextId": 1, "parts": [{"text": DEFAULT_CLAIM}]}}}
    assert TestClient(app).post("/a2a", json=bad).json()["error"]["code"] == a2a.INVALID_PARAMS


def test_accepted_output_modes_keep_only_the_parts_asked_for():
    task = send(TestClient(app), configuration={"acceptedOutputModes": ["text/plain"]})["result"]
    assert [p["kind"] for p in task["artifacts"][0]["parts"]] == ["text"]


# ---- OpenTelemetry -----------------------------------------------------------------------------------

def run_and_spans(client):
    run_id = client.post("/run", json={"input": {"claim": DEFAULT_CLAIM}}).json()["run_id"]
    return run_id, client.get(f"/runs/{run_id}/otel").json()


def test_one_span_per_trace_line_with_the_13_fields_and_the_calling_agent_as_parent(sample_world):
    client = TestClient(app)
    run_id, out = run_and_spans(client)
    lines = [json.loads(line) for line in (sample_world / f"{run_id}.jsonl").read_text().splitlines()]
    spans = out["spans"]
    assert out["run_id"] == run_id and out["trace_id"] == otel.trace_id(run_id) and len(out["trace_id"]) == 32
    assert len(spans) == len(lines) and all(set(s) == set(otel.SPAN_KEYS) for s in spans)
    for span, line in zip(spans, lines):
        assert span["name"] == f"{line['agent_name']}.{line['action']}" and len(span["span_id"]) == 16
        assert span["attributes"] == {f"greentrace.{k}": line[k] for k in tracing.FIELDS}
        assert span["end"] == line["timestamp"] and span["start"] <= span["end"]
    by_id = {s["span_id"]: s for s in spans}
    for span in spans:
        agent = span["attributes"]["greentrace.agent_name"]
        parent = by_id.get(span["parent_span_id"])
        if parent is None:
            assert span["parent_span_id"] is None and span["start"] == span["end"]  # a root: nobody called it
        else:  # the caller handed the work to this agent
            assert parent["attributes"]["greentrace.target_agent"] == agent != parent["attributes"]["greentrace.agent_name"]
    names = {s["name"]: by_id.get(s["parent_span_id"], {}).get("name") for s in spans}
    assert names["Pipeline.receive_case"] is None and names["Extractor.extract_claims"] == "Pipeline.receive_case"
    assert names["Planner.plan_hypotheses"] == "Extractor.fallback_rules"
    assert names["Writer.apply_verdict_rules"] == "Evaluator.approve"


def test_a_what_if_lands_in_the_same_trace(sample_world):
    client = TestClient(app)
    run_id, before = run_and_spans(client)
    client.post(f"/runs/{run_id}/what_if", json={"entity": "alert:A-221", "field": "alert_date", "value": "2020-12-31"})
    after = client.get(f"/runs/{run_id}/otel").json()
    assert after["trace_id"] == before["trace_id"] and after["spans"][:len(before["spans"])] == before["spans"]
    assert any(s["name"] == "Pipeline.what_if" and s["parent_span_id"] is None for s in after["spans"])
    stored = [json.loads(line) for line in (sample_world / f"{run_id}.otel.jsonl").read_text().splitlines()]
    assert {s["trace_id"] for s in stored} == {before["trace_id"]}


def test_the_official_trace_lines_do_not_change_and_otel_can_be_turned_off(sample_world, monkeypatch):
    client = TestClient(app)
    run_on, _ = run_and_spans(client)
    monkeypatch.setenv("GREENTRACE_OTEL", "0")
    run_off = client.post("/run", json={"input": {"claim": DEFAULT_CLAIM}}).json()["run_id"]
    assert not (sample_world / f"{run_off}.otel.jsonl").exists()
    off = client.get(f"/runs/{run_off}/otel")
    assert off.status_code == 404 and "GREENTRACE_OTEL=0" in off.json()["detail"]

    def official(run_id):
        lines = [json.loads(line) for line in (sample_world / f"{run_id}.jsonl").read_text().splitlines()]
        return [{k: v for k, v in line.items() if k not in ("timestamp", "run_id")} for line in lines]

    assert official(run_on) == official(run_off)  # the same lines, with or without OpenTelemetry


def test_a_broken_exporter_never_stops_an_investigation(monkeypatch):
    def broken(*_):
        raise OSError("disk full")

    monkeypatch.setattr(otel.RECORDER, "record", broken)
    out = TestClient(app).post("/run", json={"input": {"claim": DEFAULT_CLAIM}}).json()
    assert out["verdict"] == "CONTRADICTED"


def test_unknown_or_unsafe_run_ids():
    client = TestClient(app)
    assert client.get("/runs/nope/otel").status_code == 404
    assert client.get("/runs/a.b/otel").status_code == 404


class Collector(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):  # noqa: N802 - http.server's name
        Collector.received.append((self.path, self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.end_headers()

    def log_message(self, *_):
        pass


def test_otlp_export_only_when_the_endpoint_is_set(monkeypatch):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    assert otel.RECORDER.tracer() and len(otel.RECORDER.provider._active_span_processor._span_processors) == 1
    otel.RECORDER.shutdown()
    server = HTTPServer(("127.0.0.1", 0), Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Collector.received = []
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", f"http://127.0.0.1:{server.server_port}")
    try:
        run_id = TestClient(app).post("/run", json={"input": {"claim": DEFAULT_CLAIM}}).json()["run_id"]
        otel.RECORDER.provider.force_flush()
    finally:
        server.shutdown()
    spans = []
    for path, body in Collector.received:
        assert path == "/v1/traces"
        request = ExportTraceServiceRequest.FromString(body)
        for resource in request.resource_spans:
            service = {a.key: a.value.string_value for a in resource.resource.attributes}["service.name"]
            assert service == "greentrace"
            spans += [s for scope in resource.scope_spans for s in scope.spans]
    assert spans and {s.trace_id.hex() for s in spans} == {otel.trace_id(run_id)}
    assert {s.name for s in spans} >= {"Pipeline.receive_case", "Evaluator.approve", "Writer.apply_verdict_rules"}


# ---- the shared operations ------------------------------------------------------------------------------

def test_the_operations_call_the_real_code_and_refuse_clearly(monkeypatch):
    out = tools.check_claim(DEFAULT_CLAIM, stream=open(os.devnull, "w"))
    assert tools.receipt(out["run_id"])["verdict_sha256"] == out["verdict_sha256"]
    with pytest.raises(tools.Refused, match="non-empty string"):
        tools.check_claim("  ")
    with pytest.raises(tools.Refused, match="can change"):
        tools.what_if(out["run_id"], "alert:A-221", "alert_id", "x")
    with pytest.raises(tools.Refused, match="not JSON"):
        tools.check_plots("{not json")
    monkeypatch.setenv("SAMPLE_MODE", "false")
    with pytest.raises(tools.Refused, match="sample mode only"):
        tools.check_claim(DEFAULT_CLAIM, case=2)
    assert VERSION == app.version


def test_the_same_data_still_gives_the_same_verdict_through_every_door():
    seals = {tools.check_claim(DEFAULT_CLAIM, stream=open(os.devnull, "w"))["verdict_sha256"]}
    seals.add(mcp(check_case_2)[0].structured_content["verdict_sha256"])
    seals.add(send(TestClient(app))["result"]["artifacts"][0]["parts"][1]["data"]["verdict_sha256"])
    assert len(seals) == 1


def test_tampering_the_receipt_object_does_not_touch_the_run():
    out = tools.check_claim(DEFAULT_CLAIM, stream=open(os.devnull, "w"))
    receipt = tools.receipt(out["run_id"])
    tampered = copy.deepcopy(receipt)
    tampered["verdict"]["overall"] = "PROVEN"
    tampered["verdict_sha256"] = "0" * 64
    assert tools.verify_receipt(tampered)["status"] == "TAMPERED"
    assert tools.verify_receipt(receipt)["status"] == "VALID"
