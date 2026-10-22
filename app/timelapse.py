"""Satellite time machine: one real Sentinel-2 scene a year behind a plot, and what the maps say
was lost that year.

For each year from 2019 to the current one: the least cloudy clean frame of that year's dry
season (December of the year before to March), on the frame the home map draws (1200 x 800 m,
EPSG:4326, 960 x 640 pixels: scripts/fetch_satellite.py), and the Hansen/UMD tree cover loss
of that same year inside the frame, read from data/reference_cells.csv.gz (cells whose centre
lies in the frame; also on the land the EU map counted as forest in 2020).

Where the scenes come from, in this order, and nothing else:
  data/timelapse/          committed, precomputed by python -m scripts.fetch_satellite --timelapse
  data/cache/timelapse/    fetched on demand in live mode (never committed)
  app/web/static/maps/     the 2020 and recent scenes the repository ships for P-042 and P-041
SAMPLE_MODE makes no network call: a missing year says so. In live mode a missing year is
fetched from the public Earth Search catalogue within a time budget (it needs rasterio,
scripts/requirements-maps.txt); a year with no usable scene, or a failed fetch, is written down
and tried again after RETRY_DAYS. No image is ever made up, and nothing here touches a verdict.
"""
import json
import math
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from app.checks import landcover
from app.tracing import now_utc, trace
from scripts import fetch_satellite as sat

ROOT = Path(__file__).resolve().parents[1]
SHIPPED_DIR = ROOT / "data" / "timelapse"
CACHE_DIR = ROOT / "data" / "cache" / "timelapse"
MAPS_DIR = sat.MAPS_DIR
FIRST_YEAR = sat.TIMELAPSE_FIRST_YEAR
HANSEN_LAST_YEAR = 2024  # Hansen v1.12 maps loss from 2001 to 2024 (data/reference_cells.meta.json)
BUDGET_S = 45.0  # live mode: fetching stops after this, the rest is "not fetched yet"
RETRY_DAYS = 7
DRY_MONTHS = (12, 1, 2, 3)  # a shipped scene stands for a year only if it was taken in that dry season
NOT_IN_SAMPLE = ("not in the repository: sample mode makes no network call "
                 "(python -m scripts.fetch_satellite --timelapse precomputes it)")
_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_lock = threading.Lock()  # one fetch at a time: a second visitor waits rather than fetching twice


class OutsideMaps(ValueError):
    """The time machine covers the area of the reference maps only."""


def place(plot: Optional[str], lat: Optional[float], lon: Optional[float]) -> tuple[str, Optional[str], float, float]:
    """(file key, plot id or None, lat, lon) of the frame asked for: a demo plot, or a point in the maps."""
    if plot is not None:
        if plot not in sat.PLOTS:
            raise ValueError(f"no time machine for {plot!r}; plots: {sorted(sat.PLOTS)}, or give lat and lon")
        return plot, plot, *sat.PLOTS[plot]
    if lat is None or lon is None or not (math.isfinite(lat) and math.isfinite(lon)):
        raise ValueError("give plot=P-042, or lat and lon")
    box, extent = sat.frame(lat, lon), landcover.load_meta()["extent"]
    if not (extent["lat_min"] <= box["lat_min"] and box["lat_max"] <= extent["lat_max"]
            and extent["lon_min"] <= box["lon_min"] and box["lon_max"] <= extent["lon_max"]):
        raise OutsideMaps(f"the frame around {lat}, {lon} leaves the reference maps "
                          f"({extent['lat_min']}-{extent['lat_max']} N, {extent['lon_min']}-{extent['lon_max']})")
    for plot_id, centre in sat.PLOTS.items():  # a click on a demo plot's centre is that plot
        if (round(lat, 6), round(lon, 6)) == centre:
            return plot_id, plot_id, lat, lon
    return f"{lat:.5f}_{lon:.5f}", None, round(lat, 6), round(lon, 6)


def season_year(day: str) -> int:
    """The dry season a date belongs to: December counts for the next year's season."""
    when = date.fromisoformat(day[:10])
    return when.year + 1 if when.month == 12 else when.year


def hansen_by_year(box: dict[str, float]) -> dict[int, dict[str, Any]]:
    """year -> loss inside the frame: cells whose centre lies in it, their area, and how much was 2020 forest."""
    cells = landcover.read_cells(box["lat_min"], box["lat_max"], box["lon_min"], box["lon_max"])
    out: dict[int, dict[str, Any]] = {}
    for cell in cells:
        year = cell["loss_year"]
        if not year:
            continue
        entry = out.setdefault(year, {"cells": 0, "ha": 0.0, "ha_on_2020_forest": 0.0})
        area = landcover.cell_area_ha(cell["lat"])
        entry["cells"] += 1
        entry["ha"] += area
        if cell["forest_fraction_2020"] >= landcover.FOREST_FROM:
            entry["ha_on_2020_forest"] += area
    return {y: {**e, "ha": round(e["ha"], 4), "ha_on_2020_forest": round(e["ha_on_2020_forest"], 4)}
            for y, e in out.items()}


def _found(key: str, plot_id: Optional[str], year: int, directories: tuple[Path, ...]) -> Optional[dict]:
    """The scene description of that year, from the first directory that has one, with its image URL."""
    for directory in directories:
        meta = directory / f"{key}_{year}.json"
        if meta.exists():
            info = json.loads(meta.read_text(encoding="utf-8"))
            image = directory / f"{key}_{year}.jpg"
            info["_image"] = image if image.exists() else None
            info["_dir"] = directory
            return info
    if plot_id:  # the scenes the repository ships behind the map: the 2020 one and the recent one
        for name in (f"{plot_id}_2020", plot_id):
            meta = MAPS_DIR / f"{name}.json"
            if meta.exists():
                info = json.loads(meta.read_text(encoding="utf-8"))
                if season_year(info["date"]) == year and int(info["date"][5:7]) in DRY_MONTHS:
                    return {**info, "_image": MAPS_DIR / f"{name}.jpg", "_dir": MAPS_DIR}
    return None


def _url(info: dict) -> Optional[str]:
    image = info.get("_image")
    if image is None:
        return None
    if info["_dir"] == MAPS_DIR:
        return f"/static/maps/{image.name}"
    return f"/api/timelapse/image/{image.name}"


def _stale(info: dict, today: date) -> bool:
    """A year written down as missing is tried again after RETRY_DAYS."""
    made = info.get("made_on")
    return info.get("_image") is None and (not made or date.fromisoformat(made) + timedelta(days=RETRY_DAYS) <= today)


def timelapse(plot: Optional[str] = None, lat: Optional[float] = None, lon: Optional[float] = None, *,
              sample: bool = True, today: Optional[date] = None, fetcher: Optional[Callable] = None,
              cache_dir: Optional[Path] = None, shipped_dir: Optional[Path] = None, budget_s: float = BUDGET_S,
              log: Optional[Callable[[dict], Any]] = None) -> dict[str, Any]:
    key, plot_id, lat, lon = place(plot, lat, lon)
    cache_dir, shipped_dir = cache_dir or CACHE_DIR, shipped_dir or SHIPPED_DIR
    log = log or (lambda record: trace(record, stream=_Null()))
    today = today or datetime.now(timezone.utc).date()
    box = sat.frame(lat, lon)
    hansen = hansen_by_year(box)
    directories = (shipped_dir, cache_dir)
    started, fetched, years = time.monotonic(), 0, []

    for year in range(FIRST_YEAR, today.year + 1):
        info = _found(key, plot_id, year, directories)
        status = "scene" if info and info.get("_image") else (info or {}).get("status")
        if (info is None or _stale(info, today)) and not sample:
            if time.monotonic() - started > budget_s:
                status = f"not fetched yet: the {budget_s:.0f} s budget of this request is spent; ask again"
            else:
                info = _fetch(key, lat, lon, year, cache_dir, today, fetcher, log)
                fetched += 1
                status = "scene" if info.get("_image") else info["status"]
        elif info is None:
            status = NOT_IN_SAMPLE
        years.append(_year(year, info if status == "scene" else None, status, hansen, today))

    with_scene = sum(1 for y in years if y["image"])
    log({"timestamp": now_utc(), "run_id": "timelapse", "agent_name": "Investigator", "action": "timelapse",
         "input_summary": f"{plot_id or key}: frame {box['lat_min']}-{box['lat_max']} N, "
                          f"{box['lon_min']}-{box['lon_max']}", "target_agent": None, "confidence": None,
         "output_summary": f"{with_scene} of {len(years)} year(s) with a scene ({fetched} fetched now); "
                           f"Hansen loss in {len(hansen)} year(s) of the frame", "retry_count": 0,
         "status": "success", "provider": "code" if sample else "earth-search", "model": None, "rows": None})
    return {
        "key": key, "plot": plot_id, "centre": [lat, lon], "frame": box, "pixels": list(sat.MAP_PX),
        "mode": "sample" if sample else "live",
        "hansen": {"source": "Hansen/UMD/Google/USGS/NASA GFC v1.12, data/reference_cells.csv.gz",
                   "counted": "cells of 30 m whose centre lies in the frame", "last_year": HANSEN_LAST_YEAR},
        "years": years,
    }


def _year(year: int, info: Optional[dict], status: str, hansen: dict, today: date) -> dict[str, Any]:
    season = sat.dry_season(year)
    if year > HANSEN_LAST_YEAR:
        loss, note = None, f"Hansen v1.12 maps loss up to {HANSEN_LAST_YEAR}"
    else:
        loss = hansen.get(year, {"cells": 0, "ha": 0.0, "ha_on_2020_forest": 0.0})
        note = None
    return {
        "year": year,
        "season": season["label"] + (" (still running)" if year == today.year and today.month <= 3 else ""),
        "image": _url(info) if info else None,
        "scene": {k: info.get(k) for k in ("scene_id", "date", "cloud_cover_scene_percent",
                                           "frame_bad_pixels_share", "licence")} if info else None,
        "status": "scene" if info else status,
        "hansen_loss": loss,
        "hansen_note": note,
    }


def _fetch(key: str, lat: float, lon: float, year: int, cache_dir: Path, today: date,
           fetcher: Optional[Callable], log: Callable[[dict], Any]) -> dict:
    """One year, from the public catalogue, into the cache; a failure is written down, never invented around."""
    name = f"{key}_{year}"
    with _lock:
        try:
            fetch = fetcher or sat.fetch_scene
            fetch(key, lat, lon, sat.dry_season(year), cache_dir, name, quiet=True)
            info = json.loads((cache_dir / f"{name}.json").read_text(encoding="utf-8"))
            info.update(_image=cache_dir / f"{name}.jpg", _dir=cache_dir)
            outcome, status = f"{info['scene_id']} of {info['date']}", "success"
        except ImportError:
            info = {"status": "on-demand fetching needs rasterio (pip install -r scripts/requirements-maps.txt)",
                    "_image": None}
            outcome, status = info["status"], "degraded"
        except Exception as error:  # noqa: BLE001 - network, catalogue or raster failures: written down, retried later
            reason = "no usable scene" if isinstance(error, sat.SatelliteError) else "fetch failed"
            info = {"year": year, "status": f"{reason}: {str(error)[:200]}", "made_on": today.isoformat(),
                    "_image": None}
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / f"{name}.jpg").unlink(missing_ok=True)
            (cache_dir / f"{name}.json").write_text(json.dumps({k: v for k, v in info.items() if k != "_image"},
                                                               indent=2) + "\n", encoding="utf-8")
            outcome, status = info["status"], "degraded"
    log({"timestamp": now_utc(), "run_id": "timelapse", "agent_name": "Investigator", "action": "fetch_scene",
         "input_summary": f"{key} {sat.dry_season(year)['label']}", "output_summary": outcome[:300],
         "target_agent": None, "confidence": None, "retry_count": 0, "status": status,
         "provider": "earth-search", "model": None, "rows": None})
    return info


def image_path(name: str) -> Optional[Path]:
    """A time-machine JPEG by file name, only from the two time-machine folders."""
    if not _NAME.match(name) or not name.endswith(".jpg") or ".." in name:
        return None
    for directory in (SHIPPED_DIR, CACHE_DIR):
        path = directory / name
        if path.is_file():
            return path
    return None


class _Null:
    """The time machine's trace lines go to logs/timelapse.jsonl, not to the server's console."""

    def write(self, _: str) -> int:
        return 0

    def flush(self) -> None:
        pass
