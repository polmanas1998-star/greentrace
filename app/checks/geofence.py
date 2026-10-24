"""Geo-fence consistency: does the area a plot declares match the area of its own outline?

A registry row (or a plot file's properties) declares hectares; the polygon next to it has an
area of its own. Measured on a local equal-area projection (Lambert azimuthal equal-area on the
WGS84 ellipsoid, centred on the plot: Snyder, Map Projections, a Working Manual, 1987, p. 187),
a gap above AREA_MISMATCH_SHARE of the declared area is reported as one constat: "declared area
does not match the outline".

It is a constat, not a verdict rule: app/rules.py never reads it, so no verdict changes. A point
has no outline, and a drawn plot's area is computed from its own outline: neither is checked,
and each says why. Pure functions: no model, no network, no clock.
"""
import math
from typing import Any, Callable, Optional

import numpy as np
import shapely
from shapely import wkt
from shapely.errors import ShapelyError

AREA_MISMATCH_SHARE = 0.10  # gap between declared and outline area, as a share of the declared area
FINDING = "declared area does not match the outline"
PROJECTION = "Lambert azimuthal equal-area on the WGS84 ellipsoid, centred on each plot"
AREA_KEYS = ("area_ha", "Area", "area", "hectares", "surface_ha")  # app/plotfile.py reads the same names

_A = 6_378_137.0  # WGS84 semi-major axis, metres
_F = 1 / 298.257223563
_E2 = _F * (2 - _F)
_E = math.sqrt(_E2)


def _q(sin_phi):
    return (1 - _E2) * (sin_phi / (1 - _E2 * sin_phi ** 2)
                        - np.log((1 - _E * sin_phi) / (1 + _E * sin_phi)) / (2 * _E))


_QP = float(_q(1.0))
_RQ = _A * math.sqrt(_QP / 2)


def laea(lat0: float, lon0: float) -> Callable[[np.ndarray], np.ndarray]:
    """Forward oblique LAEA (ellipsoid) centred on (lat0, lon0): an (n, 2) array of lon, lat -> x, y metres."""
    phi1 = math.radians(lat0)
    beta1 = math.asin(_q(math.sin(phi1)) / _QP)
    m1 = math.cos(phi1) / math.sqrt(1 - _E2 * math.sin(phi1) ** 2)
    d = _A * m1 / (_RQ * math.cos(beta1))

    def forward(lon_lat: np.ndarray) -> np.ndarray:
        beta = np.arcsin(_q(np.sin(np.radians(lon_lat[:, 1]))) / _QP)
        dlam = np.radians(lon_lat[:, 0] - lon0)
        b = _RQ * np.sqrt(2 / (1 + math.sin(beta1) * np.sin(beta) + math.cos(beta1) * np.cos(beta) * np.cos(dlam)))
        return np.column_stack((b * d * np.cos(beta) * np.sin(dlam),
                                (b / d) * (math.cos(beta1) * np.sin(beta)
                                           - math.sin(beta1) * np.cos(beta) * np.cos(dlam))))

    return forward


def outline_area_ha(polygon_wkt: str) -> float:
    """Hectares inside a lon/lat outline (Polygon or MultiPolygon), on an equal-area projection centred on it."""
    shape = wkt.loads(polygon_wkt)
    centre = shape.centroid
    return shapely.transform(shape, laea(centre.y, centre.x)).area / 10_000.0


def compare(plot_id: str, declared_ha: Any, polygon_wkt: Optional[str]) -> dict[str, Any]:
    """One plot: its declared area against its outline. status "checked" or "not_checked" (with the reason)."""
    if not polygon_wkt:
        return _not_checked(plot_id, declared_ha, "declared by a point: there is no outline to measure")
    if isinstance(declared_ha, bool) or not isinstance(declared_ha, (int, float)) or not math.isfinite(declared_ha) \
            or declared_ha <= 0:
        return _not_checked(plot_id, declared_ha, "no declared area to compare" if declared_ha is None
                            else f"the declared area {declared_ha!r} is not a positive number of hectares")
    try:
        outline = outline_area_ha(polygon_wkt)
    except (ShapelyError, ValueError) as error:
        return _not_checked(plot_id, declared_ha, f"the outline cannot be read ({error})")
    share = abs(outline - declared_ha) / declared_ha
    matches = share <= AREA_MISMATCH_SHARE
    sentence = (f"{plot_id} declares {declared_ha:g} ha; its outline measures {outline:.2f} ha "
                f"({share:.1%} apart, threshold {AREA_MISMATCH_SHARE:.0%})"
                + ("." if matches else f": {FINDING}."))
    return {"plot_id": plot_id, "status": "checked", "declared_ha": declared_ha, "outline_ha": round(outline, 4),
            "difference_share": round(share, 4), "threshold_share": AREA_MISMATCH_SHARE, "matches": matches,
            "finding": None if matches else FINDING, "sentence": sentence}


def _not_checked(plot_id: str, declared_ha: Any, reason: str) -> dict[str, Any]:
    return {"plot_id": plot_id, "status": "not_checked", "declared_ha": declared_ha, "outline_ha": None,
            "difference_share": None, "threshold_share": AREA_MISMATCH_SHARE, "matches": None, "finding": None,
            "reason": reason, "sentence": f"{plot_id}: area not compared, {reason}."}


def registry_plot(plot: dict[str, Any]) -> dict[str, Any]:
    """A plot of an investigation: area_ha is the registry's declared column, polygon_wkt its outline."""
    if plot.get("declared_by") == "analyst":
        return _not_checked(plot["plot_id"], plot.get("area_ha"),
                            "drawn by the analyst: its area is computed from its own outline")
    return compare(plot["plot_id"], plot.get("area_ha"), plot.get("polygon_wkt"))


def file_plot(plot: dict[str, Any]) -> dict[str, Any]:
    """A plot of the analyst's own file (app/plotfile.py): the area its properties declare, if any."""
    properties = plot.get("properties") or {}
    declared = next((properties[key] for key in AREA_KEYS if properties.get(key) not in (None, "")), None)
    if plot.get("shape") == "point":
        return compare(plot["plot_id"], declared, None)
    return compare(plot["plot_id"], declared, plot.get("polygon_wkt"))


def summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Every plot's comparison folded into one constat: the plots whose outline does not match."""
    checked = [r for r in results if r["status"] == "checked"]
    mismatches = [r for r in checked if not r["matches"]]
    sentence = (f"{len(mismatches)} plot(s): {FINDING} (more than {AREA_MISMATCH_SHARE:.0%} apart): "
                + ", ".join(r["plot_id"] for r in mismatches) + "." if mismatches
                else f"Every plot with an outline and a declared area matches it within {AREA_MISMATCH_SHARE:.0%}"
                     f" ({len(checked)} checked)." if checked
                else "No plot has both an outline and a declared area: nothing to compare.")
    return {"threshold_share": AREA_MISMATCH_SHARE, "projection": PROJECTION, "checked": len(checked),
            "not_checked": len(results) - len(checked), "findings": mismatches, "sentence": sentence,
            "decides_verdict": False}


def investigation(plots: list[dict[str, Any]]) -> dict[str, Any]:
    """The constat of an investigation: every registry plot (and a drawn one) of its evidence, once each."""
    seen, results = set(), []
    for plot in plots:
        key = (plot["plot_id"], plot.get("supplier_id"))
        if key not in seen:
            seen.add(key)
            results.append(registry_plot(plot))
    return summary(results)
