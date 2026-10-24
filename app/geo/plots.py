"""Which plot an /api/plots/... question is about: a demo plot, a plot of the sample registry, or a GeoJSON.

  P-041, P-042            the two real plots near Soubre (app/web/maps.py): a square of the declared area
  P-001 ... P-040         the synthetic registry of the sample world (app/world/base.py), near Daloa
  a GeoJSON               a Polygon, or a Point with properties.area_ha, read by app/world/drawn.py

A plot is the row the rules read: plot_id, lat, lon, polygon_wkt ("" for a point), area_ha.
"""
from functools import lru_cache
from typing import Any

from app.web.maps import DRAWN_PLOT_ID, PLOTS as DEMO_PLOTS
from app.world import drawn
from app.world.base import build_base_world


class UnknownPlot(KeyError):
    """No plot with this id."""


@lru_cache(maxsize=1)
def _registry() -> dict[str, dict[str, Any]]:
    return {p["plot_id"]: p for p in build_base_world()["plots"]}


def known_ids() -> list[str]:
    return sorted(DEMO_PLOTS) + sorted(_registry())


def by_id(plot_id: str) -> dict[str, Any]:
    if plot_id in DEMO_PLOTS:
        demo = DEMO_PLOTS[plot_id]
        return {"plot_id": demo.plot_id, "lat": demo.lat, "lon": demo.lon, "area_ha": demo.area_ha,
                "polygon_wkt": demo.polygon_wkt, "source": f"demo plot ({demo.role})"}
    if plot_id in _registry():
        row = _registry()[plot_id]
        return {k: row[k] for k in ("plot_id", "lat", "lon", "area_ha", "polygon_wkt")} | {
            "source": "sample registry (synthetic world near Daloa)"}
    raise UnknownPlot(f"no plot {plot_id!r}: known plots are P-041, P-042 (real, near Soubre) and "
                      f"P-001 to P-040 (sample registry), or POST a GeoJSON")


def from_geojson(value: Any) -> dict[str, Any]:
    """A drawn or uploaded plot; without properties.plot_id it is called P-900, the drawn plot's id."""
    if isinstance(value, dict) and value.get("type") == "FeatureCollection":
        features = value.get("features") or []
        if len(features) != 1:
            raise ValueError("one plot at a time: a FeatureCollection of exactly one Feature")
        value = features[0]
    if isinstance(value, dict):
        properties = dict(value.get("properties") or {}) if isinstance(value.get("properties") or {}, dict) else None
        if properties is not None and "plot_id" not in properties:
            properties["plot_id"] = DRAWN_PLOT_ID
            value = {**value, "properties": properties}
    row = drawn.plot_from_geojson(value)
    return {k: row[k] for k in ("plot_id", "lat", "lon", "area_ha", "polygon_wkt")} | {"source": "GeoJSON"}
