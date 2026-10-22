"""Where a plot is, and whether an alert touches it.

A plot over 4 ha must be mapped as a polygon (EUDR). A smaller plot may be a single
point: it is modelled as a disk of the same area, and an alert less than 500 m from
that disk cannot be ruled in or out from the registry alone.
"""
from functools import lru_cache

from shapely import wkt
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from app.world import geo

POLYGON_REQUIRED_ABOVE_HA = 4.0
POINT_PLOT_MARGIN_M = 500.0
COTE_DIVOIRE = {"lat": (4.3, 10.8), "lon": (-8.6, -2.5)}


@lru_cache(maxsize=4096)
def _shape(polygon_wkt: str) -> BaseGeometry:
    return wkt.loads(polygon_wkt)


def coordinates_valid(plot: dict) -> bool:
    lat, lon = plot.get("lat"), plot.get("lon")
    if lat is None or lon is None:
        return False
    return (COTE_DIVOIRE["lat"][0] <= lat <= COTE_DIVOIRE["lat"][1]
            and COTE_DIVOIRE["lon"][0] <= lon <= COTE_DIVOIRE["lon"][1])


def polygon_missing(plot: dict) -> bool:
    return not plot["polygon_wkt"] and plot["area_ha"] > POLYGON_REQUIRED_ABOVE_HA


def alert_inside(plot: dict, alert: dict) -> bool:
    """Only a polygon can contain an alert for certain."""
    return bool(plot["polygon_wkt"]) and _shape(plot["polygon_wkt"]).covers(Point(alert["lon"], alert["lat"]))


def gap_to_point_plot_m(plot: dict, alert: dict) -> float:
    """Distance from the alert to the edge of a point-declared plot's disk (negative inside)."""
    return (geo.distance_m(plot["lat"], plot["lon"], alert["lat"], alert["lon"])
            - geo.disk_radius_m(plot["area_ha"]))


def alert_near_point_plot(plot: dict, alert: dict) -> bool:
    return not plot["polygon_wkt"] and gap_to_point_plot_m(plot, alert) <= POINT_PLOT_MARGIN_M
