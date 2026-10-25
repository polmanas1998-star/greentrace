# Meterless World Model, run locally for GreenTrace

GreenTrace's agents share one memory: the **Meterless World Model**. This folder runs the official
reference implementation on this machine and opens a small JSON door to it for the Python agents.

| Path | What it is | Who wrote it |
|---|---|---|
| `reference/`, `conformance/`, `LICENSE` | World Model reference implementation and its conformance suite, Apache-2.0 | Meterless, copied **unmodified** from [Meterless/Meterless](https://github.com/Meterless/Meterless) `engines/world-model`, commit `1742dec` |
| `UPSTREAM.sha256` | sha-256 of every copied file, to check they are unmodified | GreenTrace |
| `server.ts`, `package.json` | the local server: one world per run, `source-priority` conflict policy | GreenTrace (Apache-2.0) |
| `scout/index.ts`, `scout/greentrace_intents.json` | Scout Intent, the third engine: guard before a model reads, a signed contract before Ask acts. Implemented from the spec (upstream ships no code) | GreenTrace (Apache-2.0) |
| `scout-evals/` | Scout's eval harness, its locked regression set and thresholds, copied **unmodified** from `engines/scout-intent`, commit `1742dec` (`scout-evals/UPSTREAM.sha256`); `npm ci` then `SCOUT_IMPL=<abs path>/scout/index.ts npx tsx evals/runner/index.ts all` | Meterless, Apache-2.0 |
| `hmem/reference/`, `hmem/conformance/`, `hmem/LICENSE`, `hmem/AGENTS.md` | H-MEM, the second engine: memory between investigations, and its conformance suite (13/13), Apache-2.0 | Meterless, copied **unmodified** from `engines/hmem`, commit `1742dec`; `hmem/UPSTREAM.sha256` lists every file |

## Run it

```bash
npm ci                    # once: tsx (runs TypeScript) and vitest (reference tests)
npm run conformance       # Meterless conformance suite: 8/8 checks expected
npm run test:reference    # the reference's own tests
npm start                 # server on http://127.0.0.1:8787 (WORLD_MODEL_PORT to change)
sha256sum -c UPSTREAM.sha256   # proves reference/ and conformance/ are untouched
node node_modules/tsx/dist/cli.mjs hmem/conformance/runner.ts   # H-MEM conformance: 13/13 expected
(cd hmem && sha256sum -c UPSTREAM.sha256)                         # H-MEM untouched
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

## The second door: H-MEM

`POST /hmem` with `{"op": ..., "args": {...}}`, ops `add`, `query`, `delete`, `history` (the store,
retrieval and ledger services of the reference). Kept on disk in `HMEM_DIR` (default
`../data/memory`: `state.json` + `ledger.jsonl`, written by the reference itself); `HMEM_CLOCK_MS`
pins its clock, for tests only. GreenTrace's use is in `app/memory/hmem.py`.
