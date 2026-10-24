"""Every duration and threshold of app/geo, in one place. These are SETTINGS, not facts.

Change them here and nowhere else; each event and answer says which setting it used.
"""
import math

# ---- satellite passes -------------------------------------------------------------------------------------------
PASS_DAYS_DEFAULT = 30
PASS_DAYS_MAX = 30          # a TLE propagated further than this drifts too much to name a day
PASS_STEP_S = 30.0          # coarse search step; every peak is then refined to PASS_REFINE_S
PASS_REFINE_S = 0.5
SUN_MIN_ELEVATION_DEG = 0.0  # "daylight": the sun above the horizon at the plot at the time of the pass

# Sentinel-2 MSI geometry as ESA publishes it: a 290 km swath from a 786 km mean altitude.
# "A pass over the plot" means the plot lies inside that swath geometry: the satellite is then seen
# at least MIN_ELEVATION_DEG above the plot's horizon (about 78 degrees, computed below on a sphere).
SWATH_KM = 290.0
ALTITUDE_KM = 786.0
EARTH_RADIUS_KM = 6371.0


def swath_edge_elevation_deg(swath_km: float = SWATH_KM, altitude_km: float = ALTITUDE_KM,
                             radius_km: float = EARTH_RADIUS_KM) -> float:
    """Elevation of the satellite seen from a point at the edge of its swath (spherical Earth)."""
    theta = (swath_km / 2.0) / radius_km  # Earth central angle between the nadir and the swath edge
    return math.degrees(math.atan2(math.cos(theta) - radius_km / (radius_km + altitude_km), math.sin(theta)))


MIN_ELEVATION_DEG = round(swath_edge_elevation_deg(), 2)

# ---- TLE ---------------------------------------------------------------------------------------------------------
TLE_REFRESH_AFTER_HOURS = 24.0  # live mode: a copy younger than this is used as is, older is fetched again
TLE_STALE_AFTER_DAYS = 7.0      # an answer whose TLE is older says "stale": the times drift as the TLE ages
TLE_TIMEOUT_S = 15.0

# ---- calendar ----------------------------------------------------------------------------------------------------
ALERTS_VALID_DAYS = 30               # alert data: valid this long after the run read it
DOCUMENTS_VALID_MONTHS = 12          # a document: valid this long after the date it declares
DOCUMENT_YEAR_ONLY_MONTH_DAY = (12, 31)  # a document that declares only a year counts from this day of it
REFERENCE_MAPS_RECHECK_MONTHS = 12   # a map version: check this long after the maps were built that it is current
FOLLOW_UP_DAYS = (3, 7)              # UNVERIFIABLE: remind to follow up the supplier letter this many days after
PASS_EVENTS_MAX = 5                  # a run's calendar: the next pass over at most this many plots

# ---- convergence -------------------------------------------------------------------------------------------------
WHISP_URL = "https://whisp.openforis.org/api"  # forestdatapartnership/whisp-app, api/src (read 24/10/2026)
WHISP_TIMEOUT_S = 90.0     # the submit call waits for the analysis when it runs synchronously
WHISP_POLL_S = 5.0         # a queued analysis is polled this often, within WHISP_TIMEOUT_S in all

# ---- time slider -------------------------------------------------------------------------------------------------
SLIDER_FIRST_YEAR, SLIDER_LAST_YEAR = 2016, 2025
