"""OpenTelemetry: one trace per investigation, one span per GreenTrace trace line.

app/tracing.py calls record() after it has written the official line; nothing here changes that
line or that file. Each span is named <agent_name>.<action> and carries the line's 13 fields as
attributes, prefixed "greentrace." (a field that is null in the line is an empty value). Its parent
is the caller: the latest line in which another agent handed work to this one (target_agent); an
agent nobody handed work to (the Pipeline that receives the case, a what_if) starts a root span.

Times: a line is written when its step ends, so a span ends at the line's timestamp. A step with a
caller starts where the run's previous line ended (the pipeline runs one step at a time); a root
span lasts no time, as nothing earlier in the run belongs to it.

The trace id is derived from the run_id (sha256), so every line of a run, a later what_if included,
falls in the same trace, and anyone holding the run_id finds it. Span ids are random.

Exporters: always logs/<run_id>.otel.jsonl (FileSpanExporter, below), one span per line, written
before the trace line returns; OTLP over HTTP as well, only when OTEL_EXPORTER_OTLP_ENDPOINT is set
(the exporter reads the standard OTEL_EXPORTER_OTLP_* variables). GREENTRACE_OTEL=0 turns it all off.
A failure here never stops an investigation: it is logged once and the run goes on.
"""
import hashlib
import json
import logging
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from opentelemetry import context as otel_context
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.id_generator import IdGenerator, RandomIdGenerator
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import NonRecordingSpan, SpanContext, SpanKind, Status, StatusCode

from app.standards import VERSION

PREFIX = "greentrace."
FIELDS = ("timestamp", "run_id", "agent_name", "action", "input_summary", "output_summary", "target_agent",
          "confidence", "retry_count", "status", "provider", "model", "rows")  # app/tracing.py's, in its order
SPAN_KEYS = ("span_id", "parent_span_id", "name", "start", "end", "attributes")
KEPT_RUNS = 1000  # runs whose callers are remembered; an older run's next line starts a root span
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
log = logging.getLogger(__name__)


def enabled() -> bool:
    return os.environ.get("GREENTRACE_OTEL", "1").strip().lower() not in ("0", "false", "no", "off")


def trace_id(run_id: str) -> str:
    """32 hex characters, the same for every line of a run."""
    return hashlib.sha256(f"greentrace-run:{run_id}".encode()).hexdigest()[:32]


def record(line: dict[str, Any], directory: Path) -> None:
    """One span for one validated trace line of app/tracing.py; never raises."""
    if not enabled():
        return
    try:
        RECORDER.record(line, directory)
    except Exception as error:  # noqa: BLE001 - telemetry must never stop an investigation
        if not RECORDER.warned:
            RECORDER.warned = True
            log.warning("OpenTelemetry span not recorded (%s: %s); the trace lines are unaffected",
                        type(error).__name__, error)


def read(run_id: str, directory: Path) -> Optional[list[dict[str, Any]]]:
    """The spans of a run as logs/<run_id>.otel.jsonl holds them (without the trace id), or None."""
    path = directory / f"{run_id}.otel.jsonl"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as handle:
        return [{key: span[key] for key in SPAN_KEYS} for span in map(json.loads, filter(str.strip, handle))]


# ---- the recorder ----------------------------------------------------------------------------------

@dataclass
class _Run:
    directory: Path
    last_end: Optional[int] = None                               # ns, the run's previous line
    callers: dict[str, SpanContext] = field(default_factory=dict)  # agent -> the span that handed it work


class _RunTraceIds(IdGenerator):
    """Random span ids; the trace id of the run being recorded for its root spans."""

    def __init__(self) -> None:
        self.random = RandomIdGenerator()
        self.current = threading.local()

    def generate_span_id(self) -> int:
        return self.random.generate_span_id()

    def generate_trace_id(self) -> int:
        return self.current.trace_id


class Recorder:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.runs: "OrderedDict[str, _Run]" = OrderedDict()
        self.provider: Optional[TracerProvider] = None
        self._tracer: Optional[otel_trace.Tracer] = None
        self.ids = _RunTraceIds()
        self.warned = False

    def tracer(self) -> otel_trace.Tracer:
        """Built on first use: a private provider (the global one, which the MCP SDK also uses, is left alone)."""
        if self.provider is None:
            provider = TracerProvider(sampler=ALWAYS_ON, id_generator=self.ids, resource=Resource.create(
                {"service.name": "greentrace", "service.version": VERSION}))
            provider.add_span_processor(SimpleSpanProcessor(FileSpanExporter(self)))
            if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip():
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            self.provider, self._tracer = provider, provider.get_tracer("greentrace", VERSION)
        return self._tracer

    def record(self, line: dict[str, Any], directory: Path) -> None:
        run_id, agent, end = line["run_id"], line["agent_name"], nanoseconds(line["timestamp"])
        with self.lock:
            run = self.runs.get(run_id)
            if run is None:
                run = self.runs[run_id] = _Run(directory)
                while len(self.runs) > KEPT_RUNS:
                    self.runs.popitem(last=False)
            run.directory = directory
            self.runs.move_to_end(run_id)
            caller = run.callers.get(agent)
            start = end if caller is None or run.last_end is None else min(run.last_end, end)
            parent = otel_context.Context() if caller is None else \
                otel_trace.set_span_in_context(NonRecordingSpan(caller), otel_context.Context())
            self.ids.current.trace_id = int(trace_id(run_id), 16)
            span = self.tracer().start_span(f"{agent}.{line['action']}", context=parent, kind=SpanKind.INTERNAL,
                                            attributes={PREFIX + k: line[k] for k in FIELDS}, start_time=start)
            if line["status"] == "error":
                span.set_status(Status(StatusCode.ERROR, line["output_summary"]))
            span.end(end_time=end)  # the file exporter writes it now
            if line["target_agent"] and line["target_agent"] != agent:
                run.callers[line["target_agent"]] = span.get_span_context()
            run.last_end = end

    def directory_of(self, run_id: str) -> Optional[Path]:
        run = self.runs.get(run_id)
        return run.directory if run else None

    def shutdown(self) -> None:
        """Flush and stop the exporters; the next line builds them again (tests change the environment)."""
        with self.lock:
            if self.provider is not None:
                self.provider.shutdown()
            self.provider, self._tracer, self.warned = None, None, False
            self.runs.clear()


class FileSpanExporter(SpanExporter):
    """Appends each span to logs/<run_id>.otel.jsonl, beside the run's trace lines."""

    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        for span in spans:
            run_id = (span.attributes or {}).get(PREFIX + "run_id")
            directory = self.recorder.directory_of(run_id) if isinstance(run_id, str) else None
            if directory is None:
                continue
            directory.mkdir(parents=True, exist_ok=True)
            with open(directory / f"{run_id}.otel.jsonl", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(as_json(span), ensure_ascii=False) + "\n")
        return SpanExportResult.SUCCESS


def as_json(span: ReadableSpan) -> dict[str, Any]:
    return {
        "trace_id": f"{span.context.trace_id:032x}",
        "span_id": f"{span.context.span_id:016x}",
        "parent_span_id": f"{span.parent.span_id:016x}" if span.parent else None,
        "name": span.name,
        "start": iso(span.start_time),
        "end": iso(span.end_time),
        "attributes": dict(span.attributes or {}),
    }


def nanoseconds(timestamp: str) -> int:
    """A trace line's timestamp ("2026-10-24T12:01:16.160Z") in ns since the epoch, exactly."""
    moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return (moment - EPOCH) // timedelta(microseconds=1) * 1000


def iso(ns: Optional[int]) -> Optional[str]:
    """The trace lines' own format, to the millisecond."""
    if ns is None:
        return None
    seconds, rest = divmod(ns, 10**9)
    moment = datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=rest // 1000)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


RECORDER = Recorder()
