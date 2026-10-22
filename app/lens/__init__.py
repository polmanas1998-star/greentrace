"""Satellite Lens: Nemotron Vision looks at the Sentinel-2 scene, the code checks every cell it names.

The scene is the real Sentinel-2 frame behind a demo plot (app/web/static/maps/<plot>.jpg and its
.json: date, frame, licence). A 3 x 3 grid covers the frame: columns A to C from west to east, rows
1 to 3 from north to south. The model sees the image as it is, with no grid or mark drawn on it,
and answers strict JSON: one label and one confidence per cell.

The code then reads the Hansen/UMD cells of data/reference_cells.csv.gz inside each grid cell and
decides, cell by cell:
  CONFIRMED     the model and the maps agree (recent clearing seen and lost after 2020, or not)
  REJECTED      the model saw recent clearing where the maps show no loss after 2020: wrong
  MISSED BY AI  the maps show loss after 2020 where the model saw none
  NO CLAIM      the model could not see the cell, or gave no answer for it
Each check is one trace line, Evaluator check_vision_claim, with the model asked and the model that
answered. Nothing here reaches app/rules.py: the lens explains, it never decides a verdict. In
SAMPLE_MODE no model is called ("AI lens needs a model") and the code's grid stands alone; no
saved model answer is ever replayed.
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
LABELS = ("recent_clearing", "plantation", "bare_soil", "intact_forest", "cannot_see")
CLEARING = "recent_clearing"  # the one label that claims tree cover lost recently
LOSS_FROM_HA = 0.5  # Hansen loss after 2020 in a grid cell from which the code calls it a loss
AFTER_YEAR = landcover.CUTOFF_YEAR  # loss strictly after 2020
TIMEOUT_S = 90.0  # one vision call; one retry at most, as every model call (app/adapters/llm.py)
MAX_TOKENS = 1024  # nine short objects; thinking is off on NVIDIA (llm.NO_THINKING)
NEEDS_MODEL = "AI lens needs a model"
NEEDS_SCENE = "lens needs a scene for this area"
CONFIRMED, REJECTED, MISSED, NO_CLAIM = "CONFIRMED", "REJECTED", "MISSED BY AI", "NO CLAIM"

PROMPT = (
    "You are looking at one Sentinel-2 true-colour image, north up, about 1200 m wide and 800 m high, "
    "in the cocoa belt of Cote d'Ivoire. Split it in your mind into a 3 x 3 grid: columns A, B, C from "
    "left to right, rows 1, 2, 3 from top to bottom (A1 is the top-left ninth, C3 the bottom-right). "
    "For each of the nine cells, say what covers most of it now: recent_clearing (forest cut in the last "
    "few years: bare or burnt ground with stumps, fresh straight edges), plantation (rows of tree crops "
    "such as cocoa or rubber), bare_soil (old bare ground, roads, settlements), intact_forest (closed "
    "natural canopy), or cannot_see (cloud, haze or too little detail). Give a confidence from 0 to 1. "
    "Judge only what the image shows; do not guess from place names."
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
    image: Path
    meta: dict

    @property
    def frame(self) -> dict:
        return self.meta["frame"]

    @property
    def fingerprint(self) -> str:
        """Plot and scene: the same image of the same place gives the same key."""
        return hashlib.sha256(f"{self.plot_id}|{self.meta.get('jpg_sha256') or self.image.read_bytes()}".encode()).hexdigest()


def scenes(directory: Path = SCENES_DIR) -> dict[str, Scene]:
    found = {}
    for meta_file in sorted(directory.glob("P-*.json")):
        image = meta_file.with_suffix(".jpg")
        if image.is_file():
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            found[meta_file.stem] = Scene(meta_file.stem, image, meta)
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
    """One cell: what the model said against what the maps measured."""
    if label is None or label == "cannot_see":
        return MISSED if lost else NO_CLAIM
    if label == CLEARING:
        return CONFIRMED if lost else REJECTED
    return MISSED if lost else CONFIRMED


def look(scene: Scene, run_id: str, *, gate: Optional[Any] = None, log: Optional[Callable[[dict], Any]] = None,
         cells_file: Path = landcover.CELLS_FILE) -> dict[str, Any]:
    """Ask the model about the scene (unless there is no model), then check every cell by code."""
    log = log or (lambda record: trace(record))
    gate = gate or ModelGate(run_id, log=log)
    measured = maps_by_cell(scene.frame, cells_file)
    answer, info = gate.complete_json("Evaluator", PROMPT, [], SCHEMA, images=[scene.image.read_bytes()],
                                      timeout_s=TIMEOUT_S, max_tokens=MAX_TOKENS)
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
        _line(log, run_id, info, None, None, None, None)
    confirmed = sum(c["status"] == CONFIRMED for c in cells)
    corrected = sum(c["status"] in (REJECTED, MISSED) for c in cells)
    return {
        "plot_id": scene.plot_id, "scene": {k: scene.meta.get(k) for k in ("scene_id", "date", "licence")},
        "frame": scene.frame, "fingerprint": scene.fingerprint, "run_id": run_id,
        "ai": ai, "message": None if ai else NEEDS_MODEL,
        "provider": info["provider"], "model": info["model"], "requested_model": info.get("requested_model"),
        "reason": info.get("reason"),
        "loss_from_ha": LOSS_FROM_HA, "cells": cells,
        "score": {"ai_confirmed": confirmed, "code_corrected": corrected, "of": len(CELLS)} if ai else None,
    }


def _line(log, run_id, info, name, claim, truth, status) -> None:
    if name is None:
        summary_in, summary_out = "9 grid cells of the Sentinel-2 scene", f"{NEEDS_MODEL}: the code's grid stands alone"
        status_field, confidence = "degraded", None
    else:
        said = f"{claim['label']} ({claim['confidence']:.2f})" if claim else "no answer"
        summary_in = f"{name}: the model says {said}; asked {info.get('requested_model')}"
        summary_out = (f"{status}: Hansen {truth['loss_after_2020_ha']:.2f} ha lost after 2020 in {name} "
                       f"(threshold {LOSS_FROM_HA} ha); answered by {info['model']}")
        status_field, confidence = "success", claim["confidence"] if claim else None
    log({"timestamp": now_utc(), "run_id": run_id, "agent_name": "Evaluator", "action": "check_vision_claim",
         "input_summary": summary_in[:300], "output_summary": summary_out[:300], "target_agent": None,
         "confidence": confidence, "retry_count": 0, "status": status_field,
         "provider": info["provider"], "model": info["model"], "rows": truth["cells"] if truth else None})
