"""Satellite Lens: Nemotron Vision looks at the Sentinel-2 scene, the code checks every cell it names.

The scene is the real Sentinel-2 frame behind a demo plot, at two dates: before the cut-off
(<plot>_2020.jpg, dry season 2019-2020) and now (<plot>.jpg). The model gets ONE image composed by
scripts/fetch_satellite.py (<plot>_lens.jpg): before | after side by side, 1024 px wide, the same
3 x 3 grid on each half (columns A to C from west to east, rows 1 to 3 from north to south) with the
cell names written small in the corners. It says, cell by cell, whether forest seen in 2020 is gone
in the recent image, in strict JSON. On the recent scene alone a clearing of 2021 has grown back:
the first real test (22/10) gave nine "cannot see".

The code then reads the Hansen/UMD cells of data/reference_cells.csv.gz inside each grid cell and
decides, cell by cell:
  CONFIRMED     the model and the maps agree (forest lost after 2020, or not)
  REJECTED      the model says forest was lost where the maps show no loss after 2020: wrong
  MISSED BY AI  the maps show loss after 2020 where the model, seeing the cell, said none
  NOT SEEN      the model could not see the cell, or gave no answer: neither for nor against it
Each check is one trace line, Evaluator check_vision_claim, with the model asked and the model that
answered. Nothing here reaches app/rules.py: the lens explains, it never decides a verdict. In
SAMPLE_MODE no model is called ("AI lens needs a model") and the code's grid stands alone; no
saved model answer is ever replayed. Without the 2020 scene the model is not asked either: one
date cannot show a change.
"""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from app.adapters.llm import ModelGate
from app.checks import landcover
from app.tracing import now_utc, trace

SCENES_DIR = Path(__file__).resolve().parents[1] / "web" / "static" / "maps"
COLUMNS, ROWS = "ABC", "123"
CELLS = tuple(f"{c}{r}" for r in ROWS for c in COLUMNS)  # A1 B1 C1 / A2 B2 C2 / A3 B3 C3
LABELS = ("forest_lost", "forest_kept", "no_forest_in_2020", "cannot_see")
LOST = "forest_lost"  # the one label that claims forest seen in 2020 is gone
UNSEEN = "cannot_see"
LOSS_FROM_HA = 0.5  # Hansen loss after 2020 in a grid cell from which the code calls it a loss
AFTER_YEAR = landcover.CUTOFF_YEAR  # loss strictly after 2020
TIMEOUT_S = 90.0  # one vision call; one retry at most, as every model call (app/adapters/llm.py)
MAX_TOKENS = 2500  # the spec's floor: with 700 the reasoning ate the whole answer (probe of 21/10)
NEEDS_MODEL = "AI lens needs a model"
NEEDS_SCENE = "lens needs a scene for this area"
NEEDS_BEFORE = "lens needs the 2020 scene: python -m scripts.fetch_satellite --before"
CONFIRMED, REJECTED, MISSED, NOT_SEEN = "CONFIRMED", "REJECTED", "MISSED BY AI", "NOT SEEN"
TRACE_STATUS = {CONFIRMED: "confirmed", REJECTED: "rejected", MISSED: "missed", NOT_SEEN: "not_seen"}


def prompt(before_date: str, after_date: str) -> str:
    return (
        "The image shows the same place twice, side by side, north up, about 1200 m wide and 800 m high, in the "
        f"cocoa belt of Cote d'Ivoire. LEFT half: Sentinel-2 true colour of {before_date}, before the EU cut-off "
        f"of 31 December 2020. RIGHT half: the same frame on {after_date}. Both halves carry the same 3 x 3 grid; "
        "each cell's name is written small in its top-left corner: columns A, B, C from left to right, rows 1, 2, 3 "
        "from top to bottom. For each of the nine cells, compare left and right and answer: forest_lost (closed "
        "forest canopy on the left is gone on the right: bare or burnt ground, new fields, young plantation rows), "
        "forest_kept (forest on the left is still forest on the right), no_forest_in_2020 (no closed forest on the "
        "left: farms, plantations, settlements, bare ground), or cannot_see (cloud, haze or too little detail on "
        "either side). Give a confidence from 0 to 1. Judge only what the two images show."
    )
SCHEMA = {
    "type": "object", "required": ["cells"], "additionalProperties": False,
    "properties": {"cells": {"type": "array", "minItems": 9, "maxItems": 9, "items": {
        "type": "object", "required": ["cell", "label", "confidence"], "additionalProperties": False,
        "properties": {"cell": {"type": "string", "enum": list(CELLS)},
                       "label": {"type": "string", "enum": list(LABELS)},
                       "confidence": {"type": "number", "minimum": 0, "maximum": 1}}}}},
}


@dataclass(frozen=True)
class Scene:
    plot_id: str
    image: Path               # the recent scene, behind the map
    meta: dict
    before: Optional[Path] = None      # the 2020 scene, when fetched
    before_meta: Optional[dict] = None
    composite: Optional[Path] = None   # before | after with the grid: what the model sees
    composite_meta: Optional[dict] = None

    @property
    def frame(self) -> dict:
        return self.meta["frame"]

    @property
    def fingerprint(self) -> str:
        """Plot and both dates: the same images of the same place give the same key."""
        parts = [self.plot_id, self.meta.get("jpg_sha256") or hashlib.sha256(self.image.read_bytes()).hexdigest(),
                 (self.composite_meta or {}).get("jpg_sha256") or "no-2020-scene"]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _read(directory: Path, name: str) -> tuple[Optional[Path], Optional[dict]]:
    image, meta = directory / f"{name}.jpg", directory / f"{name}.json"
    if image.is_file() and meta.is_file():
        return image, json.loads(meta.read_text(encoding="utf-8"))
    return None, None


def scenes(directory: Path = SCENES_DIR) -> dict[str, Scene]:
    found = {}
    for meta_file in sorted(directory.glob("P-[0-9][0-9][0-9].json")):
        plot_id = meta_file.stem
        image, meta = _read(directory, plot_id)
        if image is None:
            continue
        before, before_meta = _read(directory, f"{plot_id}_2020")
        composite, composite_meta = _read(directory, f"{plot_id}_lens") if before else (None, None)
        found[plot_id] = Scene(plot_id, image, meta, before, before_meta, composite, composite_meta)
    return found


def scene_for(plot_id: Optional[str] = None, lat: Optional[float] = None, lon: Optional[float] = None,
              directory: Path = SCENES_DIR) -> Optional[Scene]:
    """The scene of a demo plot, or the scene whose frame holds the point; None: no scene for this area."""
    known = scenes(directory)
    if plot_id in known:
        return known[plot_id]
    if lat is None or lon is None:
        return None
    for scene in known.values():
        f = scene.frame
        if f["lat_min"] <= lat <= f["lat_max"] and f["lon_min"] <= lon <= f["lon_max"]:
            return scene
    return None


def grid(frame: dict) -> list[dict]:
    """The nine cells, with their bounds in degrees."""
    d_lat = (frame["lat_max"] - frame["lat_min"]) / 3
    d_lon = (frame["lon_max"] - frame["lon_min"]) / 3
    cells = []
    for r, row in enumerate(ROWS):
        for c, column in enumerate(COLUMNS):
            cells.append({"cell": f"{column}{row}",
                          "lat_max": frame["lat_max"] - r * d_lat, "lat_min": frame["lat_max"] - (r + 1) * d_lat,
                          "lon_min": frame["lon_min"] + c * d_lon, "lon_max": frame["lon_min"] + (c + 1) * d_lon})
    return cells


def maps_by_cell(frame: dict, cells_file: Path = landcover.CELLS_FILE) -> dict[str, dict]:
    """Hansen loss after 2020 inside each grid cell, from the committed 30 m cells (counted by their centre)."""
    rows = landcover.read_cells(frame["lat_min"], frame["lat_max"], frame["lon_min"], frame["lon_max"], cells_file)
    found = {g["cell"]: {"loss_after_2020_ha": 0.0, "on_eu_forest_ha": 0.0, "cells": 0} for g in grid(frame)}
    for g in grid(frame):
        totals = found[g["cell"]]
        for row in rows:
            if g["lat_min"] <= row["lat"] < g["lat_max"] and g["lon_min"] <= row["lon"] < g["lon_max"]:
                totals["cells"] += 1
                if row["loss_year"] > AFTER_YEAR:
                    area = landcover.cell_area_ha(row["lat"])
                    totals["loss_after_2020_ha"] += area
                    if row["forest_fraction_2020"] >= landcover.FOREST_FROM:
                        totals["on_eu_forest_ha"] += area
    for totals in found.values():
        totals["loss_after_2020_ha"] = round(totals["loss_after_2020_ha"], 2)
        totals["on_eu_forest_ha"] = round(totals["on_eu_forest_ha"], 2)
        totals["lost"] = totals["loss_after_2020_ha"] >= LOSS_FROM_HA
    return found


def judge(label: Optional[str], lost: bool) -> str:
    """One cell: what the model said against what the maps measured. "I cannot see" is not "I missed"."""
    if label is None or label == UNSEEN:
        return NOT_SEEN
    if label == LOST:
        return CONFIRMED if lost else REJECTED
    return MISSED if lost else CONFIRMED


def score(cells: list[dict]) -> dict[str, int]:
    """AI right a / seen s · missed m · wrong w: the cells it could not see count neither way."""
    statuses = [c["status"] for c in cells]
    return {"right": statuses.count(CONFIRMED), "seen": len(cells) - statuses.count(NOT_SEEN),
            "missed": statuses.count(MISSED), "wrong": statuses.count(REJECTED), "of": len(cells)}


def look(scene: Scene, run_id: str, *, gate: Optional[Any] = None, log: Optional[Callable[[dict], Any]] = None,
         cells_file: Path = landcover.CELLS_FILE) -> dict[str, Any]:
    """Ask the model about the scene (unless there is no model), then check every cell by code."""
    log = log or (lambda record: trace(record))
    gate = gate or ModelGate(run_id, log=log)
    measured = maps_by_cell(scene.frame, cells_file)
    if scene.composite is not None:
        answer, info = gate.complete_json(
            "Evaluator", prompt(scene.before_meta["date"], scene.meta["date"]), [], SCHEMA,
            images=[scene.composite.read_bytes()], timeout_s=TIMEOUT_S, max_tokens=MAX_TOKENS)
        missing = None if answer is not None else NEEDS_MODEL if gate.sample_mode else (
            f"no model answer ({info.get('reason') or 'see the trace'})")
    else:  # one date cannot show a change: the model is not asked
        answer, missing = None, NEEDS_MODEL if gate.sample_mode else NEEDS_BEFORE
        info = {"provider": "rules", "model": None, "requested_model": None, "reason": missing}
    said = {}
    if answer is not None:
        for item in answer["cells"]:
            said.setdefault(item["cell"], item)  # a cell named twice: the first answer counts
    ai = answer is not None
    cells = []
    for name in CELLS:
        claim = said.get(name)
        truth = measured[name]
        status = judge(claim["label"] if claim else None, truth["lost"]) if ai else None
        cells.append({"cell": name, "label": claim["label"] if claim else None,
                      "confidence": claim["confidence"] if claim else None, "status": status, **truth})
        if ai:
            _line(log, run_id, info, name, claim, truth, status)
    if not ai:
        _line(log, run_id, info, None, None, None, None, missing)
    pick = ("scene_id", "date", "licence")
    return {
        "plot_id": scene.plot_id, "scene": {k: scene.meta.get(k) for k in pick},
        "before": {k: scene.before_meta.get(k) for k in pick} if scene.before_meta else None,
        "images": {"after": f"/static/maps/{scene.image.name}",
                   "before": f"/static/maps/{scene.before.name}" if scene.before else None,
                   "composite": f"/static/maps/{scene.composite.name}" if scene.composite else None},
        "frame": scene.frame, "fingerprint": scene.fingerprint, "run_id": run_id,
        "ai": ai, "message": None if ai else missing,
        "provider": info["provider"], "model": info["model"], "requested_model": info.get("requested_model"),
        "reason": info.get("reason"),
        "loss_from_ha": LOSS_FROM_HA, "cells": cells,
        "score": score(cells) if ai else None,
    }


def _line(log, run_id, info, name, claim, truth, status, missing=None) -> None:
    if name is None:
        summary_in, summary_out = "9 grid cells, before | after", f"{missing}: the code's grid stands alone"
        status_field, confidence = "degraded", None
    else:
        said = f"{claim['label']} ({claim['confidence']:.2f})" if claim else "no answer"
        summary_in = f"{name}: the model says {said}; asked {info.get('requested_model')}"
        summary_out = (f"{status}: Hansen {truth['loss_after_2020_ha']:.2f} ha lost after 2020 in {name} "
                       f"(threshold {LOSS_FROM_HA} ha); answered by {info['model']}")
        status_field, confidence = TRACE_STATUS[status], claim["confidence"] if claim else None
    log({"timestamp": now_utc(), "run_id": run_id, "agent_name": "Evaluator", "action": "check_vision_claim",
         "input_summary": summary_in[:300], "output_summary": summary_out[:300], "target_agent": None,
         "confidence": confidence, "retry_count": 0, "status": status_field,
         "provider": info["provider"], "model": info["model"], "rows": truth["cells"] if truth else None})
