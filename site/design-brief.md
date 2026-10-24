# GreenTrace web: design brief

**Design read.** For an EU compliance analyst at a cocoa importer (and the hackathon judge looking over their shoulder): calm, exact, a little awe at seeing a real forest disappear year by year. The register of a precision instrument, not a campaign.

**Concept spine.** *The site is a field instrument pointed at one real plot.* Everything on the home page is a reading taken from P-042, a real 10 ha square near Soubré (Côte d'Ivoire): real Sentinel-2 scenes, real EU JRC 2020 forest cells, real Hansen/UMD loss years, and the numbers the GreenTrace code computes from them. Nothing is illustrated, nothing is generated.

**Delivery tier.** cinema (code-driven): one scroll-scrubbed time sequence built from eight real satellite scenes, no generated film (the Higgsfield account is on the free plan; real imagery is also the more honest choice for a compliance product).

**Animation mode: non-animated, user picked "sans carte" (no paid plan, so no generated film) and asked for the motion to be done in code.**
Tier-1 technique: a scroll-scrubbed time machine (catalog family: scroll scrub on image sequence). The visitor's scroll moves the clock from February 2019 to March 2026 over the same 1200 x 800 m frame; the EU 2020 forest cells and the loss cells of each year appear as the clock passes them; a hectare counter climbs to the code's 3.42 ha.

**Locked palette** (the GreenTrace brand set by Amir on 14/10, "Field instrument"):
- ground `#0B1510` (forest-black, never pure black), raised `#111D17`, line `rgba(232,239,234,.10)`
- text `#E8EFEA`, muted `#8FA398`
- ONE accent: signal mint `#9BE3B4` (forest in 2020, primary actions)
- loss: `#D9622B` (the lost cell of the logo; only for cells lost after 2020 and the CONTRADICTED verdict)
- verdict text: proven `#6FCF9A`, contradicted `#F08A63`, unverifiable `#A7B1BC`
Defense: the material world is canopy seen from orbit at night-mode contrast; mint reads as living cover, rust as cleared ground. Brand colors override the gate bans.

**Locked type.** Geist (display + body) and Geist Mono (readings, ids, SQL), self-hosted woff2 (OFL). Display tracking -0.035em.

**Section plan (home).**
1. Hero, split: claim left, the real 2026 scene with the plot outline right.
2. Time machine, pinned full-bleed scrub (signature).
3. How a verdict is made: a stepped vertical rail (agents, code, sources).
4. What would change its mind: interactive before/after toggle, split.
5. Honest when it cannot prove: full-width statement over the P-041 scene.
6. Proof you can check yourself: bento of exactly 4 cells (receipt, sources, sponsors, open standards).
7. Closing band with the two actions, footer with data credits.

**Asset plan.** Real Sentinel-2 L2A true-colour frames of P-042 (2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026 dry seasons) and P-041, from Earth Search (Element 84), credited "Contains modified Copernicus Sentinel data"; real JRC GFC2020 v4 + Hansen GFC v1.12 cells; the existing GreenTrace mark (plot outline with one lost cell). No stock, no generated imagery of the forest, ever.

**CTA inventory.**
- "Check your plots": solid mint slab, square corners, arrow slides on hover.
- "Watch it investigate": outlined ghost with a live dot that pulses once on hover.
- "Run the demo case" (investigate page): mono key-cap button that depresses.
- "Flip the alert date" (home section 4): a physical switch.
- "Download audit package": a file tab shape.
