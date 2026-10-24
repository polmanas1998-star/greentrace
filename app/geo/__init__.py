"""Geo workflow: when a satellite passes over a plot, calendars, convergence of the sources, the time slider.

  settings.py      every duration and threshold, in one place (settings, not facts)
  tle.py           Sentinel-2A/2B/2C orbits from CelesTrak, checked, with a dated copy in data/tle/
  passes.py        daylight passes over a point (sgp4), "pass over the plot", never "image guaranteed"
  ics.py           RFC 5545 files and "Add to Google Calendar" links (title and dates only)
  events.py        a run's calendar: next pass, evidence expiry, supplier follow-ups
  plots.py         which plot a question is about: P-041/P-042, the sample registry, or a GeoJSON
  convergence.py   JRC GFC2020, Hansen, GFW tiles, Whisp: n of m sources queried agree
  whisp.py         the Whisp API, only with WHISP_API_KEY
  time_slider.py   the map rule of app/rules.py on the maps cut at a given year
  router.py        the routes (mounted by app/api.py)
"""
