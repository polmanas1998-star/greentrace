import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { fmtHa, type PlotMap } from "../../lib/plots";
import { PlotFigure } from "./PlotFigure";

// ---- 1. Hero -----------------------------------------------------------------------------------

export function Hero({ map }: { map: PlotMap }) {
  return (
    <section className="relative mx-auto grid min-h-dvh max-w-[1400px] items-center gap-12 px-6 pb-16 pt-24 md:grid-cols-[1fr_1.05fr] md:px-12">
      <div>
        <h1 className="gt-display gt-rise text-5xl md:text-7xl">
          <span style={{ animationDelay: "0ms" }}>Proof,</span> <span style={{ animationDelay: "90ms" }}>plot</span>{" "}
          <span style={{ animationDelay: "180ms" }}>by</span> <span style={{ animationDelay: "270ms" }}>plot.</span>
        </h1>
        <p className="mt-6 max-w-[44ch] text-lg leading-relaxed text-gt-text/80">
          GreenTrace checks every EUDR plot against the EU forest map, shows its work, and tells you what would change its
          mind.
        </p>
        <div className="mt-10 flex flex-wrap items-center gap-4">
          <CheckPlotsCta />
          <WatchCta />
        </div>
      </div>
      <div className="relative">
        <PlotFigure
          map={map}
          image="p042-2026.jpg"
          layers="loss"
          draw
          className="aspect-[3/2] rounded-[14px] border border-gt-line shadow-[0_40px_120px_-40px_rgba(0,0,0,0.8)]"
          label="Plot P-042 near Soubré on the Sentinel-2 scene of 12 March 2026, with the forest cells lost after 2020"
        />
        <div className="absolute -bottom-5 left-5 flex items-center gap-3 rounded-[14px] border border-gt-line bg-gt-ground/90 px-4 py-3 backdrop-blur-xl">
          <span className="h-2.5 w-2.5 bg-gt-loss" aria-hidden />
          <span className="text-sm">
            <span className="font-semibold text-gt-contradicted">Contradicted.</span>{" "}
            <span className="text-gt-text/80">{fmtHa(map.measure.loss_after_2020_ha)} of 2020 forest lost inside P-042.</span>
          </span>
        </div>
      </div>
    </section>
  );
}

export function CheckPlotsCta({ label = "Check your plots" }: { label?: string }) {
  return (
    <Link
      to="/check"
      className="group inline-flex items-center gap-3 bg-gt-signal px-6 py-3.5 text-[15px] font-semibold text-gt-ground transition-transform active:translate-y-px"
    >
      {label}
      <span className="transition-transform duration-300 group-hover:translate-x-1" aria-hidden>
        →
      </span>
    </Link>
  );
}

export function WatchCta() {
  return (
    <Link
      to="/investigate"
      search={{ demo: "real-plot" }}
      className="group inline-flex items-center gap-3 rounded-full border border-gt-line-strong px-5 py-3 text-[15px] text-gt-text transition-colors hover:border-gt-signal/60"
    >
      <span className="h-2 w-2 rounded-full bg-gt-signal group-hover:[animation:gt-pulse_1s_ease-out_1]" aria-hidden />
      Watch it investigate
    </Link>
  );
}

// ---- 3. How a verdict is made ------------------------------------------------------------------

const STEPS: Array<{ who: string; what: string; detail: string; tag: string }> = [
  {
    who: "Planner",
    what: "Reads the promise and plans the checks.",
    detail: "Which plots, which years, which sources can test it. A promise that names nothing testable is said to be too vague.",
    tag: "Nemotron",
  },
  {
    who: "Extractor",
    what: "Pulls claims, plots and dates out of the documents.",
    detail: "Every extracted fact keeps the exact sentence it came from. A quote that is not in the text is thrown out by the code.",
    tag: "Nemotron, quotes checked by code",
  },
  {
    who: "Investigator",
    what: "Asks the data, one query at a time.",
    detail: "Supplier registry, deliveries, plot outlines, alerts and the EU forest cells, through Zetaris. Each query and its rows go into the trace.",
    tag: "Zetaris SQL",
  },
  {
    who: "Evaluator",
    what: "Tries to break the evidence before anyone relies on it.",
    detail: "Nine refutations, from a plot drawn around the clearing to a delivery dated before the alert. When one lands, the card goes back to the Investigator.",
    tag: "R1 to R9",
  },
  {
    who: "Rules",
    what: "Decide. Code, not a model.",
    detail: "Proven, Contradicted or Unverifiable from fixed rules. Same data, same verdict, sealed with a fingerprint you can re-derive offline.",
    tag: "rules.py",
  },
  {
    who: "Writer",
    what: "Explains it in plain words.",
    detail: "Every sentence points to the rows that support it. The Meterless World Model holds each fact with its source, so a changed fact invalidates what depended on it.",
    tag: "Meterless World Model",
  },
];

export function HowItDecides() {
  return (
    <section id="how" className="mx-auto max-w-[1100px] scroll-mt-20 px-6 py-32 md:px-12">
      <h2 className="gt-display max-w-[16ch] text-4xl md:text-6xl">Six steps between a promise and a verdict.</h2>
      <p className="mt-6 max-w-[56ch] text-lg leading-relaxed text-gt-text/75">
        The model reads and writes. The code decides. Every step leaves a line in the trace, so the path from claim to verdict
        can be replayed.
      </p>
      <ol className="relative mt-20 border-l border-gt-line pl-8 md:pl-14">
        {STEPS.map((s, i) => (
          <li key={s.who} className="relative pb-14 last:pb-0">
            <span
              className="absolute -left-[calc(2rem+5px)] top-2 h-2.5 w-2.5 rounded-full md:-left-[calc(3.5rem+5px)]"
              style={{ background: s.who === "Rules" ? "var(--gt-signal)" : "var(--gt-line-strong)" }}
              aria-hidden
            />
            <div className="grid gap-3 md:grid-cols-[180px_1fr] md:gap-10">
              <div>
                <div className="gt-mono text-[12px] text-gt-muted">{String(i + 1).padStart(2, "0")}</div>
                <div className="mt-1 text-xl font-semibold tracking-tight">{s.who}</div>
                <div className="gt-mono mt-1 text-[12px] text-gt-signal/90">{s.tag}</div>
              </div>
              <div>
                <p className="text-2xl leading-snug tracking-tight text-gt-text">{s.what}</p>
                <p className="mt-3 max-w-[60ch] leading-relaxed text-gt-text/70">{s.detail}</p>
              </div>
            </div>
          </li>
        ))}
      </ol>
      <div className="mt-16 rounded-[14px] border border-gt-line bg-gt-raised p-8 md:ml-14">
        <div className="text-xl font-semibold tracking-tight">Then it argues with itself.</div>
        <p className="mt-3 max-w-[64ch] leading-relaxed text-gt-text/75">
          A Plaintiff makes the supplier&apos;s best case from the documents, citing every line. The Evaluator answers with its
          own queries. Over up to three rounds the rules judge each argument, and the verdict never moves on rhetoric alone.
        </p>
      </div>
    </section>
  );
}

// ---- 4. What would change its mind -------------------------------------------------------------

export function Flip({ map }: { map: PlotMap }) {
  const [delivered, setDelivered] = useState(true);
  return (
    <section className="border-y border-gt-line bg-gt-raised">
      <div className="mx-auto grid max-w-[1400px] items-center gap-14 px-6 py-28 md:grid-cols-2 md:px-12">
        <div>
          <h2 className="gt-display text-4xl md:text-6xl">What would change its mind.</h2>
          <p className="mt-6 max-w-[48ch] text-lg leading-relaxed text-gt-text/75">
            A verdict you cannot argue with is a verdict you cannot trust. GreenTrace computes the smallest change to the
            evidence that would turn it around, and lets you make that change.
          </p>
          <div className="mt-10 flex items-center gap-5">
            <button
              type="button"
              role="switch"
              aria-checked={delivered}
              onClick={() => setDelivered((d) => !d)}
              className="relative h-9 w-16 shrink-0 rounded-full border border-gt-line-strong bg-gt-ground transition-colors"
              style={{ background: delivered ? "rgba(217,98,43,0.25)" : "rgba(155,227,180,0.2)" }}
            >
              <span
                className="absolute top-1 h-6 w-6 rounded-full transition-[left,background] duration-300 ease-out"
                style={{ left: delivered ? "calc(100% - 1.75rem)" : "0.25rem", background: delivered ? "var(--gt-loss)" : "var(--gt-signal)" }}
              />
              <span className="sr-only">Deliveries from P-042 after 2020</span>
            </button>
            <div>
              <div className="text-[15px] text-gt-text">Deliveries from P-042 after 2020</div>
              <div className="gt-mono text-[12px] text-gt-muted">{delivered ? "yes, as recorded" : "none (what if)"}</div>
            </div>
          </div>
        </div>
        <div className="rounded-[14px] border border-gt-line bg-gt-ground p-8 md:p-10">
          <div className="gt-mono text-[12px] text-gt-muted">verdict</div>
          <div
            key={String(delivered)}
            className="gt-display gt-rise mt-2 text-5xl md:text-7xl"
            style={{ color: delivered ? "var(--gt-contradicted)" : "var(--gt-proven)" }}
          >
            <span>{delivered ? "Contradicted." : "Proven."}</span>
          </div>
          <p className="mt-6 text-lg leading-relaxed text-gt-text/80">
            {delivered
              ? "3.42 ha that the EU map counted as forest in 2020 were lost inside P-042 in 2021, 2022, 2023, 2024 (Hansen/UMD), while it kept delivering."
              : "The claim would be PROVEN if plot P-042 had delivered nothing after 2020."}
          </p>
          <p className="gt-mono mt-8 border-t border-gt-line pt-5 text-[12px] leading-5 text-gt-muted">
            Both sentences are the engine's own output for the real-plot example (flip.py, rules 2026-10-23.1). The demo
            supplier and its deliveries are fictional; the plot and its forest cells are real.
          </p>
        </div>
      </div>
    </section>
  );
}

// ---- 5. Honest when it cannot prove ------------------------------------------------------------

export function Honest({ map }: { map: PlotMap }) {
  const lostCover = Object.entries(map.measure.loss_ha_by_year)
    .filter(([y]) => Number(y) > map.grid.cutoff_year)
    .reduce((sum, [, ha]) => sum + ha, 0);
  return (
    <section className="relative">
      <PlotFigure
        map={map}
        image="p041-2025.jpg"
        layers="cover-loss"
        className="h-[88dvh] min-h-[560px] w-full"
        label="Plot P-041 on the Sentinel-2 scene of 5 February 2025, with tree cover lost after 2020 in grey"
      />
      <div className="absolute inset-0 bg-[linear-gradient(90deg,rgba(11,21,16,0.92)_0%,rgba(11,21,16,0.7)_45%,rgba(11,21,16,0.15)_100%)]" />
      <div className="absolute inset-0 mx-auto flex max-w-[1400px] items-center px-6 md:px-12">
        <div className="max-w-[40rem]">
          <h2 className="gt-display text-4xl md:text-6xl">It refuses to convict on a hunch.</h2>
          <p className="mt-6 text-lg leading-relaxed text-gt-text/85">
            Plot P-041 lost {lostCover.toFixed(2)} ha of tree cover after 2020. But the EU 2020 map does not count that land as
            forest ({fmtHa(map.measure.forest_2020_ha)} inside the outline). So GreenTrace answers{" "}
            <span className="font-semibold text-gt-unverifiable">Unverifiable</span>, names the missing proof, and asks for a
            field check. Not guilty, not cleared.
          </p>
        </div>
      </div>
    </section>
  );
}

// ---- 6. Proof you can check yourself -----------------------------------------------------------

export function Proof() {
  return (
    <section className="mx-auto max-w-[1400px] px-6 py-32 md:px-12">
      <h2 className="gt-display max-w-[18ch] text-4xl md:text-6xl">Do not trust it. Check it.</h2>
      <div className="mt-16 grid gap-4 md:grid-cols-6">
        <div className="rounded-[14px] border border-gt-line bg-gt-raised p-8 md:col-span-4">
          <div className="text-xl font-semibold tracking-tight">A receipt you can re-derive offline.</div>
          <p className="mt-3 max-w-[56ch] leading-relaxed text-gt-text/70">
            Each verdict ships with its inputs, the rows read and a seal. One command recomputes it without the model, without
            Zetaris and without network.
          </p>
          <pre className="gt-mono mt-8 overflow-x-auto rounded-[10px] border border-gt-line bg-gt-ground p-5 text-[13px] leading-6 text-gt-text/90">
            <span className="text-gt-muted">$</span> python verify.py output_examples/01_contradicted_receipt.json{"\n"}
            <span className="text-gt-proven">VALID</span>: CONTRADICTED re-derived offline from 522 rows of 17 answers; 0 quote(s)
            found word for word; flip replayed (4 change(s)).{"\n"}
            <span className="text-gt-muted">seal 24035b459821721e168619c808d35a6a03819b9feb113dac3ffabca4cba56eb0</span>
          </pre>
        </div>
        <div className="flex flex-col justify-between rounded-[14px] border border-gt-line bg-[linear-gradient(160deg,#14402B_0%,#0f2a1d_100%)] p-8 md:col-span-2">
          <div className="text-xl font-semibold tracking-tight">Same verdict, live or offline.</div>
          <p className="mt-3 leading-relaxed text-gt-text/80">
            Live run of 25 October 2026, every sponsor at once: 16 queries answered by Zetaris, facts held by the Meterless World
            Model, 5 Nemotron calls through Meterless Nightshift, each with a signed receipt that verifies. Verdict Contradicted,
            seal 24035b45, the same seal as the offline sample run. The model reads, the rules decide.
          </p>
        </div>
        <div className="rounded-[14px] border border-gt-line bg-gt-raised p-8 md:col-span-2">
          <div className="text-xl font-semibold tracking-tight">Real maps, cited.</div>
          <ul className="mt-5 space-y-3 text-[15px] leading-relaxed text-gt-text/75">
            <li>EU JRC Global Forest Cover 2020, v4: was it forest at the cut-off.</li>
            <li>Hansen/UMD Global Forest Change v1.12: which year it was lost.</li>
            <li>Sentinel-2 L2A scenes: what the plot looks like, year after year.</li>
          </ul>
        </div>
        <div className="rounded-[14px] border border-gt-line bg-gt-raised p-8 md:col-span-2">
          <div className="text-xl font-semibold tracking-tight">Each sponsor does one job.</div>
          <dl className="mt-5 grid grid-cols-[110px_1fr] gap-x-4 gap-y-3 text-[15px] leading-relaxed">
            <dt className="text-gt-text">Zetaris</dt>
            <dd className="text-gt-text/75">answers every query, and its data-quality rules catch broken records first.</dd>
            <dt className="text-gt-text">Meterless</dt>
            <dd className="text-gt-text/75">World Model and H-MEM: facts with their sources, invalidated when a fact changes. Nightshift routes every model call and signs its receipt.</dd>
            <dt className="text-gt-text">NVIDIA</dt>
            <dd className="text-gt-text/75">Nemotron reads the documents and writes the explanation.</dd>
          </dl>
        </div>
        <div className="rounded-[14px] border border-gt-line bg-gt-raised p-8 md:col-span-2">
          <div className="text-xl font-semibold tracking-tight">Open to other agents.</div>
          <p className="mt-3 leading-relaxed text-gt-text/75">
            An MCP server and an A2A agent expose the same five operations: check a claim, check plots, what if, explain,
            verify a receipt. Every trace line is also an OpenTelemetry span.
          </p>
          <pre className="gt-mono mt-6 overflow-x-auto rounded-[10px] border border-gt-line bg-gt-ground p-4 text-[12px] leading-5 text-gt-text/80">
            python -m app.standards.mcp_server{"\n"}GET /.well-known/agent-card.json
          </pre>
        </div>
      </div>
    </section>
  );
}

// ---- 7. Closing ---------------------------------------------------------------------------------

export function Closing() {
  return (
    <section className="mx-auto max-w-[1400px] px-6 pb-32 md:px-12">
      <div className="flex flex-col items-start justify-between gap-10 rounded-[14px] border border-gt-line bg-gt-raised p-10 md:flex-row md:items-end md:p-16">
        <div>
          <h2 className="gt-display max-w-[14ch] text-4xl md:text-6xl">Your plots. Your promise. Checked.</h2>
          <p className="mt-5 max-w-[46ch] text-lg leading-relaxed text-gt-text/75">
            Drop the GeoJSON file you send for EUDR due diligence. Results come back plot by plot, riskiest first.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <CheckPlotsCta />
          <WatchCta />
        </div>
      </div>
    </section>
  );
}
