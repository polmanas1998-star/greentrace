"""Live runs for the interface: the trace line by line, as Server-Sent Events.

POST /runs                    starts one investigation in a thread, answers {run_id} at once
GET  /runs/{run_id}/events    every trace line as it is written, then the evidence file

run_case() already writes each trace line to a stream (run.py gives it stderr). Here
the stream is a LiveRun: it keeps the run's lines in order and wakes every reader.
Lines are kept, not consumed, so a page that reconnects (EventSource sends
Last-Event-ID) or a second tab gets every line. The orchestrator and the pipeline
are not changed.
"""
import json
import logging
import threading
from collections import OrderedDict
from typing import Any, Iterator, Optional

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse

from app import orchestrator
from app.pipeline import new_run_id, run_case

HEARTBEAT_SECONDS = 15  # an SSE comment line, so idle proxies keep the connection open
LOG = logging.getLogger(__name__)

router = APIRouter()


class LiveRun:
    """The stream one run writes its trace to (app.tracing.trace calls write, then flush)."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.lines: list[str] = []
        self.output: Optional[dict[str, Any]] = None
        self.error: Optional[str] = None
        self.done = False
        self._partial = ""
        self._changed = threading.Condition()

    def write(self, text: str) -> int:
        with self._changed:
            self._partial += text
            *complete, self._partial = self._partial.split("\n")
            self.lines.extend(line for line in complete if line.strip())
            self._changed.notify_all()
        return len(text)

    def flush(self) -> None:
        """Nothing to do: write() publishes each line as soon as it is complete."""

    def finish(self, output: Optional[dict[str, Any]] = None, error: Optional[str] = None) -> None:
        with self._changed:
            self.output, self.error, self.done = output, error, True
            self._changed.notify_all()

    def events(self, start: int = 0) -> Iterator[str]:
        """SSE text: one `trace` event per line from `start`, then one `result` (or `failed`) event.

        Not `error`: EventSource fires that name for its own connection errors.
        """
        sent = start
        while True:
            with self._changed:
                if sent >= len(self.lines) and not self.done:
                    self._changed.wait(HEARTBEAT_SECONDS)
                fresh, done = self.lines[sent:], self.done
            for line in fresh:
                yield f"id: {sent}\nevent: trace\ndata: {line}\n\n"
                sent += 1
            if done:
                final = ("result", self.output) if self.error is None else ("failed", {"error": self.error})
                yield f"event: {final[0]}\ndata: {json.dumps(final[1], ensure_ascii=False)}\n\n"
                return
            if not fresh:
                yield ": still running\n\n"


LIVE: "OrderedDict[str, LiveRun]" = OrderedDict()
_LIVE_LOCK = threading.Lock()


def _work(live: LiveRun, case: dict[str, Any]) -> None:
    try:
        output = run_case(case, stream=live)
    except ValueError as error:  # a bad request: the same message POST /run answers with a 422
        live.finish(error=str(error))
    except Exception as error:  # noqa: BLE001 - a crash in the thread must reach the page, not vanish
        LOG.exception("run %s failed", live.run_id)
        live.finish(error=f"{type(error).__name__}: {error}")
    else:
        live.finish(output=output)


@router.post("/runs", status_code=202)
def start(case: Any = Body(...)) -> dict[str, str]:
    if not isinstance(case, dict):
        raise HTTPException(status_code=422, detail="input must be a JSON object")
    try:
        run_id = orchestrator.checked_run_id(case.get("run_id")) or new_run_id()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    with _LIVE_LOCK:
        if run_id in LIVE or run_id in orchestrator.RUNS:
            raise HTTPException(status_code=409, detail=f"run {run_id} already exists")
        live = LIVE[run_id] = LiveRun(run_id)
        while len(LIVE) > orchestrator.KEPT_RUNS:
            LIVE.popitem(last=False)
    threading.Thread(target=_work, args=(live, {**case, "run_id": run_id}), name=f"run-{run_id}",
                     daemon=True).start()
    return {"run_id": run_id, "events": f"/runs/{run_id}/events"}


@router.get("/runs/{run_id}/events")
def events(run_id: str, request: Request) -> StreamingResponse:
    live = LIVE.get(run_id)
    if live is None:
        raise HTTPException(status_code=404, detail=f"no live run {run_id} in this process")
    last = request.headers.get("last-event-id", "")
    start = int(last) + 1 if last.isdigit() else 0
    return StreamingResponse(live.events(start), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
