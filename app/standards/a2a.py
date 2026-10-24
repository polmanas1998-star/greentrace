"""GreenTrace as an A2A agent (Agent2Agent protocol, JSON-RPC binding), versions 1.0 and 0.3.

GET  /.well-known/agent-card.json   the agent card (the spec's well-known path; /.well-known/agent.json too)
POST /a2a                           JSON-RPC 2.0: SendMessage and GetTask (A2A 1.0), message/send and
                                    tasks/get (A2A 0.3)

The version is the A2A-Version header (or query parameter): "1.0" or "0.3"; without one, the spec says
0.3, and the 1.0 method names are answered as 1.0. Each version gets its own JSON: 1.0 writes
"TASK_STATE_COMPLETED", "ROLE_AGENT" and {"text": ...} parts, 0.3 writes "completed", "agent" and
{"kind": "text", "text": ...}.

A message runs one skill, synchronously: the five operations of app/standards/tools.py. A data part
{"skill": "what_if", "run_id": ..., ...} names the skill and its arguments; otherwise the text parts
are the claim to check (check_claim). The task comes back finished, its artifact holding one line of
text and the operation's JSON. Tasks are kept in memory, the last KEPT_TASKS. No streaming, no push
notifications: the card says so. GreenTrace answers other agents; it calls none.
"""
import json
import os
import uuid
from collections import OrderedDict
from typing import Any, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app import orchestrator
from app.standards import tools
from app.tracing import now_utc

router = APIRouter(include_in_schema=False)

VERSIONS = ("1.0", "0.3")  # preferred first, as the card lists them
METHODS = {"1.0": {"SendMessage": "send", "GetTask": "get"}, "0.3": {"message/send": "send", "tasks/get": "get"}}
KEPT_TASKS = orchestrator.KEPT_RUNS
TASKS: "OrderedDict[str, dict]" = OrderedDict()
REPOSITORY = "https://github.com/polmanas1998-star/greentrace"

# JSON-RPC codes: the standard ones and the A2A errors used here (A2A 1.0, section 5.4).
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602
TASK_NOT_FOUND, PUSH_NOT_SUPPORTED, UNSUPPORTED, CONTENT_TYPE, VERSION_NOT_SUPPORTED = -32001, -32003, -32004, -32005, -32009
A2A_ERRORS = {TASK_NOT_FOUND: "TASK_NOT_FOUND", PUSH_NOT_SUPPORTED: "PUSH_NOTIFICATION_NOT_SUPPORTED",
              UNSUPPORTED: "UNSUPPORTED_OPERATION", CONTENT_TYPE: "CONTENT_TYPE_NOT_SUPPORTED",
              VERSION_NOT_SUPPORTED: "VERSION_NOT_SUPPORTED"}

# Each skill: required arguments, optional ones, and the call.
SKILLS: dict[str, tuple[tuple[str, ...], tuple[str, ...], Callable[[dict], dict]]] = {
    "check_claim": (("claim",), ("company", "case"),
                    lambda a: tools.check_claim(a["claim"], a.get("company"), a.get("case"))),
    "check_plots": (("geojson",), ("promise", "documents"),
                    lambda a: tools.check_plots(a["geojson"], a.get("promise"), a.get("documents"))),
    "what_if": (("run_id", "entity", "field", "value"), (),
                lambda a: tools.what_if(a["run_id"], a["entity"], a["field"], a["value"])),
    "explain": (("run_id",), (), lambda a: tools.explain(a["run_id"])),
    "verify_receipt": (("receipt_json",), (), lambda a: tools.verify_receipt(a["receipt_json"])),
}
TITLES = {"check_claim": "Check a claim", "check_plots": "Check a plot file", "what_if": "What if",
          "explain": "Explain a verdict", "verify_receipt": "Verify a receipt"}
TAGS = {"check_claim": ["deforestation", "eudr", "claim", "verdict"],
        "check_plots": ["deforestation", "eudr", "geojson", "plots"],
        "what_if": ["counterfactual", "verdict"], "explain": ["explanation", "evidence"],
        "verify_receipt": ["receipt", "audit", "verification"]}


class RpcError(Exception):
    def __init__(self, code: int, message: str, **metadata: Any):
        super().__init__(message)
        self.code, self.message, self.metadata = code, message, metadata

    def body(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.code in A2A_ERRORS:  # A2A 1.0, section 9.5: a google.rpc.ErrorInfo in data
            error["data"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": A2A_ERRORS[self.code],
                              "domain": "a2a-protocol.org",
                              "metadata": {k: str(v) for k, v in self.metadata.items()}}]
        elif self.metadata:
            error["data"] = self.metadata
        return error


# ---- the agent card ---------------------------------------------------------------------------------

@router.get("/.well-known/agent-card.json")
@router.get("/.well-known/agent.json")
def agent_card(request: Request) -> JSONResponse:
    return JSONResponse(card(public_url(request)), headers={"Cache-Control": "public, max-age=300"})


def public_url(request: Request) -> str:
    """Where other agents reach this server: GREENTRACE_PUBLIC_URL behind a proxy, else the request's own URL."""
    return (os.environ.get("GREENTRACE_PUBLIC_URL") or str(request.base_url)).rstrip("/")


def card(base_url: str) -> dict[str, Any]:
    """The A2A 1.0 agent card, plus the 0.3 fields (url, preferredTransport, protocolVersion) 0.3 clients read."""
    url = f"{base_url}/a2a"
    return {
        "name": "GreenTrace",
        "description": "Checks a company's deforestation-free claim against the evidence (supplier plots, "
                       "deliveries, satellite alerts, the EU JRC 2020 forest map and Hansen/UMD loss years) and "
                       "explains why. Fixed rules decide the verdict, PROVEN, CONTRADICTED or UNVERIFIABLE, never "
                       "the model; every verdict has a receipt that re-derives it offline. Decision support: a "
                       "human reviews every verdict.",
        "supportedInterfaces": [{"url": url, "protocolBinding": "JSONRPC", "protocolVersion": v} for v in VERSIONS],
        "url": url,
        "preferredTransport": "JSONRPC",
        "protocolVersion": "0.3.0",
        "provider": {"organization": "GreenTrace", "url": REPOSITORY},
        "version": tools.VERSION,
        "documentationUrl": f"{REPOSITORY}/blob/main/docs/features/standards.md",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": [{"id": name, "name": TITLES[name], "description": tools.DESCRIPTIONS[name], "tags": TAGS[name],
                    "examples": tools.EXAMPLES[name],
                    "inputModes": ["text/plain", "application/json"] if name == "check_claim" else ["application/json"],
                    "outputModes": ["text/plain", "application/json"]} for name in tools.NAMES],
    }


# ---- JSON-RPC ---------------------------------------------------------------------------------------

@router.post("/a2a")
async def a2a(request: Request) -> JSONResponse:
    raw = await request.body()
    version = request.headers.get("A2A-Version") or request.query_params.get("A2A-Version")
    return JSONResponse(await run_in_threadpool(answer, raw, version))


def answer(raw: bytes, requested_version: Optional[str]) -> dict[str, Any]:
    """One JSON-RPC request (bytes) in, one JSON-RPC response out; errors are answers too."""
    request_id: Any = None
    try:
        try:
            body = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RpcError(PARSE_ERROR, "Invalid JSON payload", detail=str(error)) from error
        if isinstance(body, dict) and isinstance(body.get("id"), (str, int)) and not isinstance(body["id"], bool):
            request_id = body["id"]
        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or not isinstance(body.get("method"), str) \
                or request_id is None:
            raise RpcError(INVALID_REQUEST, "Request payload validation error",
                           detail='a JSON-RPC 2.0 request object: {"jsonrpc": "2.0", "id": string or number, '
                                  '"method": ..., "params": {...}} (no batches)')
        version = _version(requested_version, body["method"])
        operation = METHODS[version].get(body["method"])
        if operation is None:
            other = next((v for v in VERSIONS if body["method"] in METHODS[v]), None)
            raise RpcError(METHOD_NOT_FOUND, "Method not found", method=body["method"],
                           detail=f"A2A {version} methods: {', '.join(METHODS[version])}"
                                  + (f"; {body['method']} is A2A {other}: send A2A-Version: {other}" if other else ""))
        params = body.get("params")
        if not isinstance(params, dict):
            raise RpcError(INVALID_PARAMS, "Invalid parameters", detail="params must be an object")
        result = send(params, version) if operation == "send" else get(params, version)
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except RpcError as error:
        return {"jsonrpc": "2.0", "id": request_id, "error": error.body()}


def _version(requested: Optional[str], method: str) -> str:
    if requested is None or not requested.strip():
        return "1.0" if method in METHODS["1.0"] else "0.3"  # the spec: no header means 0.3
    version = ".".join(requested.strip().split(".")[:2])  # Major.Minor; a patch number is not considered
    if version not in VERSIONS:
        raise RpcError(VERSION_NOT_SUPPORTED, f"The requested A2A protocol version {requested} is not supported "
                                              f"by this agent", supportedVersions=", ".join(VERSIONS))
    return version


def send(params: dict, version: str) -> dict[str, Any]:
    """SendMessage / message/send: run the skill the message asks for, return the finished task."""
    message = params.get("message")
    if not isinstance(message, dict):
        raise RpcError(INVALID_PARAMS, "Invalid parameters", detail="params.message must be a Message object")
    for required in ("messageId", "role", "parts"):
        if required not in message:
            raise RpcError(INVALID_PARAMS, "Invalid parameters", detail=f"params.message.{required} is required")
    if message["role"] not in ("ROLE_USER", "user"):
        raise RpcError(INVALID_PARAMS, "Invalid parameters", detail="params.message.role must be the user's")
    config = params.get("configuration") or {}
    if not isinstance(config, dict):
        raise RpcError(INVALID_PARAMS, "Invalid parameters", detail="params.configuration must be an object")
    if config.get("taskPushNotificationConfig") or config.get("pushNotificationConfig"):
        raise RpcError(PUSH_NOT_SUPPORTED, "Push notifications are not supported: the task comes back finished")
    accepted = _accepted(config.get("acceptedOutputModes"))
    if message.get("taskId"):
        if message["taskId"] not in TASKS:
            raise RpcError(TASK_NOT_FOUND, "Task not found", taskId=message["taskId"])
        raise RpcError(UNSUPPORTED, "Task is in a terminal state: GreenTrace finishes every task in one message; "
                                    "send a new message without taskId", taskId=message["taskId"])

    parts = _parts(message["parts"])
    skill, args = _skill(parts)
    context_id = message.get("contextId") or uuid.uuid4().hex
    task = {"id": uuid.uuid4().hex, "context_id": context_id, "skill": skill,
            "history": [{"message_id": message["messageId"], "role": "user", "parts": parts}]}
    try:
        out = SKILLS[skill][2](args)
    except tools.Refused as error:
        task.update(state="rejected", reason=str(error), artifacts=[])
    else:
        task.update(state="completed", reason=None, artifacts=[{
            "artifact_id": uuid.uuid4().hex, "name": skill, "description": tools.DESCRIPTIONS[skill],
            "parts": [p for p in ({"text": headline(skill, out)}, {"data": out}) if _kind(p) in accepted]}])
    task["timestamp"] = now_utc()
    _keep(task)
    rendered = render_task(task, version, config.get("historyLength"))
    return {"task": rendered} if version == "1.0" else rendered


def get(params: dict, version: str) -> dict[str, Any]:
    """GetTask / tasks/get: a task this process keeps."""
    task_id = params.get("id")
    if not isinstance(task_id, str):
        raise RpcError(INVALID_PARAMS, "Invalid parameters", detail="params.id (the task id) is required")
    task = TASKS.get(task_id)
    if task is None:
        raise RpcError(TASK_NOT_FOUND, "Task not found", taskId=task_id)
    return render_task(task, version, params.get("historyLength"))


def headline(skill: str, out: dict) -> str:
    """The artifact's line of text; the JSON part beside it holds everything."""
    if skill == "check_claim":
        return f"{out['verdict'] or out['status']}: {out['sentence']}"
    if skill == "what_if":
        return f"{out['verdict_before']} -> {out['verdict_after']}: {out['sentence']}"
    if skill == "explain":
        return f"{out['verdict']}: {out['sentence']}"
    if skill == "check_plots":
        return out["summary"]["sentence"]
    if out["status"] == "VALID":
        return f"VALID: {out['verdict']} re-derived offline from the receipt (seal {out['seal']})"
    return f"{out['status']}: {'; '.join(out['problems'])}"


# ---- reading the message ------------------------------------------------------------------------------

def _parts(parts: Any) -> list[dict]:
    """Text and JSON parts, in either version's shape; files, URLs and raw bytes are refused."""
    if not isinstance(parts, list) or not parts:
        raise RpcError(INVALID_PARAMS, "Invalid parameters", detail="params.message.parts must be a non-empty list")
    read = []
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            read.append({"text": part["text"]})
        elif isinstance(part, dict) and "data" in part:
            read.append({"data": part["data"]})
        else:
            raise RpcError(CONTENT_TYPE, "Content type not supported: GreenTrace reads text parts and JSON data "
                                         "parts", accepted="text/plain, application/json")
    return read


def _skill(parts: list[dict]) -> tuple[str, dict]:
    """A data part with "skill" (or "claim") names the operation; otherwise the text is the claim."""
    data = next((p["data"] for p in parts if isinstance(p.get("data"), dict)
                 and ("skill" in p["data"] or "claim" in p["data"])), None)
    if data is None:
        text = "\n".join(p["text"] for p in parts if "text" in p).strip()
        if not text:
            raise RpcError(INVALID_PARAMS, "Invalid parameters",
                           detail='send the claim as text, or a data part {"skill": ..., arguments}')
        return "check_claim", {"claim": text}
    skill = data.get("skill", "check_claim")
    if skill not in SKILLS:
        raise RpcError(INVALID_PARAMS, "Invalid parameters", detail=f"skill must be one of {', '.join(SKILLS)}")
    required, optional, _ = SKILLS[skill]
    args = {k: v for k, v in data.items() if k != "skill"}
    missing = [k for k in required if k not in args]
    unknown = sorted(set(args) - set(required) - set(optional))
    if missing or unknown:
        raise RpcError(INVALID_PARAMS, "Invalid parameters",
                       detail=f"{skill} takes {', '.join(required)}"
                              + (f" and optionally {', '.join(optional)}" if optional else "")
                              + (f"; missing {', '.join(missing)}" if missing else "")
                              + (f"; unknown {', '.join(unknown)}" if unknown else ""))
    return skill, args


def _accepted(modes: Any) -> set[str]:
    if not modes:
        return {"text", "data"}
    kinds = {"text" for m in modes if m == "text/plain"} | {"data" for m in modes if m == "application/json"}
    if not kinds:
        raise RpcError(CONTENT_TYPE, "Content type not supported: GreenTrace answers text/plain and "
                                     "application/json", accepted="text/plain, application/json")
    return kinds


def _kind(part: dict) -> str:
    return "text" if "text" in part else "data"


def _keep(task: dict) -> None:
    TASKS[task["id"]] = task
    while len(TASKS) > KEPT_TASKS:
        TASKS.popitem(last=False)


# ---- writing the task, in the client's version --------------------------------------------------------

STATES = {"completed": "TASK_STATE_COMPLETED", "rejected": "TASK_STATE_REJECTED"}


def render_task(task: dict, version: str, history_length: Any = None) -> dict[str, Any]:
    v1 = version == "1.0"
    status: dict[str, Any] = {"state": STATES[task["state"]] if v1 else task["state"], "timestamp": task["timestamp"]}
    if task["reason"]:
        status["message"] = _message({"message_id": uuid.uuid5(uuid.NAMESPACE_URL, task["id"]).hex, "role": "agent",
                                      "parts": [{"text": task["reason"]}]}, task, v1)
    out: dict[str, Any] = {"id": task["id"], "contextId": task["context_id"], "status": status,
                           "artifacts": [{"artifactId": a["artifact_id"], "name": a["name"],
                                          "description": a["description"],
                                          "parts": [_part(p, v1) for p in a["parts"]]} for a in task["artifacts"]],
                           "metadata": {"skill": task["skill"]}}
    history = task["history"]
    if isinstance(history_length, int) and not isinstance(history_length, bool) and history_length >= 0:
        history = history[-history_length:] if history_length else []
    if history:
        out["history"] = [_message(m, task, v1) for m in history]
    if not v1:
        out["kind"] = "task"
    return out


def _message(message: dict, task: dict, v1: bool) -> dict[str, Any]:
    out = {"messageId": message["message_id"], "contextId": task["context_id"], "taskId": task["id"],
           "role": f"ROLE_{message['role'].upper()}" if v1 else message["role"],
           "parts": [_part(p, v1) for p in message["parts"]]}
    if not v1:
        out["kind"] = "message"
    return out


def _part(part: dict, v1: bool) -> dict[str, Any]:
    if "text" in part:
        return {"text": part["text"]} if v1 else {"kind": "text", "text": part["text"]}
    return {"data": part["data"], "mediaType": "application/json"} if v1 else {"kind": "data", "data": part["data"]}
