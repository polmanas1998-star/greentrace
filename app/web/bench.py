"""Reliability tests: the 21 trap cases, run one at a time so the page fills its table as it goes.

POST /ui/bench/{case}   one trap case in sample mode, rules alone: expected, got, ok, seal

Same input as scripts/bench.py run_all(): the default promise, sample mode, no model, the
in-memory World Model. tests/test_web.py checks that both give the same verdict for every
case. Nothing is stored or scored in advance: the page counts the rows it got back.
"""
import io
from typing import Any

from fastapi import APIRouter, HTTPException

from app.memory.fallback import MemoryEngine
from app.pipeline import new_run_id, run_case
from app.world.cases import CASES_BY_NUMBER, DEFAULT_CLAIM

router = APIRouter(include_in_schema=False)


@router.post("/ui/bench/{case}")
def bench_case(case: int) -> dict[str, Any]:
    trap = CASES_BY_NUMBER.get(case)
    if trap is None:
        raise HTTPException(status_code=404, detail=f"no trap case {case}; cases are {min(CASES_BY_NUMBER)}"
                                                    f" to {max(CASES_BY_NUMBER)}")
    run_id = f"bench-{case:02d}-{new_run_id()[:8]}"
    out = run_case({"run_id": run_id, "input": {"claim": DEFAULT_CLAIM},
                    "options": {"sample_mode": True, "case": case, "use_model": False}},
                   stream=io.StringIO(), memory_engine=MemoryEngine())
    return {
        "case": case, "slug": trap.slug, "change": trap.change, "expected": trap.expected,
        "got": out.get("verdict"), "ok": out.get("verdict") == trap.expected, "status": out["status"],
        "verdict_sha256": out.get("verdict_sha256"), "degraded": out["degraded"], "mode": out["mode"],
        "run_id": run_id, "execution_time_seconds": out["execution_time_seconds"],
    }
