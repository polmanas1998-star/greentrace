"""The plot the analyst (or a judge) draws: one more delivering plot of supplier F-007 (kit, C4).

It comes in as input.plot, in GeoJSON (a Polygon, or a Point with properties.area_ha), in
longitude/latitude, optionally with properties.plot_id (default P-041). It is a
declaration, not a row of the registry: the data gate stays read-only, and the receipt
keeps the GeoJSON so verify.py rebuilds the same plot. The plot delivers 1.5 t a year
from 2021 to the fiscal year, and the company's declared volume grows by as much, so the
coverage does not change. From then on it is judged like any other plot, and the
reference maps (query Q10) measure what was lost inside it.
"""
import math
import re
from typing import Any

from shapely.geometry import Polygon, shape
from shapely.validation import explain_validity

from app.world import geo

DEFAULT_PLOT_ID, SUPPLIER_ID, TONNES_PER_YEAR, FIRST_YEAR = "P-041", "F-007", 1.5, 2021
DECLARED_BY = "analyst"
MAX_VERTICES, MIN_HA, MAX_HA = 500, 0.05, 500.0
_PLOT_ID = re.compile(r"^P-\d{3}$")


def plot_from_geojson(value: Any) -> dict[str, Any]:
    """The plot row for a drawn GeoJSON geometry or Feature; a clear ValueError for anything else."""
    if not isinstance(value, dict):
        raise ValueError("input.plot must be a GeoJSON object (Polygon, Point, or a Feature of one)")
    properties = value.get("properties") or {}
    if not isinstance(properties, dict):
        raise ValueError("input.plot: properties must be an object")
    plot_id = properties.get("plot_id", DEFAULT_PLOT_ID)
    if not isinstance(plot_id, str) or not _PLOT_ID.match(plot_id):
        raise ValueError("input.plot: properties.plot_id looks like P-041")
    geometry = value.get("geometry") if value.get("type") == "Feature" else value
    if not isinstance(geometry, dict) or geometry.get("type") not in ("Polygon", "Point"):
        raise ValueError("input.plot: a Polygon, or a Point with properties.area_ha")
    coordinates = geometry.get("coordinates")

    if geometry["type"] == "Point":
        lon, lat = _position(coordinates)
        area = properties.get("area_ha")
        if isinstance(area, bool) or not isinstance(area, (int, float)) or not MIN_HA <= area <= MAX_HA:
            raise ValueError(f"input.plot: a Point needs properties.area_ha between {MIN_HA} and {MAX_HA}")
        return _row(plot_id, lat, lon, "", float(area))

    if not isinstance(coordinates, list) or len(coordinates) != 1 or not isinstance(coordinates[0], list):
        raise ValueError("input.plot: one outer ring, no holes")
    ring = [_position(p) for p in coordinates[0]]
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    if not 4 <= len(ring) <= MAX_VERTICES:
        raise ValueError(f"input.plot: a ring of 4 to {MAX_VERTICES} positions")
    polygon = shape({"type": "Polygon", "coordinates": [ring]})
    if not polygon.is_valid:
        raise ValueError(f"input.plot: invalid polygon ({explain_validity(polygon)})")
    area = area_ha(polygon)
    if not MIN_HA <= area <= MAX_HA:
        raise ValueError(f"input.plot: {area:.2f} ha, outside {MIN_HA} to {MAX_HA} ha")
    wkt = "POLYGON((" + ", ".join(f"{lon:.6f} {lat:.6f}" for lon, lat in ring) + "))"
    return _row(plot_id, polygon.centroid.y, polygon.centroid.x, wkt, area)


def area_ha(polygon: Polygon) -> float:
    """Area of a lon/lat polygon in hectares, on the flat-earth scale of its own latitude."""
    lat = polygon.centroid.y
    sx, sy = geo.m_per_deg_lon(lat), geo.M_PER_DEG_LAT
    metres = Polygon([(x * sx, y * sy) for x, y in polygon.exterior.coords])
    return metres.area / 10_000.0


def declared_deliveries(plot: dict[str, Any], fiscal_year: int) -> list[dict[str, Any]]:
    """What the drawn plot delivers: 1.5 t a year from 2021 to the fiscal year, not certified."""
    return [{"delivery_id": f"D-{plot['plot_id']}-{year}", "supplier_id": plot["supplier_id"],
             "plot_id": plot["plot_id"], "year": year, "tonnes": TONNES_PER_YEAR, "certified": "no"}
            for year in range(FIRST_YEAR, fiscal_year + 1)]


def _position(value: Any) -> tuple[float, float]:
    if (not isinstance(value, list) or len(value) < 2
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                       for v in value[:2])):
        raise ValueError("input.plot: every position is [longitude, latitude]")
    lon, lat = float(value[0]), float(value[1])
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        raise ValueError("input.plot: positions are [longitude, latitude], in that order")
    return lon, lat


def _row(plot_id: str, lat: float, lon: float, wkt: str, area: float) -> dict[str, Any]:
    return {"plot_id": plot_id, "supplier_id": SUPPLIER_ID, "lat": round(lat, geo.COORD_DECIMALS),
            "lon": round(lon, geo.COORD_DECIMALS), "polygon_wkt": wkt, "area_ha": round(area, 2),
            "declared_by": DECLARED_BY}
