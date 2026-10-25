# GreenTrace

GreenTrace checks a company's "deforestation-free" promise against the evidence, says whether it is
PROVEN, CONTRADICTED or UNVERIFIABLE, and shows why, down to the database row. Every verdict ships
with a receipt that one command re-derives offline.

- **Website:** https://greentrace-eudr.higgsfield.app (the public face: real Sentinel-2 scenes of one
  plot, a plot file check, a live investigation; code in `site/`)
- **Engine demo:** https://greentrace-0qd7.onrender.com (the engine's own interface and its API; the
  first visit after a quiet spell takes about 50 s to wake the free instance)

**Our entry is for The Agent That Can Explain Why** (Open Agent Hackathon 2026). Solo team: Amir Ogan.

Both links run in sample mode: no model key is set, so the rule fallbacks take Nemotron's place
(the "rules alone" baseline of the bench below), and the verdict comes from `app/rules.py` as in
every mode. The company, its suppliers and its deliveries are fictional; plots P-041 and P-042 are
real places near Soubré, Côte d'Ivoire, read on real forest maps.

## What you can try in 2 minutes

1. **The real plot P-042.** On https://greentrace-eudr.higgsfield.app/investigate, press "Run the
   real plot P-042". The verdict is CONTRADICTED: "3.42 ha that the EU map counted as forest in
   2020 were lost inside P-042 in 2021, 2022, 2023, 2024 (Hansen/UMD), while it kept delivering".
   The home page scrolls through six real Sentinel-2 scenes of the same plot, 2019 to 2026. Locally:
   `python run.py --input examples/05_real_plot_contradicted.json`.
2. **What would change it.** On the same page, press "Run the demo case", then open the tab "What
   would change it": the single changes the rules tried, each with the verdict it would give. Apply
   the alert date of A-221 (2022-03-14 to 2020-12-31): the engine re-runs what depends on it and
   answers PROVEN. Through the API: `POST /runs/{run_id}/what_if` with
   `{"entity":"alert:A-221","field":"alert_date","value":"2020-12-31"}`.
3. **Re-check a verdict offline.**

   ```bash
   python verify.py output_examples/01_contradicted_receipt.json
   ```

   It prints `VALID: CONTRADICTED re-derived offline from 522 rows of 17 answers` (exit 0). Change
   the date of alert A-221 to 2019-03-14 in a copy and it prints `TAMPERED: Q4 row 30 (alert A-221)`
   and the verdict the rules give on the data as found, PROVEN (exit 1). No model, no database, no
   network: see [The verdict receipt](#the-verdict-receipt).

## The problem

The EU Deforestation Regulation (EUDR, Regulation (EU) 2023/1115) applies to large and medium
operators from **30 December 2026**. Cocoa, coffee, soy, palm oil, cattle, wood and rubber placed on
the EU market must come from land that was not deforested after the cut-off date of
**31 December 2020** (`CUTOFF` in `app/rules.py`). Companies already publish promises such as
"100% of our cocoa comes from deforestation-free farms since 2021". Checking one by hand means
crossing a sustainability report, a supplier registry with GPS plots, delivery records, satellite
alerts and forest maps, and then defending the conclusion.

**Who uses it:** the compliance analyst of a cocoa importer, before the company relies on a
supplier's promise. GreenTrace is decision support: not a legal opinion, not a public rating.

## What GreenTrace does

1. Reads the promise and the company's own report; a model quotes, code checks every quote word for word.
2. Plans what must hold, and queries the data only through 11 fixed queries (Q0-Q10) on Zetaris.
3. Writes each finding as a fact in a shared World Model, with the rows it rests on.
4. An Evaluator agent tries to break each finding with its own queries; a hole sends the plan back.
5. Fixed rules in `app/rules.py` decide; the Writer words the verdict, the single change that would
   flip it, the supplier letters and the audit package with its receipt.

## Features

Each one reads a finished run or a plot; none decides a verdict: `app/rules.py` does, alone. One
line per feature, with its route and its test file: [`docs/features/README.md`](docs/features/README.md).

- **Court of inquiry and attack log** ([`docs/features/court.md`](docs/features/court.md)): a
  Plaintiff argues for the promise, the Evaluator answers, the rules judge; and every refutation the
  Evaluator tried, card by card.
- **Explain plus** ([`docs/features/explain_plus.md`](docs/features/explain_plus.md)): the evidence
  graph, the World Model before and after a what_if, Deep Trace (the report sentence by sentence,
  with its rows), the declared area against the plot's outline, the Contradiction Radar, and the
  supplier letter in English, French, Spanish and Portuguese.
- **Geo workflow** ([`docs/features/geo_workflow.md`](docs/features/geo_workflow.md)): the next
  Sentinel-2 passes over a plot from real TLEs (`data/tle/`), a run's calendar as `.ics` and Google
  Calendar links, what each map source says about one plot, and the time slider.
- **Ask GreenTrace** (`POST /runs/{run_id}/ask`, `app/explain/ask.py`): one question about a
  finished run, answered from that run's own lines only. Without a model, the rules return the
  closest lines and say that no model was used.
- **For other agents** ([`docs/features/standards.md`](docs/features/standards.md)): an MCP server
  (stdio, and `/mcp` inside the API), an A2A agent (`/a2a`), one OpenTelemetry span per trace line.
- **Website** ([`site/README.md`](site/README.md)): the time machine over six real Sentinel-2 scenes
  of P-042, an analysis scene driven by the run's own trace lines, the plot file check, the live
  investigation with up to 12 tabs, and the Ask GreenTrace chat over AG-UI
  (`site/src/routes/api/agui.ts`).

## Architecture

```mermaid
flowchart LR
    U["Analyst: claim + optional drawn plot"] --> EX
    subgraph Agents["Five agents (app/agents)"]
        EX[Extractor] --> PL[Planner] --> IN[Investigator] --> EV[Evaluator]
        EV -- "refutation untested: revise plan" --> PL
        EV --> WR[Writer]
    end
    NM["NVIDIA Nemotron 3.5\n(fast and deep lanes)"] -. "proposes: quotes, plans, refutations" .-> Agents
    IN -- "Q0-Q10 only" --> ZT[("Zetaris\nUnified Semantic Layer eudr\n+ native DQ rules")]
    EV -- "own queries" --> ZT
    Agents <--> WM[("Meterless World Model\nreference server, unmodified")]
    WR --> RU{{"app/rules.py\ndecides the verdict"}}
    RU --> OUT["Evidence file, flip, letters,\nreceipt.json, audit package"]
```

**Lineage:** every sentence of the evidence file can be walked back to its source.

```mermaid
flowchart LR
    S["Source\n(report, registry, deliveries,\nalerts, JRC + Hansen maps)"] --> T["Zetaris table\neudr.plots, eudr.alerts,\neudr.reference_cells ..."]
    T --> Q["Query Q4, Q10 ...\nSQL + rows + sha256\n(kept in the receipt)"]
    Q --> F["Meterless World Model fact\nwritten by the Investigator,\nchallenged by the Evaluator"]
    F --> R["Rule 1-4\n(app/rules.py)"]
    R --> P["Sentence in the evidence file\nwith derived_from: Q4#7 row 30"]
```

## Run it

Sample mode needs no key and no network: the synthetic company in SQLite, the real forest maps,
the same agents and rules.

```bash
docker build -t greentrace .
docker run --rm -p 8000:8000 greentrace        # open http://localhost:8000
```

The image (648 MB on disk, measured on 24 October 2026) runs the API and, beside it, the Meterless
World Model server (Node 24) inside the container. Without Docker (Python 3.11 or 3.13, both
tested), in a virtual environment (`python3 -m venv .venv && . .venv/bin/activate`; on Windows
`python -m venv .venv` then `.venv\Scripts\activate`): `pip install -r requirements.txt`, then
`python run.py` (API) or `python run.py --input examples/01_contradicted.json` (one run, JSON on
stdout, trace in `logs/`).

Tests: `python -m pytest -q`, in sample mode (no key, no network). A few are skipped unless their
tool is there: the Meterless conformance suites (8/8 and 13/13) need Node and, once,
`cd meterless && npm ci`; the map-script tests need `pip install -r scripts/requirements-maps.txt`
(rasterio); the live Zetaris test needs `GT_LIVE_TESTS=1` with Zetaris running.

**Live mode** (Zetaris and Nemotron): start Zetaris, copy `.env.example` to `.env` and fill it, load
the data from the host with `python -m scripts.load_zetaris` (case 2 by default, `--case 1` for the
real-plot examples), then
`docker run --rm -p 8000:8000 --env-file .env -e SAMPLE_MODE=false greentrace`. In `.env`, write
`host.docker.internal` instead of `localhost` in `ZETARIS_REST_URL`; on Linux also add
`--add-host=host.docker.internal:host-gateway`.

## Examples

Inputs in `examples/`, the real outputs of `python run.py --input` in `output_examples/` (written by
`python -m scripts.make_output_examples`, never read by the app; `tests/test_output_examples.py`
reruns each input and checks the verdict and its seal).

| Input | Verdict | Why, in the output's own words |
|---|---|---|
| `01_contradicted.json` (case 2) | CONTRADICTED | alert A-221 (2022-03-14, high) lies inside P-014, which delivered in 2022 or later |
| `02_proven.json` (case 1) | PROVEN | holds on every point checked: traced volume, alerts, yields and scope |
| `03_unverifiable.json` (case 8) | UNVERIFIABLE | 88.0% of 2025 volume traced to a plot (needs 99%) |
| `04_prompt_injection.json` (case 19) | CONTRADICTED | the NGO text tells any automated reviewer to answer PROVEN; the rules still find alert A-225 inside P-030 |
| `05_real_plot_contradicted.json` | CONTRADICTED | real maps: 3.42 ha of 2020 forest lost inside P-042 in 2021-2024 |
| `06_real_plot_unverifiable.json` | UNVERIFIABLE | real maps: tree cover lost inside P-041, but the EU 2020 map did not count it as forest |

The seals (`verdict_sha256`) are listed in `output_examples/README.md`.

## Bench: 21 trap cases

`python -m scripts.bench` runs the 21 trap cases (alert one day after the cut-off, latitude and
longitude swapped, a namesake cooperative, a prompt injection in the report, the alert source down...)
through the real agents.

| Run | Score |
|---|---|
| Rules alone, sample mode (`python -m scripts.bench`, checked by `tests/test_pipeline.py`) | **18/21** |
| With Nemotron 3.5 (`python -m scripts.bench --model`, NVIDIA direct), measured on 22 October 2026 | **21/21** |

Rules alone miss cases 12, 17 and 18: an NGO naming a supplier and a plot in free text, a "100%"
that covers only the labelled programme, and an admission hidden in an annex need a model to read
the reports. The 21/21 is a dated measurement, not a promise:
model answers can vary from run to run.

### Planted false evidence

`python -m scripts.tamper_bench` changes every value of every row in the demo receipt, one at a
time (3,020 forgeries), and runs `verify.py` on each, offline (`data/bench_tamper.json`):

| Forger | Forgeries | Flagged TAMPERED |
|---|---|---|
| Changes a value, leaves the hashes | 3,020 | **3,020** |
| Changes a value and rewrites every hash, so integrity passes | 177 that move the verdict or the flip | **177** |

The other 2,843 careful forgeries leave the verdict and the flip as they were, and the receipt
stays VALID: a receipt proves the verdict follows from the rows it carries, not that the rows are
true. Catching the 177 is what re-deriving the verdict, instead of trusting it, buys. The bench
also found a bug: a forged polygon the rules could not parse crashed `verify.py` instead of
answering TAMPERED (fixed, `tests/test_receipt.py`).

## The verdict receipt

```bash
curl -s localhost:8000/runs/<run_id>/receipt > receipt.json      # or the audit package: /runs/<run_id>/package
python verify.py receipt.json
```

`verify.py` uses only GreenTrace's own code: no model, no database, no network. It recomputes every
row hash, finds every quote word for word, rebuilds the evidence, applies `app/rules.py`, replays
the flip and prints **VALID** (exit 0) or **TAMPERED** with the section and row that changed and the
verdict the rules give on the data as found (exit 1). Try it on
`output_examples/01_contradicted_receipt.json`: change the date of alert A-221 to 2019-03-14 and it
prints `TAMPERED: Q4 row ...` and the recomputed verdict PROVEN, the flip the receipt announced.
The receipt checks integrity and re-derivation; it is not a signature: compare its seal with the
`verdict_sha256` the app printed. We haven't seen an EUDR tool ship a verdict you can re-check offline.

## Real plots on real maps

The company is synthetic; the maps are not. `data/reference_cells.csv.gz` holds 716,800 cells of
30 m around Soubre, Côte d'Ivoire: forest in 2020 (EU JRC GFC2020 v4) and the year of tree cover
loss (Hansen/UMD v1.12). An analyst can draw a plot (`input.plot`, GeoJSON); query Q10 reads the
cells inside it, rule 2 contradicts the promise when at least 0.5 ha or 10% of the plot that the EU
map counted as forest in 2020 was lost after 2020 while it delivered, and the Evaluator re-reads the
cells with its own query (refutation R9).

- **P-042** (10 ha square at 5.7300 N, 6.5220 W): 4.623 ha of 2020 forest, 3.4235 ha of it lost after
  2020. CONTRADICTED.
- **P-041** (10.6 ha square at 5.7052 N, 6.7244 W): no 2020 forest on the EU map, 0.51 ha of tree
  cover lost in 2021 and 2024. UNVERIFIABLE: "tree cover was lost, but the EU 2020 map did not count
  this land as forest: confirm in the field". The honest counter-example.

No exact outline was recorded for either plot: both are squares of the declared area around the
declared point. Every number is measured, and kept in `data/reference_cells.meta.json`.

## API

The routes of the features above are listed in [`docs/features/README.md`](docs/features/README.md).
This section documents the plot file check in full.

The analyst's own plot file, checked plot by plot. Routes in `app/web/features.py`, logic in
`app/plotfile.py` (reading) and `app/plotcheck.py` (measuring, ranking, exports); every verdict comes
from `app/rules.py`. In sample mode (`SAMPLE_MODE=true`) the cells are read from
`data/reference_cells.csv.gz`, with no key and no network.

### `POST /api/check-plots`

Body: the EUDR geolocation file itself, a GeoJSON `FeatureCollection`, or the same file wrapped as
`{"geojson": <FeatureCollection>}`.

```json
{"type": "FeatureCollection", "features": [
  {"type": "Feature", "properties": {"plot_id": "P-042", "supplier_id": "S-7"},
   "geometry": {"type": "Polygon", "coordinates": [[[-6.5239, 5.7281], [-6.5201, 5.7281],
                                                   [-6.5201, 5.7319], [-6.5239, 5.7319], [-6.5239, 5.7281]]]}},
  {"type": "Feature", "properties": {"plot_id": "PT-1", "area_ha": 3},
   "geometry": {"type": "Point", "coordinates": [-6.6, 5.72]}}]}
```

- Coordinates are longitude, latitude (EPSG:4326). Geometries: `Polygon`, `MultiPolygon`, or `Point`.
  A point is measured as a square of its declared area around it; without an area it cannot be
  measured (UNVERIFIABLE, `area_missing`).
- At most 500 features, 5,000 vertices per plot, plot area between 0.01 and 5,000 ha. An open ring is
  closed, with a warning.
- Properties read, first one found wins: plot id `plot_id`, `plotId`, `id`, `ProductionPlace`,
  `production_place`, `name`, `Name` (else the feature's position); supplier `supplier`,
  `supplier_id`, `supplierId`, `supplier_name`, `producer`, `ProducerName`, ... (else `"not given"`);
  area `area_ha`, `Area`, `area`, `hectares`, `surface_ha`. All properties are echoed back untouched.

Errors:

| Status | When | Body |
|---|---|---|
| 200 | at least one plot could be read | the result below; unreadable features are listed in `errors` |
| 422 | not a FeatureCollection, no features, more than 500, a `crs` other than EPSG:4326, or no feature readable | `{"detail": "the file must be a GeoJSON FeatureCollection (the EUDR geolocation format)"}` |
| 503 | live mode without Zetaris settings | `{"detail": "live mode needs Zetaris (.env): ..."}` |

One bad feature never refuses the file: it goes to `errors` as
`{"index": 4, "id": "BAD", "error": "a LineString geometry: plots are Polygon, MultiPolygon or Point"}`
(`index` counts features from 1).

Response (200), from a real sample-mode run, `plots` cut to one:

```json
{
  "batch_id": "plots-cb848a5cc4b44c98",
  "producing_until": 2026,
  "summary": {
    "plots": {"CONTRADICTED": 1, "UNVERIFIABLE": 1, "NO_ISSUE": 1},
    "refused": 1,
    "sentence": "1 plot(s) contradict a deforestation-free claim; 1 cannot be verified; 1 show no issue; 1 feature(s) could not be read."
  },
  "plots": [{
    "risk_rank": 1,
    "plot_id": "P-042", "supplier": "S-7", "shape": "polygon", "area_ha": 9.997, "centre": [5.73, -6.522],
    "verdict": "CONTRADICTED", "rule": 2, "code": "forest_loss_in_delivering_plot",
    "sentence": "3.42 ha that the EU map counted as forest in 2020 were lost inside P-042 in 2021, 2022, 2023, 2024 (Hansen/UMD), while it kept delivering (threshold 0.50 ha: 0.5 ha or 10 % of the plot).",
    "measure": {
      "covered": true, "reason": null, "forest_map": true,
      "forest_2020_ha": 4.623, "loss_after_2020_ha": 3.4235,
      "loss_ha_by_year": {"2019": 0.0941, "2020": 3.0336, "2021": 3.5617, "2022": 1.1268, "2023": 0.59, "2024": 0.3613},
      "forest_loss_ha_by_year": {"2020": 0.0765, "2021": 1.4985, "2022": 0.9737, "2023": 0.59, "2024": 0.3613},
      "cells": 144
    },
    "evidence": {"query": "Q10", "answer": "...", "maps": "EU JRC Global Forest Cover 2020 v4 (forest in 2020) + Hansen/UMD/Google/USGS/NASA GFC v1.12 (loss year)"},
    "note": null,
    "properties": {"plot_id": "P-042", "supplier_id": "S-7"}
  }],
  "suppliers": [{"supplier": "S-7", "plots": 1, "CONTRADICTED": 1, "UNVERIFIABLE": 0, "NO_ISSUE": 0,
                 "forest_lost_after_2020_ha": 3.4235, "rank": 1, "why": "Rank 1: ..."}],
  "errors": [{"index": 4, "id": "BAD", "error": "a LineString geometry: plots are Polygon, MultiPolygon or Point"}],
  "warnings": [],
  "rules_version": "2026-10-23.1",
  "exports": {
    "plots_no_issue_found.geojson": "/api/check-plots/plots-cb848a5cc4b44c98/plots_no_issue_found.geojson",
    "findings.csv": "/api/check-plots/plots-cb848a5cc4b44c98/findings.csv",
    "receipt": "/api/check-plots/plots-cb848a5cc4b44c98/receipt",
    "package": "/api/check-plots/plots-cb848a5cc4b44c98/package"
  },
  "seal": "ab3a7d10db7535ecc54d65276c09181b99656e8cca172471f779f8dded07bfb0",
  "promise": null,
  "log_file": "logs/plots-cb848a5cc4b44c98.jsonl"
}
```

- `plots` is already sorted by risk: CONTRADICTED, then UNVERIFIABLE, then NO_ISSUE; inside each,
  most 2020 forest lost first, then by plot id. `risk_rank` is that order, from 1. Show `sentence`
  as is: it is the one line per plot, written by the rules, never by a model.
- `verdict` is one of `CONTRADICTED`, `UNVERIFIABLE`, `NO_ISSUE`; `code` says why:
  `forest_loss_in_delivering_plot`, `forest_loss_below_threshold`,
  `tree_cover_loss_outside_2020_forest`, `tree_cover_loss_no_eu_forest_map`, `area_missing`,
  `polygon_missing`, `no_reference_data`. `NO_ISSUE` has `code` null.
- A plot outside the mapped area is never a zero: `verdict` UNVERIFIABLE, `rule` 1, `code`
  `no_reference_data`, `sentence` `"FAR: no reference map for this area yet."`, `measure.covered`
  false and every hectare null. In sample mode, `note` says that only the maps around Soubre,
  Côte d'Ivoire, are shipped.
- `measure.loss_ha_by_year` is all tree cover lost each year; `forest_loss_ha_by_year` only the loss
  on land the EU map counted as forest in 2020, the loss the EUDR cut-off is about.
  `forest_map` false means tree cover loss was read where no EU 2020 forest map exists.
- `suppliers` rolls the plots up per supplier, worst first. `promise` is null unless the body
  carries one (not used by the page yet).

### Exports

All `GET`, on a batch held by the running process (a restart forgets it: 404 `no plot check ... in
this process`).

| Route | Type | Content |
|---|---|---|
| `/api/check-plots/{batch_id}` | JSON | the response above, again |
| `/api/check-plots/{batch_id}/plots_no_issue_found.geojson` | `application/geo+json`, attachment | a FeatureCollection of the NO_ISSUE plots only, geometry as sent, the original `properties` plus `greentrace_status: "no issue found by GreenTrace"`, `greentrace_checked` (the maps) and `greentrace_batch`; empty if there are none |
| `/api/check-plots/{batch_id}/findings.csv` | `text/csv` (UTF-8 with BOM, opens in Excel), attachment | one row per plot, in risk order: `risk_rank, plot_id, supplier, verdict, rule, code, sentence, area_ha, forest_2020_ha, forest_lost_after_2020_ha, tree_cover_loss_by_year, cells, sources` |
| `/api/check-plots/{batch_id}/receipt` | JSON | the verdict receipt (`kind: "plot_check"`): the plots, the rules and data hashes, every result, the seal. `python verify.py receipt.json` replays every plot offline: VALID (exit 0), TAMPERED (1), UNREADABLE (2) |
| `/api/check-plots/{batch_id}/package` | `application/zip`, attachment | `results.json`, `findings.csv`, `plots_no_issue_found.geojson`, `receipt.json`, `trace.jsonl`, `manifest.json` |
| `/api/check-plots/{batch_id}/plots/{plot_id}/timelapse` | JSON | the time machine on one plot: its outline, a frame around it, one scene and the loss cells per year |

"No issue found" means no forest loss after 2020 on the reference maps inside the outline, nothing
more. Decision support, not a legal opinion, not a public rating.

## Limits

- The company, suppliers, deliveries, reports and alerts are synthetic (placed on real places). Only
  the forest maps, the Sentinel-2 scenes and the Sentinel-2 TLEs are real data.
- The website and the engine demo run in sample mode, with no model key: what they show is the
  rules-alone path. The model paths added for the court, Ask GreenTrace and the letter translation
  are tested with a fake model client only.
- The chat on the website speaks AG-UI to the site's own agent (`site/src/routes/api/agui.ts`), which
  has no model and forwards each question to `POST /runs/{run_id}/ask`. CopilotKit was tried and
  left out: its runtime was too heavy for the Cloudflare Worker the site runs on.
- The model runs on NVIDIA's API directly: Nightshift, which serves the Meterless Runtime, needs a
  dedicated GPU we do not have. The Meterless provider is written to its contract and tested against a
  fake Runtime only (`tests/test_llm.py`).
- A satellite alert or a map cell is not legal proof; 30 m maps can misread a few cells.
- Every verdict needs a human review before any decision. The plot file says "no issue found by
  GreenTrace", never more; supplier letters are drafts that ask, never accuse.

## Sponsor technology

Proof of a live run: `output_examples/live/` keeps one run of the demo case with Zetaris answering all 16 queries, the Meterless World Model server holding the facts and 7 NVIDIA Nemotron calls. Its verdict and seal are the same as the offline sample run (`24035b45...`): the model reads, the rules decide.

- **Zetaris** (data): the Unified Semantic Layer `eudr` (`data/usl_eudr.sql`) over a Postgres
  datasource, built by `scripts/load_zetaris.py`; native data-quality rules (`REGISTER DQ`, read by Q0
  through `SHOW DQ INVALID RECORD`, `data/dq_rules.json`); the agents' only access to data is the 11
  queries Q0-Q10 of `app/queries.py`, run over the Zetaris REST API (`app/adapters/zetaris.py`). Every
  answer keeps its SQL, rows and hash for the receipt.
- **Meterless** (build): the official World Model reference implementation, copied unmodified
  (`meterless/reference`, upstream commit `1742dec`, `meterless/UPSTREAM.sha256`), passes its
  conformance suite 8/8 (`tests/test_world_model.py`) and is the agents' shared memory
  (`app/memory/world_model.py`, `meterless/server.ts`); a Meterless Runtime provider coded to its
  contract in `app/adapters/llm.py` (see Limits). Second Meterless engine: **H-MEM**, the unmodified
  reference (`meterless/hmem`, same upstream commit, conformance 13/13, `tests/test_hmem.py`), used for
  one thing only: one note per supplier a finding concerns, read first by the next investigation and
  shown as "Seen before". It never decides, never enters the receipt, lives in `data/memory/` (local,
  erasable: `python -m app.memory.hmem --forget <company_id>`) and is on when `HMEM_URL` is set (Docker).
- **Cursor** (build): how it was used, honestly, in `docs/CURSOR_LOG.md` (free plan only; the sponsor
  credit was never distributed during the build window).
- **NVIDIA Nemotron** (power): Nemotron 3.5 on two pinned lanes, `nemotron-3.5-lightning-30b-a3b`
  (fast: the Writer) and `nemotron-3.5-super-vl-120b-a12b` (deep: Extractor, Planner, Investigator,
  Evaluator). Nemotron proposes; the code decides.

## AI usage

Built with Claude Code alongside Cursor, as the hackathon's AI Usage Policy allows; every change was
reviewed, tested and explained by the author before it was committed. At run time, Nemotron reads and
proposes; `app/rules.py` decides, and `verify.py` re-derives every verdict without any model.

## Pre-existing work, declared

- The build kit (plan, specifications, 21 trap cases) and the interface mockups in `design/`
  (commit `5b33f41`), written before 22 October 2026.
- The pitch deck and the presentation video.
- The technical probes of 21 October 2026 (documented in `docs/specs/satellite_lens.md`).
- The official `smoke_test.py` of the Participant Setup Guide, and commit `5e37dee` (17 October 2026:
  smoke test, environment template, `.gitignore`). All project code was written from 22 October 2026.

## Data licences

- Hansen/UMD/Google/USGS/NASA Global Forest Change v1.12: CC BY 4.0. "Source: Hansen/UMD/Google/USGS/NASA".
- EU JRC Global Forest Cover 2020 v4: CC BY 4.0 (to confirm on the JRC dataset page).
- Sentinel-2 backgrounds (`python -m scripts.fetch_satellite`, public Earth Search catalogue): "Contains
  modified Copernicus Sentinel data 2026" (P-042, scene of 2026-03-12) and "Contains modified Copernicus
  Sentinel data 2025" (P-041, scene of 2025-02-05). Copernicus Sentinel data are free and open.
- Sentinel-2 time machine scenes of P-042 (`python -m scripts.fetch_satellite --timelapse`,
  `data/timelapse/`, shown on the website): "Contains modified Copernicus Sentinel data 2019-2026".
- Land on the website's analysis scene: Natural Earth 1:110m, public domain. Website fonts: Geist and
  Geist Mono, SIL Open Font License (`site/public/fonts/OFL.txt`).
- Meterless World Model and H-MEM references: Apache-2.0, copyright Meterless (`meterless/LICENSE`,
  `meterless/hmem/LICENSE`).

## License

GreenTrace is released under the Apache License 2.0 (`LICENSE`), the licence of the Meterless
references it embeds. The folders `meterless/reference`, `meterless/conformance` and `meterless/hmem`
keep Meterless's copyright; the data keep the licences listed above.
