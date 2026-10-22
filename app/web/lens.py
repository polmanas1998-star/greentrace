"""The Satellite Lens for the page: started in the background, read back when it is done.

POST /api/lens        {"plot": "P-042"} or {"lat": .., "lon": ..}: the lens on that scene
GET  /api/lens/{key}  where it stands: running, done (with the grid) or failed

A vision call can take a minute (max 90 s), so it runs in a thread and the page asks again. One
call at a time: a second lens waits ("queued") instead of hitting the provider's rate limit (on
22/10 P-041, asked right after P-042, got "no model provider answered"). The result is cached in
this process by fingerprint (plot + both dates) and mode, never written to disk: a model answer is
never replayed from a file, and an answer the model did not give is not cached, so asking again
asks again. Each check is a trace line in logs/lens-<key>.jsonl.
"""
import logging
import os
import threading
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException

from app import lens

LOG = logging.getLogger(__name__)
router = APIRouter(include_in_schema=False)
JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()  # guards JOBS
_ONE_CALL = threading.Lock()  # one vision call at a time


def _mode() -> str:
    return "sample" if os.environ.get("SAMPLE_MODE", "true").lower() in ("1", "true", "yes") else "model"


@router.post("/api/lens")
def start(plot: Optional[str] = Body(None, max_length=16), lat: Optional[float] = Body(None),
          lon: Optional[float] = Body(None)) -> dict[str, Any]:
    scene = lens.scene_for(plot, lat, lon)
    if scene is None:
        return {"status": "no_scene", "message": lens.NEEDS_SCENE}
    key = f"{scene.fingerprint[:16]}-{_mode()}"
    with _LOCK:
        job = JOBS.get(key)
        if job is None or job["status"] == "failed" or (job["status"] == "done" and not job["keep"]):
            job = JOBS[key] = {"key": key, "status": "queued", "plot_id": scene.plot_id, "result": None, "keep": False}
            threading.Thread(target=_work, args=(job, scene), name=f"lens-{key}", daemon=True).start()
    return _public(job)


@router.get("/api/lens/{key}")
def read(key: str) -> dict[str, Any]:
    job = JOBS.get(key)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no lens {key} in this process")
    return _public(job)


def _work(job: dict[str, Any], scene: lens.Scene) -> None:
    try:
        with _ONE_CALL:
            job["status"] = "running"
            result = lens.look(scene, f"lens-{job['key']}")
        # Keep what can be kept: a model answer, or the plain fact that this mode or this scene has no model call.
        job["keep"] = result["ai"] or result["message"] in (lens.NEEDS_MODEL, lens.NEEDS_BEFORE)
        job["result"] = result
        job["status"] = "done"
    except Exception as error:  # noqa: BLE001 - a crash in the thread must reach the page, not vanish
        LOG.exception("lens %s failed", job["key"])
        job["error"] = f"{type(error).__name__}: {error}"
        job["status"] = "failed"


def _public(job: dict[str, Any]) -> dict[str, Any]:
    return {k: job.get(k) for k in ("key", "status", "plot_id", "result", "error")}
