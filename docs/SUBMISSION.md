# HackOS submission: text to copy and paste

Every field below is written to be pasted as is. Every claim is checked against the code and the
README of this repository; numbers come from runs, never from plans.

---

## Project name

GreenTrace

## Tagline

Checks a company's deforestation-free promise against the evidence, explains why down to the database row, and ships every verdict with a receipt anyone can re-check offline.

## Track

The Agent That Can Explain Why

## Short description (300 characters max)

GreenTrace checks a cocoa importer's "deforestation-free since 2021" claim for the EU Deforestation Regulation: five agents query Zetaris, an Evaluator tries to break every finding, fixed rules decide, and each verdict ships with a receipt that one command re-derives offline.

## Long description

**The problem.** The EU Deforestation Regulation (Regulation (EU) 2023/1115) applies to large and medium operators from 30 December 2026: cocoa placed on the EU market must come from land not deforested after 31 December 2020. Companies already publish promises such as "100% of our cocoa comes from deforestation-free farms since 2021". The compliance analyst of an importer has to cross a sustainability report, a supplier registry with GPS plots, delivery records, satellite alerts and forest maps, and then defend the conclusion.

**What GreenTrace does.** Five agents work on one investigation. The Extractor reads the promise and the reports; Nemotron quotes every item, and code drops any quote that is not word for word in the paragraph it cites. The Planner lists what must hold. The Investigator reads the data only through eleven fixed queries (Q0-Q10) on Zetaris and writes each finding as a fact in the Meterless World Model, tied to the rows it rests on. The Evaluator, the only critic, tries to break every finding with its own queries, from a fixed catalogue of refutations (R1-R6, R8, R9); a refutation nobody planned to test sends the plan back to the Planner, and the trace shows it. Then fixed rules in `app/rules.py` decide: PROVEN, CONTRADICTED or UNVERIFIABLE, per claim part. The Writer words the verdict, the single change that would flip it, draft letters to suppliers that ask and never accuse, a plot file, and an audit package.

**Explain why, then prove it.** Every finding names the records it rests on, and the receipt names the exact query and row behind each one. Every verdict ships with `receipt.json`: the SQL, the rows and their hashes, the exact quotes, the rules' hash and the flip. `python verify.py receipt.json` rebuilds the evidence, applies the rules and replays the flip with no model, no database and no network, then prints VALID, or TAMPERED with the row that changed and the verdict the rules give on the data as found.

**Real maps.** The company is synthetic; the forest maps are real. 716,800 cells of 30 m around Soubre, Côte d'Ivoire hold forest in 2020 (EU JRC GFC2020) and the year of tree cover loss (Hansen/UMD). An analyst can draw a plot: on P-042, 3.42 ha of 2020 forest were lost after 2020 while it delivered, so the claim is CONTRADICTED; on P-041, tree cover was lost but the EU map never counted that land as forest in 2020, so GreenTrace says UNVERIFIABLE and asks for a field check instead of condemning it.

**Measured.** On 21 trap cases (an alert one day after the cut-off, latitude and longitude swapped, a namesake cooperative, a prompt injection in an NGO note, the alert source down...), the rules alone score 18/21 in sample mode, and 21/21 with Nemotron 3.5 (measured on 22 October 2026). Sample mode runs with no key and no network: `docker build -t greentrace .` then `docker run --rm -p 8000:8000 greentrace`.

GreenTrace is decision support for a human analyst: not a legal opinion, not a public rating, and every verdict needs a human review.

## Sponsor technology explanation

**Zetaris (data layer).** Zetaris is the agents' only access to data. `scripts/load_zetaris.py` attaches a Postgres datasource and compiles the Unified Semantic Layer `eudr` (`data/usl_eudr.sql`): company, suppliers, plots, deliveries, alerts, report paragraphs, and `reference_cells`, the 716,800 real map cells loaded with COPY. Data quality uses Zetaris's native rules (`REGISTER DQ`, read back through `SHOW DQ INVALID RECORD` as query Q0, rules in `data/dq_rules.json`): the rule `in_cocoa_belt` flags plot P-027 in trap case 16, where latitude and longitude were swapped, and the claim becomes UNVERIFIABLE instead of being checked on the wrong place. The agents may only run the eleven queries Q0-Q10 of `app/queries.py`, over the Zetaris REST API (`app/adapters/zetaris.py`), with every value checked before it reaches SQL; Q10 reads the map cells inside a drawn plot. Every answer keeps its SQL, rows and hash, which is what the receipt replays.

**Meterless (build layer).** The agents share one memory: the official Meterless World Model reference implementation, copied unmodified (`meterless/reference`, upstream commit `1742dec`, every file hashed in `meterless/UPSTREAM.sha256`), which passes its conformance suite 8/8 and runs as a small server inside the same container as the API (`meterless/server.ts`, `app/memory/world_model.py`). A second Meterless engine, H-MEM, also the unmodified reference (`meterless/hmem`, conformance 13/13), is used for one thing only: one note per supplier a finding concerns, read first by the next investigation and shown as "Seen before"; it never decides and never enters the receipt. The model gateway (`app/adapters/llm.py`) has a Meterless Runtime provider written to its provider contract and tested against a fake Runtime. Honest limit: Nightshift, which serves the Runtime, needs a dedicated GPU we do not have, so it is not installed; calls go to NVIDIA directly, and when the chain starts at Meterless the trace records why it was skipped and what came next (a `provider_fallback` line).

**Cursor (build).** The sponsor credit for Cursor was never distributed during the build window, so we built on Cursor's free plan, whose agent stopped at its usage limit on the first session. What Cursor did and did not do is logged honestly in `docs/CURSOR_LOG.md`; its working rules are in `.cursor/rules/greentrace.mdc`.

**NVIDIA Nemotron (power layer).** Nemotron 3.5 runs on two pinned lanes: `nemotron-3.5-super-vl-120b-a12b` (deep) for the Extractor, the Planner, the Investigator and the Evaluator, and `nemotron-3.5-lightning-30b-a3b` (fast) for the Writer. The Extractor reads the reports three times and keeps only what at least two readings agree on, each item quoted word for word and checked by code. The Evaluator asks Nemotron which refutations to try from a finding's structure, and code tests each one with its own query. Satellite Lens shows Nemotron Vision the real Sentinel-2 scene behind a demo plot; it labels a 3 x 3 grid and the code checks every cell against the Hansen loss map (CONFIRMED, REJECTED, MISSED BY AI). On the 21 trap cases, the score is 21/21 with Nemotron against 18/21 for the rules alone (measured on 22 October 2026). Nemotron proposes; the code decides.

**What was unique.**
- A verdict you can re-check offline: `python verify.py receipt.json` re-derives it from the rows with no model, no database and no network, and names the row that was changed. We haven't seen an EUDR tool ship a verdict you can re-check offline.
- The flip: every verdict comes with the single change of one value that would turn it around ("The claim would be PROVEN if alert A-221 were dated 2020-12-31"), found by trying each change through the same rules.
- A real EU map that refuses to condemn: on P-041, tree cover was lost, but the EU 2020 forest map did not count that land as forest, so GreenTrace answers UNVERIFIABLE and asks for a field check.

## Pre-existing work declared

- The build kit and the project file (plan, specifications, 21 trap cases), written from 13 to 21 October 2026.
- The interface mockups in `design/`, drawn from 14 to 21 October 2026 (commit `5b33f41`).
- The pitch deck and the presentation video.
- The Satellite Lens probes of 21 October 2026 (`docs/specs/satellite_lens.md`).
- The official `smoke_test.py` of the Participant Setup Guide, and commit `5e37dee` of 17 October 2026 (smoke test, environment template, `.gitignore`).

All project code is dated after 22 October 2026 00:00 UTC: the first commit of the build window is `65b46e9` at 00:03 UTC (`git log`).

## AI usage

Most of the code was written with Claude Code (one local session and two web sessions), then reviewed, tested and explained by the author before every commit. Cursor was used on its free plan (`docs/CURSOR_LOG.md`). At run time, Nemotron reads and proposes; fixed rules in code decide, and `verify.py` re-derives every verdict without any model. The hackathon's AI Usage Policy was followed.

## Links

- Repository: https://github.com/polmanas1998-star/greentrace
- Demo video: [to fill in]
- Live demo: [to fill in if hosted; see `deploy/huggingface/`]
