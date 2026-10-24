# A live run, kept as proof

`01_contradicted_live.json` and its trace `01_contradicted_live.trace.jsonl` come from one run of
`examples/01_contradicted.json` in live mode (`SAMPLE_MODE=false`) on 25 October 2026:

- every query answered by Zetaris (provider `zetaris` in the trace: 16 queries), after
  `python -m scripts.load_zetaris --case 2`;
- the Meterless World Model reference server on 127.0.0.1:8787 (`meterless/server.ts`) holding the
  facts (provider `meterless`, model `world-model-reference`);
- NVIDIA Nemotron for the Extractor and the Evaluator's proposals (provider `nvidia`, 7 calls);
- Meterless Runtime (Nightshift) not configured: the chain fell through to NVIDIA directly, and the
  trace says so (`provider_fallback`).

Verdict CONTRADICTED with seal `24035b459821721e168619c808d35a6a03819b9feb113dac3ffabca4cba56eb0`:
the same seal as the sample-mode run in `output_examples/01_contradicted.json`. The model reads and
proposes; the rules decide, so the verdict does not depend on which engines answered.
