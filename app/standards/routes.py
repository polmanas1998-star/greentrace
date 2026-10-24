"""The HTTP side of app/standards, in one router: app.include_router(routes.router) in app/api.py.

POST|GET|DELETE /mcp                 the MCP server, streamable HTTP (app/standards/mcp_server.py)
GET  /.well-known/agent-card.json    the A2A agent card (also /.well-known/agent.json)
POST /a2a                            the A2A agent, JSON-RPC (app/standards/a2a.py)

The router's lifespan starts the MCP session manager with the API (FastAPI merges it into the app's).
"""
from fastapi import APIRouter

from app.standards import a2a, mcp_server

router = APIRouter(lifespan=mcp_server.lifespan)
router.include_router(a2a.router)
for path in ("/mcp", "/mcp/"):
    router.add_route(path, mcp_server.Endpoint(), methods=["GET", "POST", "DELETE"], include_in_schema=False)
