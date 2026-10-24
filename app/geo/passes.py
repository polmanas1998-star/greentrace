"""When Sentinel-2A, 2B and 2C pass over a plot, from their TLE (app/geo/tle.py), in daylight.

A pass over the plot: the satellite climbs at least MIN_ELEVATION_DEG above the plot's horizon, which
puts the plot inside the 290 km swath geometry (app/geo/settings.py), while the sun is above the horizon
there. It says where the satellite will be, never that it will take an image: the acquisition plan, the
clouds and the state of the instrument are not known here. "Pass over the plot", not "image guaranteed".

Method. SGP4 (the sgp4 package: Vallado's implementation) gives the satellite in the TEME frame; a rotation
by Greenwich mean sidereal time (IAU-82, as sgp4 uses it) turns it Earth-fixed (polar motion and UT1-UTC are
ignored: under a second of time); the plot is a point on the WGS84 ellipsoid at height 0. The elevation is
sampled every PASS_STEP_S, each peak above the horizon is refined to PASS_REFINE_S (golden section), and the
start and end of the time above the threshold by bisection. The sun: the low-precision formulas of the
Astronomical Almanac (about 0.01 degree between 1950 and 2050), enough to tell day from night.
"""
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import numpy as np
from sgp4.api import jday

from app.geo import settings, tle as tles

WGS84_A_KM, WGS84_F = 6378.137, 1.0 / 298.257223563
J2000_JD = 2451545.0
GOLDEN = (math.sqrt(5.0) - 1.0) / 2.0
NOTE = ("pass over the plot: the plot lies inside the satellite's nominal 290 km swath geometry at that time, "
        "in daylight. No image is promised: the acquisition plan, clouds and the instrument are not checked.")


# ---- frames and time ---------------------------------------------------------------------------------------------

def julian(when: datetime) -> tuple[float, float]:
    """(whole part, fraction) of the Julian date, as sgp4 takes it."""
    when = when.astimezone(timezone.utc)
    return jday(when.year, when.month, when.day, when.hour, when.minute,
                when.second + when.microsecond / 1e6)


def gmst(jd: np.ndarray) -> np.ndarray:
    """Greenwich mean sidereal time in radians (IAU-82, the formula of sgp4.propagation.gstime)."""
    t = (jd - J2000_JD) / 36525.0
    seconds = -6.2e-6 * t ** 3 + 0.093104 * t ** 2 + (876600.0 * 3600 + 8640184.812866) * t + 67310.54841
    return np.mod(np.radians(seconds / 240.0), 2.0 * np.pi)


def observer(lat: float, lon: float) -> tuple[np.ndarray, np.ndarray]:
    """(position in km, unit vector up) of a point on the WGS84 ellipsoid, Earth-fixed."""
    phi, lam = math.radians(lat), math.radians(lon)
    e2 = WGS84_F * (2.0 - WGS84_F)
    n = WGS84_A_KM / math.sqrt(1.0 - e2 * math.sin(phi) ** 2)
    position = np.array([n * math.cos(phi) * math.cos(lam), n * math.cos(phi) * math.sin(lam),
                         n * (1.0 - e2) * math.sin(phi)])
    up = np.array([math.cos(phi) * math.cos(lam), math.cos(phi) * math.sin(lam), math.sin(phi)])
    return position, up


def earth_fixed(satrec, jd: float, fr: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(Earth-fixed position km, TEME velocity km/s, sgp4 error codes) at the times jd + fr."""
    error, r, v = satrec.sgp4_array(np.full(fr.shape, jd), fr)
    theta = gmst(jd + fr)
    c, s = np.cos(theta), np.sin(theta)
    fixed = np.stack([c * r[:, 0] + s * r[:, 1], -s * r[:, 0] + c * r[:, 1], r[:, 2]], axis=1)
    return fixed, v, error


def elevation_deg(satrec, jd: float, fr: np.ndarray, site: tuple[np.ndarray, np.ndarray]) -> np.ndarray:
    """The satellite's elevation above the plot's horizon, in degrees (NaN where sgp4 fails)."""
    fixed, _, error = earth_fixed(satrec, jd, fr)
    rho = fixed - site[0]
    sine = (rho @ site[1]) / np.linalg.norm(rho, axis=1)
    out = np.degrees(np.arcsin(np.clip(sine, -1.0, 1.0)))
    out[error != 0] = np.nan
    return out


def sun_elevation_deg(when: datetime, lat: float, lon: float) -> float:
    """The sun's elevation at a place and time (Astronomical Almanac, low-precision formulas)."""
    jd, fr = julian(when)
    n = (jd + fr) - J2000_JD
    mean_longitude = (280.460 + 0.9856474 * n) % 360.0
    anomaly = math.radians((357.528 + 0.9856003 * n) % 360.0)
    ecliptic = math.radians(mean_longitude + 1.915 * math.sin(anomaly) + 0.020 * math.sin(2 * anomaly))
    obliquity = math.radians(23.439 - 0.0000004 * n)
    right_ascension = math.atan2(math.cos(obliquity) * math.sin(ecliptic), math.cos(ecliptic))
    declination = math.asin(math.sin(obliquity) * math.sin(ecliptic))
    hour_angle = float(gmst(np.array([jd + fr]))[0]) + math.radians(lon) - right_ascension
    phi = math.radians(lat)
    return math.degrees(math.asin(math.sin(phi) * math.sin(declination)
                                  + math.cos(phi) * math.cos(declination) * math.cos(hour_angle)))


# ---- passes ------------------------------------------------------------------------------------------------------

def passes_of(tle: tles.Tle, lat: float, lon: float, start: datetime, days: float,
              min_elevation_deg: float = settings.MIN_ELEVATION_DEG,
              daylight_only: bool = True) -> list[dict[str, Any]]:
    """Every pass of one satellite over (lat, lon) between start and start + days, sorted by time."""
    satrec, site = tle.satrec(), observer(lat, lon)
    jd, fr0 = julian(start)
    step = settings.PASS_STEP_S

    def at(seconds: float) -> float:
        return float(elevation_deg(satrec, jd, np.array([fr0 + seconds / 86400.0]), site)[0])

    seconds = np.arange(0.0, days * 86400.0 + step, step)
    elevation = elevation_deg(satrec, jd, fr0 + seconds / 86400.0, site)
    found = []
    for i in range(1, len(seconds) - 1):  # a peak cut by the window's edge is left out
        here = elevation[i]
        if not (here > 0.0 and here >= elevation[i - 1] and here > elevation[i + 1]):
            continue
        peak_s, peak = _golden_max(at, seconds[i - 1], seconds[i + 1], settings.PASS_REFINE_S)
        if peak < min_elevation_deg:
            continue
        when = start + timedelta(seconds=peak_s)
        sun = sun_elevation_deg(when, lat, lon)
        daylight = sun >= settings.SUN_MIN_ELEVATION_DEG
        if daylight_only and not daylight:
            continue
        rise = _crossing(at, peak_s - 600.0, peak_s, min_elevation_deg)
        fall = _crossing(at, peak_s + 600.0, peak_s, min_elevation_deg)
        _, velocity, _ = earth_fixed(satrec, jd, np.array([fr0 + peak_s / 86400.0]))
        found.append({
            "satellite": tle.satellite, "norad": tle.norad, "time": tles.iso(when),
            "max_elevation_deg": round(peak, 1), "daylight": daylight, "sun_elevation_deg": round(sun, 1),
            "start": tles.iso(start + timedelta(seconds=rise)), "end": tles.iso(start + timedelta(seconds=fall)),
            "direction": "descending" if velocity[0, 2] < 0 else "ascending",
        })
    return found


def _golden_max(f: Callable[[float], float], low: float, high: float, tolerance: float) -> tuple[float, float]:
    a, b = low, high
    c, d = b - GOLDEN * (b - a), a + GOLDEN * (b - a)
    fc, fd = f(c), f(d)
    while b - a > tolerance:
        if fc > fd:
            b, d, fd = d, c, fc
            c = b - GOLDEN * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + GOLDEN * (b - a)
            fd = f(d)
    middle = (a + b) / 2.0
    return middle, f(middle)


def _crossing(f: Callable[[float], float], below: float, above: float, threshold: float) -> float:
    """The time between `below` (under the threshold) and `above` (over it) where f crosses it."""
    while abs(above - below) > settings.PASS_REFINE_S:
        middle = (below + above) / 2.0
        if f(middle) >= threshold:
            above = middle
        else:
            below = middle
    return round((below + above) / 2.0, 1)


def predict(tle_set: tles.TleSet, lat: float, lon: float, start: datetime, days: float,
            min_elevation_deg: float = settings.MIN_ELEVATION_DEG) -> list[dict[str, Any]]:
    found = [p for t in tle_set.tles for p in passes_of(t, lat, lon, start, days, min_elevation_deg)]
    return sorted(found, key=lambda p: (p["time"], p["satellite"]))


def report(lat: float, lon: float, *, days: float = settings.PASS_DAYS_DEFAULT, now: Optional[datetime] = None,
           sample: bool = True, fetch: Callable[[str], str] = tles.default_fetch,
           shipped=None, cache=None) -> dict[str, Any]:
    """The answer of GET /api/plots/passes: the TLEs used, their age, and the daylight passes."""
    check_place(lat, lon)
    if not 0 < days <= settings.PASS_DAYS_MAX:
        raise ValueError(f"days must be between 1 and {settings.PASS_DAYS_MAX}")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    out: dict[str, Any] = {
        "lat": lat, "lon": lon, "days": days, "from": tles.iso(now), "until": tles.iso(now + timedelta(days=days)),
        "min_elevation_deg": settings.MIN_ELEVATION_DEG,
        "threshold": f"the plot inside the {settings.SWATH_KM:g} km swath seen from {settings.ALTITUDE_KM:g} km "
                     f"(app/geo/settings.py), with the sun above the horizon",
        "note": NOTE,
    }
    tle_set, reason = tles.current(sample, now, fetch=fetch, shipped=shipped, cache=cache)
    if tle_set is None:
        return {"status": "not_queried", "reason": reason, "tle_epoch": None, "tle_age_days": None,
                "tle_source": None, "tles": [], "passes": [], **out}
    oldest = min(tle_set.tles, key=lambda t: t.epoch)
    age = tles.age_days(oldest, now)
    return {
        "status": "success",
        "tle_epoch": tles.iso(oldest.epoch),  # the oldest of the TLEs used: every pass is at least this fresh
        "tle_age_days": age,
        "tle_stale": age > settings.TLE_STALE_AFTER_DAYS,
        "tle_source": tle_set.source,
        "tle_file": tle_set.file,
        "tle_fetched_at": tles.iso(tle_set.fetched_at),
        "tles": [{"satellite": t.satellite, "norad": t.norad, "epoch": tles.iso(t.epoch),
                  "age_days": tles.age_days(t, now), "line1": t.line1, "line2": t.line2} for t in tle_set.tles],
        "problems": tle_set.problems,
        "passes": predict(tle_set, lat, lon, now, days),
        **out,
    }


def check_place(lat: float, lon: float) -> None:
    if not (math.isfinite(lat) and math.isfinite(lon) and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError("lat must be in -90..90 and lon in -180..180")
