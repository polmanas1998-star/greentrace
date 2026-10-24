# data/tle: the dated copy of the Sentinel-2 TLEs

`python -m app.geo.tle refresh` fetches the TLEs of Sentinel-2A (40697), 2B (42063) and 2C (60989) from CelesTrak. It checks them, writes `sentinel-2_<UTC time>.json` here and removes the older copy. Commit that file: it is what SAMPLE_MODE reads, with no network call.

`python -m app.geo.tle show` prints what the copy holds and how old each TLE is. Live mode refreshes into `data/cache/tle/`, which is never committed (see `app/geo/tle.py`).
