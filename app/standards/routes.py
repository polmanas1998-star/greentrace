"""The HTTP side of app/standards, in one router: app.include_router(routes.router) in app/api.py.

POST|GET|DELETE /mcp                 the MCP server, streamable HTTP (app/standards/mcp_server.py)
GET  /.well-known/agent-card.json    the A2A agent card (also /.well-known/agent.json)
POST /a2a                            the A2A agent, JSON-RPC (app/standards/a2a.py)
GET  /runs/{run_id}/otel             the run's OpenTelemetry spans, from logs/<run_id>.otel.jsonl (app/standards/otel.py)

The router's lifespan starts the MCP session manager with the API (FastAPI merges it into the app's).
"""
from typing import Any

from fastapi import APIRouter, HTTPException

from app import orchestrator, tracing
from app.standards import a2a, mcp_server, otel

router = APIRouter(lifespan=mcp_server.lifespan)
router.include_router(a2a.router)
for path in ("/mcp", "/mcp/"):
    router.add_route(path, mcp_server.Endpoint(), methods=["GET", "POST", "DELETE"], include_in_schema=False)


@router.get("/runs/{run_id}/otel")
def otel_spans(run_id: str) -> dict[str, Any]:
    """{"run_id", "trace_id": 32 hex, "spans": [{span_id, parent_span_id, name, start, end, attributes}]}."""
    try:
        orchestrator.checked_run_id(run_id)  # it becomes a file name: the same safe alphabet as the trace
    except ValueError as error:
        raise HTTPException(status_code=404, detail=f"no run {run_id!r}: {error}") from error
    spans = otel.read(run_id, tracing.LOG_DIR)
    if spans is None:
        why = "OpenTelemetry is off (GREENTRACE_OTEL=0)" if not otel.enabled() else "no such run in logs/"
        raise HTTPException(status_code=404, detail=f"no OpenTelemetry spans for run {run_id}: {why}")
    return {"run_id": run_id, "trace_id": otel.trace_id(run_id), "spans": spans}
