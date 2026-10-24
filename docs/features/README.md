# Features: index

One line per feature: its route, its code and its test file. None of them decides a verdict:
`app/rules.py` does, alone. Every engine route below answers in sample mode (`SAMPLE_MODE=true`),
with no key and no network. Run one test file with `python -m pytest -q <test file>`, all of them with
`python -m pytest -q`.

Runs live in the memory of the process that made them (the last 50): the `/runs/{run_id}/...`
routes answer 404 after a restart.

## The investigation

| Feature | Route | Code | Test |
|---|---|---|---|
| One investigation | `POST /run` (or `python run.py --input FILE`) | `app/pipeline.py`, `app/agents/` | `tests/test_pipeline.py` |
| Live investigation, trace streamed | `POST /runs`, `GET /runs/{run_id}/events` | `app/web/live.py` | `tests/test_web.py` |
| What if: change one value | `POST /runs/{run_id}/what_if` | `app/orchestrator.py` | `tests/test_what_if.py` |
| Receipt and audit package | `GET /runs/{run_id}/receipt`, `GET /runs/{run_id}/package`, `python verify.py` | `app/receipt.py`, `verify.py` | `tests/test_receipt.py` |
| Loss by year per plot | `GET /runs/{run_id}/timeline` | `app/explain/timeline.py` | `tests/test_real_plot.py` |
| Suppliers to check first | `GET /runs/{run_id}/suppliers` | `app/explain/suppliers.py` | `tests/test_features.py` |
| Plot file check and its exports | `POST /api/check-plots`, `GET /api/check-plots/{batch_id}/...` | `app/plotfile.py`, `app/plotcheck.py` | `tests/test_plotcheck.py` |
| Satellite time machine | `GET /api/timelapse`, `GET /api/check-plots/{batch_id}/plots/{plot_id}/timelapse` | `app/timelapse.py` | `tests/test_timemachine.py` |
| Satellite Lens | `POST /api/lens`, `GET /api/lens/{key}` | `app/lens/`, `app/web/lens.py` | `tests/test_lens.py` |

The plot file check is documented in full in the main [README](../../README.md#api).

## Court of inquiry ([court.md](court.md))

| Feature | Route | Code | Test |
|---|---|---|---|
| Court: Plaintiff, Defense, Judge | `GET /runs/{run_id}/court` | `app/court/` | `tests/test_court.py` |
| Attack log | `GET /runs/{run_id}/attacks` | `app/court/defense.py` | `tests/test_court.py` |

## Explain plus ([explain_plus.md](explain_plus.md))

| Feature | Route | Code | Test |
|---|---|---|---|
| Evidence graph | `GET /runs/{run_id}/graph` | `app/explain/graph.py` | `tests/test_explain_plus.py` |
| World Model before and after a what_if | `GET /runs/{run_id}/world_diff` | `app/explain/world_diff.py` | `tests/test_explain_plus.py` |
| Deep Trace | `GET /runs/{run_id}/sentences` | `app/explain/deep_trace.py` | `tests/test_explain_plus.py` |
| Contradiction Radar | `GET /runs/{run_id}/radar` | `app/explain/radar.py` | `tests/test_explain_plus.py` |
| Supplier letter in 4 languages | `GET /runs/{run_id}/letter?lang=en\|fr\|es\|pt` | `app/explain/letters_i18n.py` | `tests/test_explain_plus.py` |
| Declared area against the outline | no route of its own: `geofence` in the output of `POST /run` and of each plot of `POST /api/check-plots` | `app/checks/geofence.py` | `tests/test_explain_plus.py` |

## Ask GreenTrace

| Feature | Route | Code | Test |
|---|---|---|---|
| One question about a finished run, answered from its own lines | `POST /runs/{run_id}/ask` with `{"question": "..."}` | `app/explain/ask.py` | `tests/test_ask.py` |

No feature page: the module's docstring says how it works. Without a model the rules return the
closest lines and say so; with one, an answer that cites a line that does not exist, or states a
number found neither in the question nor in the lines it cites, goes back to the rules.

## Geo workflow ([geo_workflow.md](geo_workflow.md))

| Feature | Route | Code | Test |
|---|---|---|---|
| Next Sentinel-2 passes over a plot | `GET /api/plots/passes` | `app/geo/passes.py` | `tests/test_geo_workflow.py` |
| The same passes as a calendar file | `GET /api/plots/passes.ics` | `app/geo/ics.py` | `tests/test_geo_workflow.py` |
| A run's calendar (next pass, expiries, follow-ups) | `GET /runs/{run_id}/calendar`, `GET /runs/{run_id}/calendar.ics` | `app/geo/events.py` | `tests/test_geo_workflow.py` |
| Convergence of the sources on one plot | `GET /api/plots/convergence`, `POST /api/plots/convergence` | `app/geo/convergence.py` | `tests/test_geo_workflow.py` |
| Time slider | `GET /api/plots/verdict_at`, `GET /api/plots/verdict_series` | `app/geo/time_slider.py` | `tests/test_geo_workflow.py` |
| The dated TLE copy (`data/tle/`) | `python -m app.geo.tle refresh`, `python -m app.geo.tle show` | `app/geo/tle.py` | `tests/test_geo_workflow.py` |

## For other agents ([standards.md](standards.md))

| Feature | Route | Code | Test |
|---|---|---|---|
| MCP server, five tools | `python -m app.standards.mcp_server` (stdio), `/mcp` (streamable HTTP) | `app/standards/mcp_server.py`, `app/standards/tools.py` | `tests/test_standards.py` |
| A2A agent | `GET /.well-known/agent-card.json`, `POST /a2a` | `app/standards/a2a.py` | `tests/test_standards.py` |
| OpenTelemetry spans | `GET /runs/{run_id}/otel` (and `logs/<run_id>.otel.jsonl`) | `app/standards/otel.py` | `tests/test_standards.py` |

## Website ([site/README.md](../../site/README.md))

| Feature | Route | Code | Test |
|---|---|---|---|
| Ask GreenTrace chat over AG-UI | `POST /api/agui` on the website | `site/src/routes/api/agui.ts`, `site/src/components/gt/AskPanel.tsx` | none in this repository |
| Relay to the engine | `/api/gt/*` on the website | `site/src/routes/api/gt/$.ts` | none in this repository |

The website's TypeScript has no automated test here; the engine routes it calls are tested above.
