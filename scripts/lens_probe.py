"""Run the real Satellite Lens on the demo plots and print what the model said next to what the code found.

    python -m scripts.lens_probe                      # P-042 and P-041, one question per cell, then the grid
    python -m scripts.lens_probe --plot P-042 --method cells
    python -m scripts.lens_probe --pause 20           # seconds between two runs (rate limit)

Needs a model: SAMPLE_MODE=false and the keys of .env (MODEL_PROVIDER, MODEL_API_KEY or the
Meterless Runtime). It calls exactly what the page calls (app.lens.look), one run at a time, and
prints, for each plot and method, the 3 x 3 grid, the table cell by cell, the score and how long it
took. Every check also goes to logs/probe-<plot>-<method>.jsonl. Nothing is saved for replay: each
run asks the model again.
"""
import argparse
import io
import os
import sys
import time
from typing import Any, Callable

from dotenv import load_dotenv

from app import lens
from app.tracing import trace

PLOTS = ("P-042", "P-041")
METHODS = ("cells", "grid")
MARK = {lens.CONFIRMED: "ok", lens.REJECTED: "WRONG", lens.MISSED: "MISSED", lens.NOT_SEEN: "--", None: "?"}


def quiet_log(record: dict) -> None:
    """Into logs/<run_id>.jsonl only: the console keeps the tables."""
    trace(record, stream=io.StringIO())


def report(result: dict[str, Any], seconds: float) -> str:
    """The printed page of one run: header, 3 x 3 grid, table, score."""
    lines = [f"{result['plot_id']} · {result.get('method') or 'no'} method · {result['provider']} "
             f"{result['model'] or ''} (asked {result['requested_model']}) · {seconds:.1f} s"]
    if result.get("fallback"):
        lines.append(f"  fallback: {result['fallback']}")
    if not result["ai"]:
        lines.append(f"  no AI grid: {result['message']}")
    cells = {c["cell"]: c for c in result["cells"]}
    for row in "123":
        lines.append("  " + "  ".join(f"{column}{row} {MARK[cells[column + row]['status']]:<6}" for column in "ABC"))
    lines.append(f"  {'cell':<5}{'Hansen after 2020':<22}{'AI says':<26}code")
    for c in result["cells"]:
        truth = f"{c['loss_after_2020_ha']:.2f} ha{' LOST' if c['lost'] else ''}"
        said = f"{c['label']} {c['confidence']:.2f}" if c["label"] else "no answer"
        lines.append(f"  {c['cell']:<5}{truth:<22}{said:<26}{c['status'] or '-'}")
    if result["score"]:
        s = result["score"]
        lines.append(f"  AI right {s['right']} / seen {s['seen']} · missed {s['missed']} · wrong {s['wrong']}")
    lines.append(f"  trace: logs/{result['run_id']}.jsonl")
    return "\n".join(lines)


def probe(plot_id: str, method: str, look: Callable[..., dict] = lens.look) -> tuple[dict, float]:
    scene = lens.scene_for(plot_id)
    if scene is None:
        raise SystemExit(f"{plot_id}: no scene in app/web/static/maps (run python -m scripts.fetch_satellite)")
    started = time.monotonic()
    result = look(scene, f"probe-{plot_id}-{method}", log=quiet_log, method=method)
    return result, time.monotonic() - started


def main(argv: list[str]) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plot", choices=PLOTS, action="append", help="one plot (default: both)")
    parser.add_argument("--method", choices=METHODS, action="append", help="one method (default: both)")
    parser.add_argument("--pause", type=float, default=10.0, help="seconds between two runs (default 10)")
    args = parser.parse_args(argv)
    if os.environ.get("SAMPLE_MODE", "").strip().lower() in ("1", "true", "yes", "on"):
        print("SAMPLE_MODE is true: the lens asks no model. Set SAMPLE_MODE=false (and the model keys) to probe.")
        return 2
    runs = [(plot, method) for plot in args.plot or PLOTS for method in args.method or METHODS]
    for index, (plot_id, method) in enumerate(runs):
        if index:
            time.sleep(args.pause)
        result, seconds = probe(plot_id, method)
        print(report(result, seconds))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
