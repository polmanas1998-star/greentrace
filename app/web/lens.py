"""The Satellite Lens for the page: started in the background, read back when it is done.

POST /api/lens        {"plot": "P-042"} or {"lat": .., "lon": ..}: the lens on that scene
GET  /api/lens/{key}  where it stands: running, done (with the grid) or failed

A vision call can take a minute (max 90 s), so it runs in a thread and the page asks again. The
result is cached in this process by fingerprint (plot + scene) and mode, never written to disk:
a model answer is never replayed from a file. Each check is a trace line in logs/lens-<key>.jsonl.
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
_LOCK = threading.Lock()


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
        if job is None or job["status"] == "failed":
            job = JOBS[key] = {"key": key, "status": "running", "plot_id": scene.plot_id, "result": None}
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
        job["result"] = lens.look(scene, f"lens-{job['key']}")
        job["status"] = "done"
    except Exception as error:  # noqa: BLE001 - a crash in the thread must reach the page, not vanish
        LOG.exception("lens %s failed", job["key"])
        job["error"] = f"{type(error).__name__}: {error}"
        job["status"] = "failed"


def _public(job: dict[str, Any]) -> dict[str, Any]:
    return {k: job.get(k) for k in ("key", "status", "plot_id", "result", "error")}
