"""The analyst's own plot file: the GeoJSON the EUDR already asks for, read strictly.

A FeatureCollection of production plots: each a Polygon or MultiPolygon in longitude/latitude
(EPSG:4326, as GeoJSON says), or a Point when the plot is 4 ha or less. Properties are free;
GreenTrace reads an identifier and a supplier from the usual names (PLOT_KEYS, SUPPLIER_KEYS)
and an area for a Point (AREA_KEYS). Every other property is kept and given back untouched.

Each feature is checked on its own: a feature that fails says why, with its index and its
id, and the others are still checked. Nothing is guessed: a missing id becomes its position
in the file ("#12"), a missing supplier stays "not given", a Point without an area is kept
and the rules say it cannot be measured. Auto-closing an open ring is the only repair, and it
is reported.
"""
import math
from typing import Any

from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.validation import explain_validity

from app.world import geo

MAX_PLOTS = 500
MAX_VERTICES = 5000  # per plot, all rings together
MAX_HA = 5000.0  # per plot: a larger outline is more likely a region than a plot
MIN_HA = 0.01
PLOT_KEYS = ("plot_id", "plotId", "id", "ProductionPlace", "production_place", "name", "Name")
SUPPLIER_KEYS = ("supplier", "supplier_id", "supplierId", "supplier_name", "producer", "ProducerName",
                 "producer_name", "farmer", "cooperative")
AREA_KEYS = ("area_ha", "Area", "area", "hectares", "surface_ha")
NOT_GIVEN = "not given"


class FileRefused(ValueError):
    """The whole file cannot be read (not a FeatureCollection, too many plots, nothing valid)."""


def read(geojson: Any) -> tuple[list[dict], list[dict], list[str]]:
    """(plots, errors per feature, warnings) from a FeatureCollection; raises FileRefused for the file."""
    if not isinstance(geojson, dict) or geojson.get("type") != "FeatureCollection":
        raise FileRefused("the file must be a GeoJSON FeatureCollection (the EUDR geolocation format)")
    features = geojson.get("features")
    if not isinstance(features, list) or not features:
        raise FileRefused("the FeatureCollection has no features")
    if len(features) > MAX_PLOTS:
        raise FileRefused(f"{len(features)} plots: at most {MAX_PLOTS} per check, split the file")
    crs = (geojson.get("crs") or {}).get("properties", {}).get("name", "")
    if crs and not any(code in crs for code in ("4326", "CRS84")):
        raise FileRefused(f"coordinates must be longitude/latitude (EPSG:4326), not {crs}")

    plots, errors, warnings, seen = [], [], [], set()
    for index, feature in enumerate(features, start=1):
        label = f"#{index}"
        try:
            plot, notes = _plot(feature, label)
            if plot["plot_id"] in seen:
                raise ValueError(f"the id {plot['plot_id']!r} is used twice in the file")
            seen.add(plot["plot_id"])
            plots.append(plot)
            warnings += [f"{plot['plot_id']}: {note}" for note in notes]
        except ValueError as error:
            ident = _ident(feature) if isinstance(feature, dict) else None
            errors.append({"index": index, "id": ident, "error": str(error)})
    if not plots:
        raise FileRefused(f"no plot could be read: {errors[0]['error']}" if errors else "no plot could be read")
    return plots, errors, warnings


def _ident(feature: dict) -> Any:
    properties = feature.get("properties") or {}
    if isinstance(properties, dict):
        for key in PLOT_KEYS:
            if properties.get(key) not in (None, ""):
                return str(properties[key])
    return str(feature["id"]) if feature.get("id") not in (None, "") else None


def _plot(feature: Any, label: str) -> tuple[dict, list[str]]:
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        raise ValueError("not a GeoJSON Feature")
    properties = feature.get("properties") or {}
    if not isinstance(properties, dict):
        raise ValueError("properties must be an object")
    plot_id = (_ident(feature) or label)[:64]
    supplier = next((str(properties[k]) for k in SUPPLIER_KEYS if properties.get(k) not in (None, "")), NOT_GIVEN)
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict):
        raise ValueError("no geometry")
    kind, notes = geometry.get("type"), []

    if kind == "Point":
        lon, lat = _position(geometry.get("coordinates"))
        area = next((properties[k] for k in AREA_KEYS if k in properties), None)
        if area is not None and (isinstance(area, bool) or not isinstance(area, (int, float))
                                 or not math.isfinite(area) or not MIN_HA <= area <= MAX_HA):
            raise ValueError(f"the area of a Point must be a number of hectares between {MIN_HA} and {MAX_HA}")
        return _row(plot_id, supplier, lat, lon, "", float(area) if area is not None else None, "point",
                    properties), notes

    if kind not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"a {kind or 'missing'} geometry: plots are Polygon, MultiPolygon or Point")
    polygons = [geometry.get("coordinates")] if kind == "Polygon" else geometry.get("coordinates")
    if not isinstance(polygons, list) or not polygons:
        raise ValueError("no coordinates")
    vertices, cleaned = 0, []
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon:
            raise ValueError("a polygon needs at least its outer ring")
        rings = []
        for ring in polygon:
            if not isinstance(ring, list):
                raise ValueError("a ring must be a list of [longitude, latitude] positions")
            points = [_position(p) for p in ring]
            if points and points[0] != points[-1]:
                points.append(points[0])
                notes.append("an open ring was closed")
            if len(points) < 4:
                raise ValueError("a ring needs at least 4 positions (3 corners and the first again)")
            vertices += len(points)
            rings.append(points)
        cleaned.append(rings)
    if vertices > MAX_VERTICES:
        raise ValueError(f"{vertices} vertices: at most {MAX_VERTICES} per plot")
    outline = shape({"type": "MultiPolygon" if kind == "MultiPolygon" else "Polygon",
                     "coordinates": cleaned if kind == "MultiPolygon" else cleaned[0]})
    if not outline.is_valid:
        raise ValueError(f"invalid outline ({explain_validity(outline)})")
    area = area_ha(outline)
    if not MIN_HA <= area <= MAX_HA:
        raise ValueError(f"{area:.2f} ha: a plot must be between {MIN_HA} and {MAX_HA} ha")
    centre = outline.representative_point() if kind == "MultiPolygon" else outline.centroid
    return _row(plot_id, supplier, centre.y, centre.x, outline.wkt, area, kind.lower(), properties), \
        list(dict.fromkeys(notes))


def area_ha(outline: Any) -> float:
    """Hectares of a lon/lat outline, each part on the flat-earth scale of its own latitude."""
    parts = list(outline.geoms) if isinstance(outline, MultiPolygon) else [outline]
    total = 0.0
    for part in parts:
        sx, sy = geo.m_per_deg_lon(part.centroid.y), geo.M_PER_DEG_LAT
        scaled = Polygon([(x * sx, y * sy) for x, y in part.exterior.coords],
                         [[(x * sx, y * sy) for x, y in ring.coords] for ring in part.interiors])
        total += scaled.area / 10_000.0
    return total


def _position(value: Any) -> tuple[float, float]:
    if (not isinstance(value, list) or len(value) < 2
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                       for v in value[:2])):
        raise ValueError("every position is [longitude, latitude], two numbers")
    lon, lat = float(value[0]), float(value[1])
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        raise ValueError(f"[{value[0]}, {value[1]}] is not [longitude, latitude]: check the order")
    return lon, lat


def _row(plot_id: str, supplier: str, lat: float, lon: float, wkt: str, area: Any, shape_kind: str,
         properties: dict) -> dict[str, Any]:
    return {"plot_id": plot_id, "supplier_id": supplier, "lat": round(lat, geo.COORD_DECIMALS),
            "lon": round(lon, geo.COORD_DECIMALS), "polygon_wkt": wkt,
            "area_ha": round(area, 4) if area is not None else None, "shape": shape_kind,
            "properties": properties}
