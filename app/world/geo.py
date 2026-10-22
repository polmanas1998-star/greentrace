"""Small flat-earth helpers, accurate to well under 1 % over a few kilometres."""
import math

M_PER_DEG_LAT = 110_574.0
COORD_DECIMALS = 6  # EUDR asks for six decimals


def m_per_deg_lon(lat: float) -> float:
    return 111_320.0 * math.cos(math.radians(lat))


def offset(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    return (
        round(lat + north_m / M_PER_DEG_LAT, COORD_DECIMALS),
        round(lon + east_m / m_per_deg_lon(lat), COORD_DECIMALS),
    )


def square_side_m(area_ha: float) -> float:
    return math.sqrt(area_ha * 10_000.0)


def square_wkt(lat: float, lon: float, area_ha: float) -> str:
    """A square of the given area centred on (lat, lon), WKT in lon/lat order."""
    half = square_side_m(area_ha) / 2.0
    corners = [(-half, -half), (-half, half), (half, half), (half, -half), (-half, -half)]
    points = []
    for north, east in corners:
        c_lat, c_lon = offset(lat, lon, north, east)
        points.append(f"{c_lon:.6f} {c_lat:.6f}")
    return "POLYGON((" + ", ".join(points) + "))"


def disk_radius_m(area_ha: float) -> float:
    """Radius of the disk with the given area: how a point-declared plot is modelled."""
    return math.sqrt(area_ha * 10_000.0 / math.pi)


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    mid = (lat1 + lat2) / 2.0
    north = (lat2 - lat1) * M_PER_DEG_LAT
    east = (lon2 - lon1) * m_per_deg_lon(mid)
    return math.hypot(north, east)
