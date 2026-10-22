# Meterless World Model, run locally for GreenTrace

GreenTrace's agents share one memory: the **Meterless World Model**. This folder runs the official
reference implementation on this machine and opens a small JSON door to it for the Python agents.

| Path | What it is | Who wrote it |
|---|---|---|
| `reference/`, `conformance/`, `LICENSE` | World Model reference implementation and its conformance suite, Apache-2.0 | Meterless, copied **unmodified** from [Meterless/Meterless](https://github.com/Meterless/Meterless) `engines/world-model`, commit `1742dec` |
| `UPSTREAM.sha256` | sha-256 of every copied file, to check they are unmodified | GreenTrace |
| `server.ts`, `package.json` | the local server: one world per run, `source-priority` conflict policy | GreenTrace (Apache-2.0) |

## Run it

```bash
npm ci                    # once: tsx (runs TypeScript) and vitest (reference tests)
npm run conformance       # Meterless conformance suite: 8/8 checks expected
npm run test:reference    # the reference's own tests
npm start                 # server on http://127.0.0.1:8787 (WORLD_MODEL_PORT to change)
sha256sum -c UPSTREAM.sha256   # proves reference/ and conformance/ are untouched
```

GreenTrace finds the server at `WORLD_MODEL_URL` (default `http://127.0.0.1:8787`). If it does not
answer, `app/memory/world_model.py` switches to an in-memory twin (`app/memory/fallback.py`) and
every trace line of the run says `degraded`. `tests/test_world_model.py` runs the same case on both
and checks they build the same world, byte for byte.

## The door

- `GET /health`: engine name, conflict policy, source priority. GreenTrace refuses a server whose
  policy is not `source-priority` with `evaluator > investigator > extractor`.
- `POST /call` with `{"namespace": run_id, "op": ..., "args": {...}}`. Ops: `upsertEntity`,
  `relate`, `assertFact` (writes) and `entity`, `facts`, `entityAt`, `timeline`, `audit` (reads).
  Every write needs provenance (`source.kind` and `source.at`), as the reference requires.
- Listens on 127.0.0.1 only (no authentication), bodies up to 1 MB, at most 200 worlds in memory.
