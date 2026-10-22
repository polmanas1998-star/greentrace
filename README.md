# GreenTrace

Open Agent Hackathon 2026, track "The Agent That Can Explain Why". Solo team: Amir Ogan.

Before 22 October 2026 00:00 UTC this repository holds setup only (the official smoke test,
environment template and .gitignore). Project code starts at kickoff.

Setup: `pip install -r requirements.txt`, copy `.env.example` to `.env`, fill in the keys, then
`python smoke_test.py` (three PASS lines expected, with Zetaris running).

## Run

Sample mode (no key, no network): `docker build -t greentrace .` then `docker run --rm -p 8000:8000 greentrace`
(the API on port 8000, the Meterless World Model server beside it inside the container).
Live: start Zetaris, copy `.env.example` to `.env` and fill it, load the data from the host with
`python -m scripts.load_zetaris`, then `docker run --rm -p 8000:8000 --env-file .env -e SAMPLE_MODE=false greentrace`
(in `.env`, write `host.docker.internal` instead of `localhost` in `ZETARIS_REST_URL`; on Linux also add
`--add-host=host.docker.internal:host-gateway`).

Satellite backgrounds of the home map (`python -m scripts.fetch_satellite`, Sentinel-2 L2A from the public
Earth Search catalogue): "Contains modified Copernicus Sentinel data [year]", the year of each scene being in
`app/web/static/maps/<plot>.json`. Copernicus Sentinel data are free and open.
