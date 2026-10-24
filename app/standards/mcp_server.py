"""GreenTrace as an MCP server (Model Context Protocol, official Python SDK `mcp`).

    python -m app.standards.mcp_server      stdio, for Claude Desktop, Cursor and other MCP clients
    POST /mcp                               streamable HTTP, mounted in the API (app/standards/routes.py)

Five tools, each one calling the real code through app/standards/tools.py: check_claim, check_plots,
what_if, explain, verify_receipt. One resource template: greentrace://runs/{run_id}/receipt, the
receipt verify_receipt takes. A run lives in the process that made it, so what_if, explain and the
receipt answer for runs made through the same server (stdio and HTTP are two processes).

HTTP is stateless and answers in JSON. A browser request (one that sends Origin) is refused unless
its origin is local or listed in GREENTRACE_MCP_ALLOWED_ORIGINS: the DNS rebinding protection the
MCP specification asks for. Clients that are not browsers send no Origin and are not affected.
"""
import os
import sys
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncIterator, Callable, Optional, TextIO

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app.canonical import canonical_json
from app.standards import tools

INSTRUCTIONS = (
    "GreenTrace checks a company's deforestation-free claim against the evidence. Fixed rules decide the "
    "verdict (PROVEN, CONTRADICTED or UNVERIFIABLE), never the model; the same data always gives the same "
    "verdict. Start with check_claim, then explain or what_if with its run_id. Every verdict needs a human "
    "review before any decision."
)
LOCAL_ORIGINS = ("http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*")
READS = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
RUNS = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)


def build(stream: Optional[TextIO] = None) -> MCPServer:
    """The server and its five tools. stream: where the trace lines are echoed (stderr for stdio)."""
    server = MCPServer(name="greentrace", title="GreenTrace", version=tools.VERSION, instructions=INSTRUCTIONS,
                       website_url="https://github.com/polmanas1998-star/greentrace")

    @server.tool(description=tools.DESCRIPTIONS["check_claim"], annotations=RUNS)
    def check_claim(
        claim: Annotated[str, Field(description="The claim, in the company's words")],
        company: Annotated[Optional[str], Field(description="Company id; default chocodurable")] = None,
        case: Annotated[Optional[int], Field(description="Sample mode only: which synthetic world, 1 to 21 "
                                                         "(default 2)")] = None,
    ) -> dict[str, Any]:
        return _call(tools.check_claim, claim, company, case, stream=stream)

    @server.tool(description=tools.DESCRIPTIONS["check_plots"], annotations=RUNS)
    def check_plots(
        geojson: Annotated[dict[str, Any] | str, Field(description="GeoJSON FeatureCollection of the plots, "
                                                                   "as an object or its JSON text")],
        promise: Annotated[Optional[str], Field(description="Optional: the promise to judge with the plots")] = None,
        documents: Annotated[Optional[list[dict[str, Any]]],
                             Field(description="Optional: [{name, issuer, text}] read for the promise")] = None,
    ) -> dict[str, Any]:
        return _call(tools.check_plots, geojson, promise, documents)

    @server.tool(description=tools.DESCRIPTIONS["what_if"], annotations=RUNS)
    def what_if(
        run_id: Annotated[str, Field(description="run_id returned by check_claim")],
        entity: Annotated[str, Field(description="alert:<alert_id> or plot:<plot_id>, e.g. alert:A-221")],
        field: Annotated[str, Field(description="alert_date, confidence, area_ha, lat or lon")],
        value: Annotated[str | float, Field(description="New value: YYYY-MM-DD, low/high/highest, or a number")],
    ) -> dict[str, Any]:
        return _call(tools.what_if, run_id, entity, field, value)

    @server.tool(description=tools.DESCRIPTIONS["explain"], annotations=READS)
    def explain(run_id: Annotated[str, Field(description="run_id returned by check_claim")]) -> dict[str, Any]:
        return _call(tools.explain, run_id)

    @server.tool(description=tools.DESCRIPTIONS["verify_receipt"], annotations=READS)
    def verify_receipt(
        receipt_json: Annotated[dict[str, Any] | str, Field(description="The receipt, as an object or its JSON "
                                                                        "text (resource greentrace://runs/"
                                                                        "{run_id}/receipt)")],
    ) -> dict[str, Any]:
        return _call(tools.verify_receipt, receipt_json)

    @server.resource("greentrace://runs/{run_id}/receipt", name="receipt", mime_type="application/json",
                     description="The receipt of a run of this server, as GET /runs/{run_id}/receipt gives it")
    def receipt(run_id: str) -> str:
        try:
            return canonical_json(tools.receipt(run_id))
        except tools.Refused as error:
            raise ResourceNotFoundError(str(error)) from error

    return server


def _call(operation: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
    """A refusal the caller can fix comes back as the tool's error text; anything else is a crash."""
    try:
        return operation(*args, **kwargs)
    except tools.Refused as error:
        raise ToolError(str(error)) from error


# ---- streamable HTTP inside the API ----------------------------------------------------------------

SERVER = build()


@asynccontextmanager
async def lifespan(_app: Any) -> AsyncIterator[None]:
    """One session manager per lifespan of the API (a manager runs once), entered with the app's lifespan."""
    SERVER.streamable_http_app(stateless_http=True, json_response=True,
                               transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    async with SERVER.session_manager.run():
        Endpoint.running = True
        try:
            yield
        finally:
            Endpoint.running = False


class Endpoint:
    """The ASGI app behind /mcp: the origin check, then the SDK's session manager."""
    running = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not Endpoint.running:
            await JSONResponse({"detail": "MCP over HTTP starts with the API's lifespan: serve the API with "
                                          "python run.py (uvicorn), or use stdio: python -m "
                                          "app.standards.mcp_server"}, status_code=503)(scope, receive, send)
            return
        origin = Headers(scope=scope).get("origin")
        if origin is not None and not allowed_origin(origin):
            await JSONResponse({"detail": f"MCP over HTTP refuses the browser origin {origin!r} (DNS rebinding "
                                          f"protection). Allowed: {', '.join(origins())}; add yours to "
                                          f"GREENTRACE_MCP_ALLOWED_ORIGINS (comma-separated)."},
                               status_code=403)(scope, receive, send)
            return
        await SERVER.session_manager.handle_request(scope, receive, send)


def origins() -> list[str]:
    extra = os.environ.get("GREENTRACE_MCP_ALLOWED_ORIGINS", "")
    return [*LOCAL_ORIGINS, *(o.strip().rstrip("/") for o in extra.split(",") if o.strip())]


def allowed_origin(origin: str) -> bool:
    """Exact match, or "scheme://host:*" for that host on any port or none (the SDK's own pattern)."""
    origin = origin.rstrip("/")
    return any(origin == allowed or (allowed.endswith(":*") and (origin == allowed[:-2] or (
        origin.startswith(allowed[:-1]) and origin[len(allowed) - 1:].isdigit()))) for allowed in origins())


def main() -> None:
    load_dotenv()  # keys and SAMPLE_MODE from .env, as run.py does
    build(stream=sys.stderr).run("stdio")  # stdout carries the protocol: the trace lines go to stderr


if __name__ == "__main__":
    main()
