# GreenTrace web

The public face of GreenTrace: a scroll-driven page over one real plot, plus the two tools (check a
plot file, investigate a claim). It runs as a Higgsfield website (React 19 + TanStack Start on a
Cloudflare Worker) and talks to the GreenTrace engine through its own relay, `/api/gt/*`
(`src/routes/api/gt/$.ts`), so the engine needs no CORS rule.

Only the files GreenTrace wrote are here; the Higgsfield template they plug into is not.
`apply_to_template.py <checkout>/app` copies them in and patches the template shell.

- `src/components/gt/TimeMachine.tsx`: the signature. Real Sentinel-2 L2A scenes of plot P-042
  (`public/assets/world/`, from `data/timelapse/` made by `scripts/fetch_satellite.py --timelapse`),
  the EU JRC GFC2020 forest cells and the Hansen loss cells, all from `src/data/plots.json`.
- `src/data/plots.json`: made by the engine itself (`app/web/maps.py` `plot_map`, with
  `app/checks/landcover.py` `measure` for the loss by year on 2020 forest). Nothing is drawn by hand.
- `src/components/gt/runTabs.tsx`: the panels of an investigation. Panels that newer engine
  versions add (court, attack log, deep trace, evidence graph, memory diff, radar, calendar) only
  appear when the engine answers for them.
- `VITE_GT_ENGINE` at build time points a local preview at a local engine; unset, the site talks
  to the public demo engine on Render.

Fonts: Geist and Geist Mono (SIL Open Font License, `public/fonts/OFL.txt`).
Imagery: contains modified Copernicus Sentinel data 2019-2026.
