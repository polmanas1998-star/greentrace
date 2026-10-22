"""Entry point shared by run.py and the API."""
import uuid
from pathlib import Path
from typing import Any, Optional, TextIO

from app import orchestrator
from app.tracing import trace


def new_run_id() -> str:
    return uuid.uuid4().hex[:16]


def run_case(
    case: dict[str, Any],
    *,
    log_dir: Optional[Path] = None,
    stream: Optional[TextIO] = None,
    memory_engine: Any = None,
) -> dict[str, Any]:
    if not isinstance(case, dict):
        raise ValueError("input must be a JSON object")
    run_id = orchestrator.checked_run_id(case.get("run_id")) or new_run_id()
    return orchestrator.investigate(
        case, run_id,
        log=lambda record: trace(record, log_dir=log_dir, stream=stream),
        memory_engine=memory_engine, log_dir=log_dir, stream=stream,
    )
