# Geo workflow: satellite passes, calendars, convergence of the sources, time slider

Code: `app/geo/`. Routes: `app/geo/router.py`, mounted by `app/api.py`. Tests: `tests/test_geo_workflow.py`.
Every duration and threshold is a named setting in `app/geo/settings.py`. They are settings, not facts.

## What it does

| Route | Answer |
|---|---|
| `GET /api/plots/passes?lat=&lon=&days=30` (or `plot_id=`) | The next daylight Sentinel-2A/2B/2C passes over the point, the TLE used and its age |
| `GET /api/plots/passes.ics?lat=&lon=` | The same passes as an iCalendar file |
| `GET /runs/{run_id}/calendar` | A run's events: the next pass, evidence expiry, supplier follow-ups, each with a Google Calendar link |
| `GET /runs/{run_id}/calendar.ics` | The same events as an iCalendar file |
| `GET /api/plots/convergence?plot_id=` / `POST` a GeoJSON | What each source says about one plot, and "n of m sources queried agree" |
| `GET /api/plots/verdict_at?plot_id=&year=2016..2025` | The map rule of `app/rules.py` applied to the maps cut at the end of that year |
| `GET /api/plots/verdict_series?plot_id=` | Every year of the slider at once, and the first year that is CONTRADICTED |

`plot_id` can be P-041 or P-042 (real plots near Soubré), or P-001 to P-040 (the synthetic sample registry).

**Passes.** The two-line element sets (TLEs) come from CelesTrak (NORAD 40697, 42063 and 60989). Each one is checked: checksum, catalogue number and name. The positions come from `sgp4` (TEME frame, rotated by GMST, WGS84 observer). "A pass over the plot" means two things: the plot lies inside the 290 km swath geometry seen from 786 km, which puts the satellite at 78.27° or more above the horizon, and the sun is above the horizon. The answer never says an image will be taken.

**Calendar.** No OAuth: the routes return `.ics` files (RFC 5545, written by hand and checked in tests with `icalendar`) and Google Calendar links that carry only the title and the dates. Titles name plots by their id, never a person, a supplier or a set of coordinates. The run's dates come from its trace (`logs/<run_id>.jsonl`).
- Alerts expire on the date query Q4 read them plus `ALERTS_VALID_DAYS`.
- When a plot was measured on JRC GFC2020 v4 and Hansen v1.12 (query Q10), there is one version check per map, `REFERENCE_MAPS_RECHECK_MONTHS` after the maps were built.
- Each document read by query Q1 expires on its declared date plus `DOCUMENTS_VALID_MONTHS`.
- An UNVERIFIABLE verdict gets one follow-up per supplier letter on day 3 and day 7, with the letter text in the `.ics`.

Anything that cannot be scheduled is listed under `not_scheduled` with the reason.

**Convergence.** The question is: "was forest standing in 2020 lost after 2020 inside this plot?". The threshold is the one in the rules: 0.5 ha or 10 % of the plot, whichever is smaller. Four sources answer:
- JRC GFC2020: forest in 2020.
- Hansen GFC v1.12: tree cover loss after 2020.
- GFW tiles, live mode only.
- Whisp, in live mode with `WHISP_API_KEY` only. The request is `POST https://whisp.openforis.org/api/submit/geojson` with the `X-API-KEY` header. The result is read from `Ind_01_treecover`, `Ind_04_disturbance_after_2020` and `risk_pcrop`.

Each source is marked `queried`, `not_queried` (with the reason) or `unavailable` (with the reason).

**Time slider.** Hansen losses after year Y are removed from the plot's cells, then `rules.check_plot` judges what is left. P-042 is PROVEN up to 2020 and CONTRADICTED from 2021, the year its loss on 2020 forest reaches 1.50 ha.

## How to run it

```bash
pip install -r requirements.txt            # adds sgp4==2.27 and icalendar==7.3.0
python -m app.geo.tle refresh              # needs network access to celestrak.org; writes data/tle/sentinel-2_<UTC>.json
python -m app.geo.tle show                 # what the copy holds, and how old each TLE is
SAMPLE_MODE=true uvicorn app.api:app --port 8000
curl "localhost:8000/api/plots/passes?plot_id=P-042"
curl -o passes.ics "localhost:8000/api/plots/passes.ics?lat=5.73&lon=-6.522"
curl "localhost:8000/api/plots/convergence?plot_id=P-042"
curl "localhost:8000/api/plots/verdict_at?plot_id=P-042&year=2021"
# after POST /run: curl localhost:8000/runs/<run_id>/calendar   and   .../calendar.ics
python -m pytest -q tests/test_geo_workflow.py
```

In SAMPLE_MODE nothing touches the network: the TLEs come from the copy in `data/tle/`, and the maps from `data/reference_cells.csv.gz`.

## Honest limits

- **The TLE copy ages.** `data/tle/sentinel-2_20261024T1624Z.json` was fetched from CelesTrak on 2026-10-24 at 16:24 UTC. SAMPLE_MODE reads it as is, so its passes drift as it gets older: run `python -m app.geo.tle refresh` where CelesTrak is reachable and commit the new file. Without any copy, the passes answer `"status": "not_queried"` and give the reason. The tests use made-up orbits built with `sgp4`, never the real elements.
- **A pass is geometry, not an acquisition.** The ESA acquisition plan, clouds and whether each satellite is currently imaging are not checked. Accuracy also drops as a TLE ages: the answer gives the age and flags a TLE older than `TLE_STALE_AFTER_DAYS`. UT1-UTC and polar motion are ignored (under a second). The swath threshold is computed on a spherical Earth.
- **The sample documents declare only a year** (`rse_2025`, `ngo_2025`). Their expiry is therefore counted from the 31 December of that year (`DOCUMENT_YEAR_ONLY_MONTH_DAY`), and the event says so.
- **The sources are not all independent.** JRC GFC2020 and Hansen come from the same committed cells file. The GFW tiles serve the same Hansen/UMD layer (tree cover ≥ 30 % in 2000), and the answer says `same_layer_as`. Offline, only the Soubré extent of the committed maps can be measured.
- **The Whisp API was checked against its source code**, not by a live call: there was no key, and no network access from the build environment. The source is `forestdatapartnership/whisp-app`, `api/src`, read on 24/10/2026.
- **The slider is today's map, cut at a year.** It runs on Hansen v1.12 cut at that year, not on the map as it was published that year, since Hansen reprocesses past years. Hansen v1.12 stops at 2024, so 2025 shows the 2024 state. The verdict covers the map rule on one plot, not a whole claim.
- **The slider runs on the JRC/Hansen maps embedded in the repository, not on Google Earth Engine.** Earth Engine needs a registered Google Cloud account, so it is not coded.
