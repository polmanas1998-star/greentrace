"""Trace lines: one JSON object per agent step, in logs/<run_id>.jsonl and on a stream.

The first ten fields are the ones the official Technical Execution Requirements
list; provider, model and rows are ours (which model route answered, which
Zetaris rows were read). Every line carries every field, None when it does not
apply: a missing or unknown field raises instead of writing a partial line.
"""
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, TextIO

from app.canonical import canonical_json
from app.standards import otel

OFFICIAL_FIELDS = (
    "timestamp",
    "run_id",
    "agent_name",
    "action",
    "input_summary",
    "output_summary",
    "target_agent",
    "confidence",
    "retry_count",
    "status",
)
EXTRA_FIELDS = ("provider", "model", "rows")
FIELDS = OFFICIAL_FIELDS + EXTRA_FIELDS

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

# run_id becomes a file name: keep it to a safe alphabet so it can never point outside logs/.
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def trace(
    record: dict[str, Any],
    *,
    log_dir: Optional[Path] = None,
    stream: Optional[TextIO] = None,
) -> str:
    """Validate one trace record, append it to logs/<run_id>.jsonl, echo it, return the line."""
    keys = set(record)
    missing = [field for field in FIELDS if field not in keys]
    unknown = sorted(keys - set(FIELDS))
    if missing or unknown:
        raise ValueError(f"trace record: missing {missing}, unknown {unknown}")

    run_id = record["run_id"]
    if not isinstance(run_id, str) or not _RUN_ID.match(run_id):
        raise ValueError(f"trace record: run_id must match {_RUN_ID.pattern}, got {run_id!r}")

    line = canonical_json(record)
    directory = log_dir if log_dir is not None else LOG_DIR
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / f"{run_id}.jsonl", "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    otel.record(record, directory)  # one OpenTelemetry span per line (app/standards/otel.py); GREENTRACE_OTEL=0: off

    out = stream if stream is not None else sys.stdout
    out.write(line + "\n")
    out.flush()
    return line
