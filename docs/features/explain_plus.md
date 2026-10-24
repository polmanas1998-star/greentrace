# Explain plus: graph, memory diff, Deep Trace, geo-fence, radar, letters

Six ways to look inside a finished run. All of them read what the run kept (the report, the
cards, the plan, the data gate's answers with their SQL and rows, the trace, and the Meterless
World Model); none re-runs the investigation, and none decides a verdict: `app/rules.py` still
does, alone.

| Route | Module | What it answers |
|---|---|---|
| `GET /runs/{run_id}/graph` | `app/explain/graph.py` | Which sentence rests on which finding, record, query and source |
| `GET /runs/{run_id}/world_diff` | `app/explain/world_diff.py` | What the last what_if changed in the World Model |
| `GET /runs/{run_id}/sentences` | `app/explain/deep_trace.py` | The report, sentence by sentence, with the rows behind each |
| `GET /runs/{run_id}/radar` | `app/explain/radar.py` | What each claim needs, and which missing document could flip the verdict |
| `GET /runs/{run_id}/letter?lang=en\|fr\|es\|pt[&supplier=F-007]` | `app/explain/letters_i18n.py` | The supplier letters in four languages |
| check-plots and every run | `app/checks/geofence.py` | Does a plot's declared area match its own outline? |

## Try it

```bash
SAMPLE_MODE=true uvicorn app.api:app --port 8000
curl -s -X POST localhost:8000/run -H 'Content-Type: application/json' \
  -d '{"run_id":"demo","input":{"claim":"100% of our cocoa comes from deforestation-free farms since 2021."},"options":{"case":2}}' >/dev/null
curl -s localhost:8000/runs/demo/sentences
curl -s localhost:8000/runs/demo/graph
curl -s localhost:8000/runs/demo/radar
curl -s 'localhost:8000/runs/demo/letter?lang=fr'
curl -s -X POST localhost:8000/runs/demo/what_if -H 'Content-Type: application/json' \
  -d '{"entity":"alert:A-221","field":"alert_date","value":"2019-03-14"}' >/dev/null
curl -s localhost:8000/runs/demo/world_diff
python -m pytest -q tests/test_explain_plus.py
```

## How each one works

**Evidence graph.** Nodes are `sentence`, `fact` (a finding card, or a record it rests on),
`query` (one answer of the data gate, numbered as in the receipt), `source`, `rule` (the verdict
rule, or a refutation R1-R9 the Evaluator tried on a card) and `verdict`. Every edge carries `via`,
the record it comes from: `world_model` (a card's `depends_on` in the World Model), `writer` (the
Writer's citations), `rows` (the answers that hold the record's rows, found by id), `queries`
(`SOURCE_OF`), `rules`, `evaluator`. Nothing is inferred from the wording.

**Memory diff.** The orchestrator notes the World Model tick before each what_if. The diff lists the
`invalidated_by` edges the last what_if wrote (`invalidated`), the facts the engine marked
superseded by a newer fact (`superseded`, named `about.predicate@v2`), the changed value (its value
before is replayed from the Investigator's rows and earlier what_ifs, as `verify.py` does) and the
entity attributes that differ between the World Model's snapshot at that tick and now (`changed`).

**Deep Trace.** Each sentence comes with the SQL, the engine (`sqlite` or `zetaris`) and the rows of
every answer that holds a row it cites. A source that never answered shows its failed attempt, read
back from the trace with its SQL rendered again. Limitations and the human-review line rest on no
query: they come back `"evidence": []`, `"unsupported": true`. A test checks that no other sentence
is unsupported in any of the 21 trap cases.

**Geo-fence consistency.** The outline's area on a local Lambert azimuthal equal-area projection
(WGS84 ellipsoid, centred on the plot; the test checks it against the exact ellipsoid area of a
1-degree cell) is compared with the declared area: registry `area_ha` in a run, the `area_ha` /
`Area` / `area` / `hectares` / `surface_ha` property in a plot file. More than
`AREA_MISMATCH_SHARE` (10 %) apart gives the constat "declared area does not match the outline".
It sits in `geofence` of the run output and of each check-plots plot; the rules never read it, so
no verdict changes (the 21 registry samples all match within 0.1 %).

**Contradiction Radar.** The plan's hypotheses, the clauses of the promise and what the Extractor
read (admissions, NGO mentions, a narrower scope) are sorted into `checkable_now`,
`needs_document` (which document, from the finding that holds the part open) and `out_of_scope`
(no query reads it, or an NGO mention dated before the cut-off). Each missing document is tried
against the verdict with the outcomes it could establish: the single changes `flip.py` tried on
the entities it concerns, an unconfirmed alert confirmed (`flip.apply`), and its findings settled
in the claim's favour. `would_flip` is true or false; no probability is given.

**Letters.** The run's own letter. If the run used a model, Nemotron translates it and the answer
is kept only when every protected token (ids, dates, numbers, document ids, quotes, names, source
names) comes back character for character, as many times. Otherwise, and always in sample mode,
hand-written sentences per language are filled with the values read from the English letter.

## Honest limits

- Runs live in memory (`orchestrator.RUNS`, last 50): a restarted server has none; runs that stop
  early (out of scope, too vague) are not kept, so these routes answer 404 for them.
- The memory diff covers the last what_if only, and needs the run's World Model in this process.
- Deep Trace maps a sentence to rows through what the Writer cites, so a sentence is "supported" by
  the records it names, not proven true by them. Rows are capped at 50 per answer (`row_count` is
  the full count).
- The radar's document names come from a fixed table per finding code; a document that settles a
  finding is assumed to settle it fully. `would_flip` says whether one of the tried outcomes moves
  the verdict, not how likely that outcome is.
- The hand-written templates know every finding the rules write today. A new finding wording stays
  in English and is listed under `untranslated`. Numbers keep their decimal point in every
  language, on purpose. The model translation was tested with a fake client only; it was not run
  against Nemotron in this branch.
- The geo-fence check needs both an outline and a declared area: points, drawn plots (their area is
  computed from their outline) and files without an area property are listed as not checked.
