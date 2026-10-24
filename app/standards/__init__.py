"""GreenTrace for other agents: an MCP server, an A2A agent, OpenTelemetry spans.

tools.py       the five operations both protocols expose, each calling the real code
mcp_server.py  the MCP server: stdio (python -m app.standards.mcp_server) and HTTP at /mcp
a2a.py         the A2A agent: its card at /.well-known/agent-card.json, JSON-RPC at POST /a2a
otel.py        one OpenTelemetry span per trace line, logs/<run_id>.otel.jsonl, GET /runs/{run_id}/otel
routes.py      what app/api.py includes: the HTTP side of the three

Nothing here decides a verdict: the rules do (app/rules.py), as for every other route.
"""

VERSION = "0.1.0"  # the API's version (app/api.py), in the MCP handshake, the agent card and the spans
