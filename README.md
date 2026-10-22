# GreenTrace

Open Agent Hackathon 2026, track "The Agent That Can Explain Why". Solo team: Amir Ogan.

Before 22 October 2026 00:00 UTC this repository holds setup only (the official smoke test,
environment template and .gitignore). Project code starts at kickoff.

Setup: `pip install -r requirements.txt`, copy `.env.example` to `.env`, fill in the keys, then
`python smoke_test.py` (three PASS lines expected, with Zetaris running).

Satellite backgrounds of the home map (`python -m scripts.fetch_satellite`, Sentinel-2 L2A from the public
Earth Search catalogue): "Contains modified Copernicus Sentinel data [year]", the year of each scene being in
`app/web/static/maps/<plot>.json`. Copernicus Sentinel data are free and open.
