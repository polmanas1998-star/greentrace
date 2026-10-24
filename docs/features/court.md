# Court of inquiry (Plaintiff / Defense / Judge) and the attack log

An adversarial debate layer; the verdict stays with the rules.

The court runs on a finished investigation. It asks no query again and starts no second run:
it reads the rows the run already holds, the Evaluator's reviews, the run's World Model and its
trace (`logs/<run_id>.jsonl`).

- **Plaintiff** (new agent, `app/court/plaintiff.py`): builds the best defense of the promise
  from the pieces of the investigation: the company's report (Q1), the registry (Q2, Q3), the
  deliveries (Q5, Q6), the alerts (Q4, Q9), the plot the analyst drew. Fast lane (Nemotron) when
  a model is configured, rule templates otherwise. Every argument cites a piece; the code strikes
  (`withdrawn`) an argument whose citation is not a row of this run, quotes a field value the row
  does not have, or quotes a passage that is not word for word in the paragraph it names.
- **Defense** = the existing Evaluator (`app/court/defense.py` only reads its record). Against
  an argument for a claim part: the cards the rules wrote on that part, as the World Model holds
  them, with the query and rows each rests on. Against an answer that raises a refutation
  (R1-R9): the Evaluator's own test of it, with its query, SQL and rows.
- **Judge** = `app/rules.py` (`app/court/judge.py`). Round 1: an argument for a part the rules
  did not find PROVEN is refuted. Rounds 2 and 3: the Plaintiff may answer a counter-argument
  that holds, with a piece it has not cited yet in that line of defense; the answer stands only
  if the Evaluator's test broke the evidence. A passage of the report against a fact read from
  the data is tested by the rules: does the rule still find the same thing? The verdict is the
  run's, recomputed by the rules and checked equal. The Writer words the judgment; the model's
  sentence is refused (template used) if it names another verdict or a number not in the record.

Every intervention is one normal trace line: `Plaintiff` (`argue`, `answer`, `rest`),
`Evaluator` (`counter_argument`), `Judge` (`strike_argument`, `judge_round`), `Writer`
(`write_judgment`), each with `target_agent` set.

## Run it

```bash
SAMPLE_MODE=true uvicorn app.api:app --port 8000
curl -s -X POST localhost:8000/run -H 'content-type: application/json' \
  -d '{"run_id":"demo","input":{"claim":"100% of our cocoa comes from deforestation-free farms since 2021."},"options":{"case":2}}' >/dev/null
curl -s localhost:8000/runs/demo/court
curl -s localhost:8000/runs/demo/attacks
python -m pytest -q tests/test_court.py
```

`GET /runs/{run_id}/court`: `{run_id, verdict, rounds: [{n, plaintiff, defense, judge}], judgment, provider}`.
Each Plaintiff item: `{id, claim, cites: [{source, ref, quote}], status: standing|refuted|withdrawn, answers}`
(`answers`: the counter-argument it replies to, null in round 1). Each Defense item:
`{id, answers, refutation, query_id, sql, rows, holds, detail}`. `holds` is from the Defense's
side: true means the counter-argument stands (the evidence held). `rows` are the rows of that
answer about the card (the full answer is in the receipt). `sql: null` means the source did not
answer; `query_id: null` means the test asked no query (R6 judges the kind of source).
`provider` is the route that wrote the Plaintiff's arguments (`meterless`, `nvidia` or `rules`).

`GET /runs/{run_id}/attacks`: every refutation the Evaluator tried, card by card:
`{refutation, question, query_id, result: refuted_evidence|evidence_held|source_unavailable, rows, retry_count}`.
`rows` is the number of rows the query returned; `retry_count` counts the times the Evaluator
had to send the plan back before it could run that attack (R3 waits for the deliveries).

## Honest limits

- The court never changes a verdict, by construction: `tests/test_court.py` checks it on the 21
  cases in sample mode, and that two runs of a case give the same court JSON.
- In sample mode (no model) the Plaintiff's arguments are templates filled with the run's rows
  and report sentences. They are as strong as the pieces allow, no stronger.
- In the 21 sample cases every refutation the Evaluator tests is rejected, so no answer of the
  Plaintiff stands after round 1; `refuted_evidence` only appears when a refutation holds (for
  example after a what_if) and is covered by a test, not by a sample case.
- The Plaintiff only raises refutations the Evaluator actually tested on that card, at most one
  per counter-argument per round, three rounds at most: some tested refutations are never raised
  in the debate. They all appear in the attack log.
- `source_unavailable` in the attack log means the run holds no answer to that attack's query.
  A query that fails inside the Evaluator stops the run today, so a finished run rarely shows it.
- The model path (Plaintiff and Writer) is tested with a fake client only. The `Plaintiff` lane
  is registered from `app/court/plaintiff.py` (`LANES.setdefault("Plaintiff", "fast")`), not in
  `app/adapters/llm.py`.
- The court is computed on the first request and kept with the run in memory; a what_if makes
  the next request compute it again. A run lost with the process (restart) has no court.
- The attack log needs the trace file; if it is gone the route answers 404 and says so.
