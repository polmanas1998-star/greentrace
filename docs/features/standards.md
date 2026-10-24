# GreenTrace for other agents: MCP, A2A, OpenTelemetry

GreenTrace can be called by other agents through two open protocols, and every investigation can be
exported as an OpenTelemetry trace. Nothing here decides a verdict: the same fixed rules
(`app/rules.py`) decide it, whichever door the request comes through. Code: `app/standards/`.

## The five operations

The MCP tools and the A2A skills are the same five functions (`app/standards/tools.py`). Each one
calls the code the API already runs. Nothing is reimplemented.

| Operation | Arguments | What it calls |
|---|---|---|
| `check_claim` | `claim`, `company`?, `case`? (sample mode only, 1 to 21) | the pipeline, as `POST /run` |
| `check_plots` | `geojson` (object or JSON text), `promise`?, `documents`? | the `POST /api/check-plots` route |
| `what_if` | `run_id`, `entity` (`alert:A-221`, `plot:P-014`), `field`, `value` | `orchestrator.what_if`, as `POST /runs/{id}/what_if` |
| `explain` | `run_id` | the plain sentence (`app/web/plain.py`) and each finding with the rows it cites |
| `verify_receipt` | `receipt_json` (object or JSON text) | `verify.check`, as `python verify.py`: `VALID`, `TAMPERED` or `UNREADABLE` |

A run is kept in the memory of the process that made it (the last 50 runs). `what_if` and `explain`
only answer for runs made by the same server. If a run is unknown, the tool says so: it does not
guess.

## MCP server

The server uses the official Python SDK (`mcp==2.3.0`). It also offers one resource template,
`greentrace://runs/{run_id}/receipt`, so an MCP client can fetch a receipt and pass it to
`verify_receipt` without using the REST API.

**stdio** (Claude Desktop, Cursor, Claude Code). Run from the repository root:

```bash
python -m app.standards.mcp_server
```

Over stdio, stdout carries only the protocol and the trace lines go to stderr. A client config
block (Claude Desktop: `claude_desktop_config.json`; Cursor: `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "greentrace": {
      "command": "/absolute/path/to/greentrace/.venv/bin/python",
      "args": ["-m", "app.standards.mcp_server"],
      "env": {"PYTHONPATH": "/absolute/path/to/greentrace", "SAMPLE_MODE": "true"}
    }
  }
}
```

On Windows, `command` is `C:\\path\\to\\greentrace\\.venv\\Scripts\\python.exe`. With Claude Code:
`claude mcp add greentrace -- python -m app.standards.mcp_server` (from the repository root).

**Streamable HTTP**, inside the API (`python run.py`, or the Docker image), at `/mcp`. It is
stateless and answers in JSON:

```bash
claude mcp add --transport http greentrace http://localhost:8000/mcp
curl -s localhost:8000/mcp -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"check_claim","arguments":{"claim":"100% of our cocoa comes from deforestation-free farms since 2021."}}}'
```

A browser request (one that sends an `Origin` header) is refused with a 403 unless the origin is
local, or is listed in `GREENTRACE_MCP_ALLOWED_ORIGINS` (comma-separated). This is the DNS rebinding
protection the MCP specification asks for. Clients that are not browsers send no `Origin` and are not
affected.

## A2A agent

This follows the A2A specification **1.0** (the current release) and still answers **0.3**
clients. Checked against the official `a2a-sdk` clients 1.2.3 and 0.3.26.

- Agent card: `GET /.well-known/agent-card.json`, also served at `/.well-known/agent.json`. It lists
  the five operations as skills, with `capabilities.streaming` and `pushNotifications` set to false.
  The card has `supportedInterfaces` for 1.0 clients, and `url`, `preferredTransport` and
  `protocolVersion` for 0.3 clients. Behind a proxy, set `GREENTRACE_PUBLIC_URL` (for example
  `https://your.host`) so the card gives the public URL.
- JSON-RPC at `POST /a2a`. The version comes from the `A2A-Version` header (or query parameter):
  - With `A2A-Version: 1.0`: the methods are `SendMessage` and `GetTask`, and the JSON uses
    `TASK_STATE_COMPLETED`, `ROLE_AGENT` and `{"text": ...}` parts.
  - With no header, or `0.3`: the methods are `message/send` and `tasks/get`, and the JSON uses
    `completed`, `agent` and `{"kind": "text", ...}` parts.
- What a message runs: plain text is a claim to check. To run another skill, send a data part that
  names it with its arguments, for example `{"skill": "what_if", "run_id": "...", "entity": "alert:A-221",
  "field": "alert_date", "value": "2020-12-31"}`.
- What comes back: a finished task. Its artifact holds one line of text (for example
  `CONTRADICTED: This promise is false: ...`) and the operation's full JSON. If GreenTrace refuses an
  input (for example an unknown run), the task is `rejected` and its status message says why.
- Errors are standard JSON-RPC errors: -32700, -32600, -32601 (the message names the right version
  when you call the other version's method), -32602, plus the A2A ones: -32001, -32003, -32004,
  -32005, -32009 (each with a `google.rpc.ErrorInfo`).

```bash
curl -s localhost:8000/a2a -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"message/send",
  "params":{"message":{"kind":"message","role":"user","messageId":"m1","parts":[{"kind":"text",
  "text":"100% of our cocoa comes from deforestation-free farms since 2021."}]}}}'
```

## OpenTelemetry

Each investigation is one trace, and each GreenTrace trace line is one span (`app/standards/otel.py`,
called from `app/tracing.py` after the official line is written; the official `.jsonl` file does not
change).

- **Span names and attributes.** A span is named `<agent_name>.<action>`. Its attributes are the
  line's 13 fields with the prefix `greentrace.` (a field that is null in the line is an empty
  value).
- **Parent.** A span's parent is the calling agent: the latest line in which another agent handed
  work to this one (`target_agent`). Lines from an agent nobody handed work to (the Pipeline
  receiving the case, a `what_if`) are root spans.
- **Times.** A trace line is written when its step ends, so a span ends at the line's timestamp and
  starts where the run's previous line ended. A root span lasts no time.
- **Trace id.** It is derived from the run_id (sha256), so a later `what_if` lands in the same
  trace.
- **Where the spans go.**
  - Always to `logs/<run_id>.otel.jsonl`, one span per line, written by GreenTrace's own file
    exporter.
  - To OTLP/HTTP only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (for example
    `http://localhost:4318`; the standard `OTEL_EXPORTER_OTLP_*` variables apply).
- **Turning it off.** `GREENTRACE_OTEL=0` turns all of this off. A failure in the telemetry is
  logged once and never stops an investigation.
- **`GET /runs/{run_id}/otel`** returns:

```json
{"run_id": "...", "trace_id": "32 hex", "spans": [{"span_id": "16 hex", "parent_span_id": null,
  "name": "Pipeline.receive_case", "start": "ISO", "end": "ISO", "attributes": {"greentrace.agent_name": "Pipeline"}}]}
```

## Honest limits

- **One direction only.** MCP and A2A expose GreenTrace to other agents. GreenTrace itself does not
  call other agents, over A2A or otherwise.
- **No authentication.** The `/mcp` and `/a2a` endpoints have none, like the rest of the API. Do not
  expose live mode (with real keys) publicly as is.
- **Runs live in memory.** A server restart forgets them, and the stdio and HTTP servers are two
  processes that do not share runs. A2A tasks are kept the same way (the last 50).
- **Synchronous A2A.** There is no streaming, no push notifications and no multi-turn task. Every
  task is finished in one message.
- **Approximate span times.** Span start times are derived from consecutive trace lines (the
  timestamps have millisecond precision). They are not measured inside each step.
- **What was tested.** The MCP server was tested with the SDK's own client (in memory, stdio,
  HTTP). It was not tried here inside the Claude Desktop or Cursor apps themselves.
- **Image size.** The Docker image grows from 588 MB to 637 MB (measured on `integration/24-10` and on
  this branch). 16 MB of that is `cryptography`, which the MCP SDK requires.
