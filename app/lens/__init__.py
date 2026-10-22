"""Satellite Lens: Nemotron Vision looks at the Sentinel-2 scene, the code checks every cell it names.

The scene is the real Sentinel-2 frame behind a demo plot, at two dates: before the cut-off
(<plot>_2020.jpg, dry season 2019-2020) and now (<plot>.jpg). The model gets ONE image composed by
scripts/fetch_satellite.py (<plot>_lens.jpg): before | after side by side, 1024 px wide, the same
3 x 3 grid on each half (columns A to C from west to east, rows 1 to 3 from north to south) with the
cell names written small in the corners. It says, cell by cell, whether forest seen in 2020 is gone
in the recent image, in strict JSON. On the recent scene alone a clearing of 2021 has grown back:
the first real test (22/10) gave nine "cannot see".

Since the second real test (22/10: "forest_kept" for all nine cells at 512 px per date) the lens
asks one question per cell first: the cell cropped at both dates and enlarged (bicubic only, by
scripts/fetch_satellite.py: <plot>_lens_A1.jpg ... _C3.jpg), one request at a time. If a cell gets
no answer (timeout, rate limit, invalid JSON), the whole grid falls back to the one-image method
above, and the result says so.

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
import time
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
CELL_PAUSE_S = 1.0  # between two cell questions: nine requests in a row met NVIDIA's rate limit on 22/10
MAX_TOKENS = 2500  # the spec's floor: with 700 the reasoning ate the whole answer (probe of 21/10)
NEEDS_MODEL = "AI lens needs a model"
NEEDS_SCENE = "lens needs a scene for this area"
NEEDS_BEFORE = "lens needs the 2020 scene: python -m scripts.fetch_satellite --before"
CONFIRMED, REJECTED, MISSED, NOT_SEEN = "CONFIRMED", "REJECTED", "MISSED BY AI", "NOT SEEN"
TRACE_STATUS = {CONFIRMED: "confirmed", REJECTED: "rejected", MISSED: "missed", NOT_SEEN: "not_seen"}


LOOK_FOR = (
    "What to compare: closed forest canopy looks dark green with a rough, irregular, clumpy texture; a clearing, "
    "a young crop or bare soil looks lighter and smoother, often with straight edges or geometric shapes."
)


def cell_prompt(cell: str, before_date: str, after_date: str) -> str:
    return (
        f"The image shows one ground cell, {cell}, about 400 m wide and 267 m high, north up, in the cocoa belt of "
        "Cote d'Ivoire, twice: LEFT, Sentinel-2 true colour of " + before_date + ", before the EU cut-off of "
        "31 December 2020; RIGHT, the same cell on " + after_date + ". Each side is enlarged from about 10 m per "
        "pixel, so it is soft: judge shapes and tones, not details. " + LOOK_FOR + " Answer for the whole cell: "
        "forest_lost (closed forest on the left has been replaced on the right by lighter, smoother ground), "
        "forest_kept (closed forest on the left is still closed forest on the right), no_forest_in_2020 (the left "
        "shows no closed forest), or cannot_see (cloud, haze, or too blurred to judge on either side). "
        "Give a confidence from 0 to 1."
    )


CELL_SCHEMA = {
    "type": "object", "required": ["label", "confidence"], "additionalProperties": False,
    "properties": {"label": {"type": "string", "enum": list(LABELS)},
                   "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
}


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
    composite: Optional[Path] = None   # before | after with the grid: the one-image method
    composite_meta: Optional[dict] = None
    cells: Optional[dict] = None       # cell -> the pair image of that cell: the method asked first
    cells_meta: Optional[dict] = None

    @property
    def frame(self) -> dict:
        return self.meta["frame"]

    @property
    def fingerprint(self) -> str:
        """Plot and both dates: the same images of the same place give the same key."""
        parts = [self.plot_id, self.meta.get("jpg_sha256") or hashlib.sha256(self.image.read_bytes()).hexdigest(),
                 (self.composite_meta or {}).get("jpg_sha256") or "no-2020-scene",
                 hashlib.sha256(json.dumps(self.cells_meta or {}, sort_keys=True).encode()).hexdigest()]
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
        cells, cells_meta = _cells(directory, plot_id) if before else (None, None)
        found[plot_id] = Scene(plot_id, image, meta, before, before_meta, composite, composite_meta, cells, cells_meta)
    return found


def _cells(directory: Path, plot_id: str) -> tuple[Optional[dict], Optional[dict]]:
    """The nine cell pairs, only when every one of them is there."""
    meta_file = directory / f"{plot_id}_lens_cells.json"
    if not meta_file.is_file():
        return None, None
    pairs = {cell: directory / f"{plot_id}_lens_{cell}.jpg" for cell in CELLS}
    if not all(path.is_file() for path in pairs.values()):
        return None, None
    return pairs, json.loads(meta_file.read_text(encoding="utf-8"))


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
         cells_file: Path = landcover.CELLS_FILE, gate_factory: Optional[Callable[[], Any]] = None,
         method: str = "cells", sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Ask the model about the scene (unless there is no model), then check every cell by code.

    method "cells": one question per cell pair, then the one-image grid if a cell gets no answer;
    method "grid": the one-image grid only (scripts/lens_probe.py compares the two).
    """
    log = log or (lambda record: trace(record))
    new_gate = gate_factory or ((lambda: gate) if gate is not None else (lambda: ModelGate(run_id, log=log)))
    measured = maps_by_cell(scene.frame, cells_file)
    first = new_gate()
    answer, info, infos, used, fallback, missing = None, {}, {}, None, None, None
    if first.sample_mode or (scene.cells is None and scene.composite is None):
        missing = NEEDS_MODEL if first.sample_mode else NEEDS_BEFORE  # no call: no model, or one date only
        info = {"provider": "rules", "model": None, "requested_model": None, "reason": missing}
    else:
        if method == "cells" and scene.cells is not None:
            answer, infos, fallback = _ask_cells(scene, first, sleep)
            used = "cells" if answer is not None else None
            info = infos[CELLS[-1]] if answer is not None else {}
        if answer is None and scene.composite is not None:
            gate_now = first if used is None and fallback is None else new_gate()
            answer, info = gate_now.complete_json(
                "Evaluator", prompt(scene.before_meta["date"], scene.meta["date"]), [], SCHEMA,
                images=[scene.composite.read_bytes()], timeout_s=TIMEOUT_S, max_tokens=MAX_TOKENS)
            used = "grid" if answer is not None else None
            infos = {}
        if answer is None:
            missing = f"no model answer ({info.get('reason') or fallback or 'see the trace'})"
            info = info or {"provider": "rules", "model": None, "requested_model": None}
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
            _line(log, run_id, infos.get(name, info), name, claim, truth, status, method=used)
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
        "ai": ai, "message": None if ai else missing, "method": used, "fallback": fallback if used == "grid" else None,
        "provider": info["provider"], "model": info["model"], "requested_model": info.get("requested_model"),
        "reason": info.get("reason"),
        "loss_from_ha": LOSS_FROM_HA, "cells": cells,
        "score": score(cells) if ai else None,
    }


def _ask_cells(scene: Scene, gate: Any, sleep: Callable[[float], None]) -> tuple[Optional[dict], dict, Optional[str]]:
    """One question per cell pair, one at a time; the first cell without an answer stops it and says why."""
    answers, infos = [], {}
    for index, cell in enumerate(CELLS):
        if index:
            sleep(CELL_PAUSE_S)
        said, info = gate.complete_json(
            "Evaluator", cell_prompt(cell, scene.before_meta["date"], scene.meta["date"]), [], CELL_SCHEMA,
            images=[scene.cells[cell].read_bytes()], timeout_s=TIMEOUT_S, max_tokens=MAX_TOKENS)
        if said is None:
            return None, infos, f"cell {cell}: {info.get('reason') or 'no answer'}; fell back to the one-image grid"
        answers.append({"cell": cell, **said})
        infos[cell] = info
    return {"cells": answers}, infos, None


def _line(log, run_id, info, name, claim, truth, status, missing=None, method=None) -> None:
    if name is None:
        summary_in, summary_out = "9 grid cells, before | after", f"{missing}: the code's grid stands alone"
        status_field, confidence = "degraded", None
    else:
        said = f"{claim['label']} ({claim['confidence']:.2f})" if claim else "no answer"
        summary_in = f"{name}: the model says {said} ({method} method); asked {info.get('requested_model')}"
        summary_out = (f"{status}: Hansen {truth['loss_after_2020_ha']:.2f} ha lost after 2020 in {name} "
                       f"(threshold {LOSS_FROM_HA} ha); answered by {info['model']}")
        status_field, confidence = TRACE_STATUS[status], claim["confidence"] if claim else None
    log({"timestamp": now_utc(), "run_id": run_id, "agent_name": "Evaluator", "action": "check_vision_claim",
         "input_summary": summary_in[:300], "output_summary": summary_out[:300], "target_agent": None,
         "confidence": confidence, "retry_count": 0, "status": status_field,
         "provider": info["provider"], "model": info["model"], "rows": truth["cells"] if truth else None})
