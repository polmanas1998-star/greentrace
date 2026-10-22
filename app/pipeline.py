"""Entry point shared by run.py and the API. Agents come in the next tasks."""
import uuid
from pathlib import Path
from typing import Any, Optional, TextIO

from app.tracing import now_utc, trace


def new_run_id() -> str:
    return uuid.uuid4().hex[:16]


def run_case(
    case: dict[str, Any],
    *,
    log_dir: Optional[Path] = None,
    stream: Optional[TextIO] = None,
) -> dict[str, Any]:
    if not isinstance(case, dict):
        raise ValueError("input must be a JSON object")

    run_id = new_run_id()
    trace(
        {
            "timestamp": now_utc(),
            "run_id": run_id,
            "agent_name": "Pipeline",
            "action": "receive_case",
            "input_summary": f"{len(case)} top-level field(s): {', '.join(sorted(case)) or 'none'}",
            "output_summary": "agents not implemented yet",
            "target_agent": None,
            "confidence": None,
            "retry_count": 0,
            "status": "not_implemented",
            "provider": None,
            "model": None,
            "rows": None,
        },
        log_dir=log_dir,
        stream=stream,
    )
    return {"run_id": run_id, "status": "not_implemented"}
