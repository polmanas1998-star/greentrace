import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState, type DragEvent } from "react";

import { Page } from "../components/gt/shell";
import { API, engine, optional, verdictStyle, type PlotCheck, type PlotResult } from "../lib/gt";

export const Route = createFileRoute("/check")({
  head: () => ({
    meta: [
      { title: "Check your plots · GreenTrace" },
      { name: "description", content: "Upload an EUDR GeoJSON file: every plot read against the EU 2020 forest map and the Hansen loss years." },
    ],
  }),
  component: CheckPage,
});

type State =
  | { kind: "idle" }
  | { kind: "working"; file: string }
  | { kind: "error"; message: string }
  | { kind: "done"; file: string; result: PlotCheck };

const MAX_BYTES = 4 * 1024 * 1024;

function CheckPage() {
  const [state, setState] = useState<State>({ kind: "idle" });
  const [promise, setPromise] = useState("");
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function run(name: string, text: string) {
    let geojson: unknown;
    try {
      geojson = JSON.parse(text);
    } catch {
      setState({ kind: "error", message: `${name} is not valid JSON. Export the plots as GeoJSON (a FeatureCollection) and try again.` });
      return;
    }
    setState({ kind: "working", file: name });
    try {
      const body = promise.trim() ? { geojson, promise: promise.trim() } : { geojson };
      const result = await engine<PlotCheck>("/api/check-plots", { json: body });
      setState({ kind: "done", file: name, result });
    } catch (error) {
      setState({ kind: "error", message: error instanceof Error ? error.message : String(error) });
    }
  }

  async function onFile(file: File | undefined) {
    if (!file) return;
    if (file.size > MAX_BYTES) {
      setState({ kind: "error", message: `${file.name} is ${(file.size / 1e6).toFixed(1)} MB; the limit is 4 MB.` });
      return;
    }
    await run(file.name, await file.text());
  }

  async function example() {
    const text = await fetch("/data/example_plots.geojson").then((r) => r.text());
    await run("example_plots.geojson (9 plots, 3 example cooperatives)", text);
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setOver(false);
    void onFile(event.dataTransfer.files?.[0]);
  }

  return (
    <Page>
      <section className="mx-auto max-w-[1200px] px-6 pb-24 pt-32 md:px-12">
        <h1 className="gt-display max-w-[14ch] text-5xl md:text-7xl">Check your plots.</h1>
        <p className="mt-6 max-w-[58ch] text-lg leading-relaxed text-gt-text/75">
          Drop the GeoJSON file you prepare for EUDR due diligence. Each plot is read against the EU forest map of 2020 and the
          year its tree cover was lost, then ranked, riskiest first.
        </p>

        <div className="mt-12 grid gap-6 md:grid-cols-[1.4fr_1fr]">
          <label
            onDragOver={(e) => {
              e.preventDefault();
              setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={onDrop}
            className="flex min-h-[240px] cursor-pointer flex-col items-center justify-center rounded-[14px] border border-dashed p-10 text-center transition-colors"
            style={{ borderColor: over ? "var(--gt-signal)" : "var(--gt-line-strong)", background: over ? "rgba(155,227,180,0.06)" : "var(--gt-raised)" }}
          >
            <span className="text-xl font-semibold tracking-tight">Drop a .geojson file here</span>
            <span className="mt-2 text-gt-muted">or click to choose one. Polygons or points, 4 MB at most.</span>
            <input
              ref={input}
              type="file"
              accept=".geojson,.json,application/geo+json,application/json"
              className="sr-only"
              onChange={(e) => void onFile(e.target.files?.[0])}
            />
          </label>
          <div className="flex flex-col gap-4">
            <button
              type="button"
              onClick={() => void example()}
              disabled={state.kind === "working"}
              className="gt-mono rounded-[10px] border border-gt-line-strong bg-gt-ground px-5 py-4 text-left text-[14px] text-gt-text shadow-[0_3px_0_0_var(--gt-line-strong)] transition-[transform,box-shadow] active:translate-y-[3px] active:shadow-none disabled:opacity-50"
            >
              Use the example file
              <span className="mt-1 block font-sans text-[13px] text-gt-muted">9 real places near Soubré, 3 example cooperatives</span>
            </button>
            <div>
              <label htmlFor="promise" className="text-[14px] text-gt-text">
                Your promise <span className="text-gt-muted">(optional)</span>
              </label>
              <textarea
                id="promise"
                value={promise}
                onChange={(e) => setPromise(e.target.value)}
                rows={4}
                maxLength={2000}
                placeholder="e.g. Our cocoa is deforestation-free since 2021."
                className="mt-2 w-full rounded-[10px] border border-gt-line bg-gt-raised p-4 text-[15px] text-gt-text placeholder:text-gt-muted/70 focus:border-gt-signal/60 focus:outline-none"
              />
              <p className="mt-1 text-[13px] text-gt-muted">Judged against these plots when the model is available.</p>
            </div>
          </div>
        </div>

        <div aria-live="polite" className="mt-16">
          {state.kind === "working" && <Working file={state.file} />}
          {state.kind === "error" && (
            <div className="rounded-[14px] border border-gt-contradicted/40 bg-gt-raised p-6">
              <div className="font-semibold text-gt-contradicted">The check did not run.</div>
              <p className="mt-2 text-gt-text/80">{state.message}</p>
            </div>
          )}
          {state.kind === "done" && <Results result={state.result} file={state.file} />}
        </div>
      </section>
    </Page>
  );
}

function Working({ file }: { file: string }) {
  return (
    <div>
      <p className="gt-mono text-[13px] text-gt-muted">
        Reading {file}. The first request can take up to a minute while the engine wakes up.
      </p>
      <div className="mt-6 space-y-3">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="gt-skeleton h-16" style={{ opacity: 1 - i * 0.15 }} />
        ))}
      </div>
    </div>
  );
}

function Results({ result, file }: { result: PlotCheck; file: string }) {
  const plots = [...result.plots].sort((a, b) => a.risk_rank - b.risk_rank);
  const counts = result.summary.plots;
  return (
    <div>
      <div className="gt-mono text-[13px] text-gt-muted">{file}</div>
      <h2 className="gt-display mt-3 max-w-[30ch] text-3xl md:text-5xl">{result.summary.sentence}</h2>
      <div className="mt-6 flex flex-wrap gap-3">
        {(["CONTRADICTED", "UNVERIFIABLE", "NO_ISSUE"] as const).map((v) => (
          <span key={v} className="flex items-center gap-2 rounded-full border border-gt-line px-4 py-1.5 text-[14px]">
            <span className="h-2 w-2 rounded-full" style={{ background: verdictStyle(v).color }} aria-hidden />
            {counts[v] ?? 0} {verdictStyle(v).label.toLowerCase()}
          </span>
        ))}
        {result.summary.refused > 0 && (
          <span className="rounded-full border border-gt-line px-4 py-1.5 text-[14px] text-gt-muted">{result.summary.refused} refused</span>
        )}
      </div>

      {result.suppliers.length > 0 && (
        <div className="mt-14">
          <h3 className="text-xl font-semibold tracking-tight">Suppliers to look at first</h3>
          <ol className="mt-4 divide-y divide-gt-line border-y border-gt-line">
            {result.suppliers.map((s) => (
              <li key={s.supplier} className="grid gap-2 py-4 md:grid-cols-[40px_220px_1fr]">
                <span className="gt-num text-gt-muted">{s.rank}</span>
                <span className="font-medium">{s.supplier}</span>
                <span className="text-[15px] text-gt-text/70">{s.why}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      <div className="mt-14">
        <h3 className="text-xl font-semibold tracking-tight">Plot by plot</h3>
        <ul className="mt-4 divide-y divide-gt-line border-y border-gt-line">
          {plots.map((p) => (
            <PlotRow key={p.plot_id} plot={p} batchId={result.batch_id} />
          ))}
        </ul>
      </div>

      <div className="mt-14 flex flex-wrap items-center gap-3">
        <a
          href={`${API}${result.exports["package"]}`}
          className="relative inline-flex items-center gap-2 rounded-t-[10px] border border-b-0 border-gt-signal/50 bg-gt-raised px-5 py-3 text-[15px] font-medium text-gt-signal after:absolute after:-bottom-px after:left-0 after:right-0 after:h-px after:bg-gt-signal/50"
        >
          Download audit package
        </a>
        <a href={`${API}${result.exports["plots_no_issue_found.geojson"]}`} className="px-3 py-3 text-[15px] text-gt-text/80 underline-offset-4 hover:underline">
          Plots with no issue found (GeoJSON)
        </a>
        <a href={`${API}${result.exports["findings.csv"]}`} className="px-3 py-3 text-[15px] text-gt-text/80 underline-offset-4 hover:underline">
          Findings (CSV)
        </a>
        <a href={`${API}${result.exports["receipt"]}`} className="px-3 py-3 text-[15px] text-gt-text/80 underline-offset-4 hover:underline">
          Receipt (JSON)
        </a>
      </div>
      <p className="gt-mono mt-6 break-all text-[12px] text-gt-muted">
        seal {result.seal} · rules {result.rules_version}
      </p>
    </div>
  );
}

type Timelapse = {
  years: Array<{
    year: number;
    image: string | null;
    status: string;
    scene: { date?: string } | null;
    hansen_loss: { ha: number; ha_on_2020_forest: number } | null;
    hansen_note: string | null;
  }>;
};

type Passes = {
  status: string;
  reason?: string;
  tle_age_days?: number;
  note?: string;
  passes: Array<{ satellite: string; time: string; max_elevation_deg: number }>;
};

function NextPasses({ lat, lon }: { lat: number; lon: number }) {
  const [data, setData] = useState<Passes | null | "error">(null);
  useEffect(() => {
    let alive = true;
    optional<Passes>(`/api/plots/passes?lat=${lat}&lon=${lon}&days=30`)
      .then((d) => alive && setData(d))
      .catch(() => alive && setData("error"));
    return () => {
      alive = false;
    };
  }, [lat, lon]);
  if (data === null || data === "error") return null;
  return (
    <div className="mt-6">
      <div className="text-[14px] text-gt-muted">Next Sentinel-2 passes over the plot (30 days)</div>
      {data.status !== "success" ? (
        <p className="mt-2 text-[14px] text-gt-text/70">{data.reason}</p>
      ) : (
        <>
          <ul className="gt-num mt-2 flex flex-wrap gap-x-5 gap-y-1 text-[13px]">
            {data.passes.slice(0, 6).map((p) => (
              <li key={`${p.satellite}${p.time}`}>
                {p.satellite.replace("Sentinel-", "S")} {new Date(p.time).toUTCString().slice(5, 22)} UTC
              </li>
            ))}
          </ul>
          <a href={`${API}/api/plots/passes.ics?lat=${lat}&lon=${lon}`} className="mt-2 inline-block text-[14px] text-gt-signal underline-offset-4 hover:underline">
            Add the passes to a calendar (.ics)
          </a>
        </>
      )}
    </div>
  );
}

function PlotRow({ plot, batchId }: { plot: PlotResult; batchId: string }) {
  const [open, setOpen] = useState(false);
  const [tl, setTl] = useState<Timelapse | null>(null);
  const [tlError, setTlError] = useState<string | null>(null);
  const style = verdictStyle(plot.verdict);
  const m = (plot.measure ?? {}) as Record<string, unknown>;

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && !tl && !tlError) {
      try {
        setTl(await engine<Timelapse>(`/api/check-plots/${batchId}/plots/${encodeURIComponent(plot.plot_id)}/timelapse`));
      } catch (error) {
        setTlError(error instanceof Error ? error.message : String(error));
      }
    }
  }

  return (
    <li>
      <button type="button" onClick={() => void toggle()} aria-expanded={open} className="grid w-full gap-2 py-5 text-left md:grid-cols-[110px_190px_160px_1fr_24px] md:items-baseline">
        <span className="gt-mono text-[14px]">{plot.plot_id}</span>
        <span className="text-[14px] text-gt-text/70">{plot.supplier ?? "no supplier given"}</span>
        <span className="text-[14px] font-semibold" style={{ color: style.color }}>
          {style.label}
        </span>
        <span className="text-[15px] leading-relaxed text-gt-text/85">{plot.sentence}</span>
        <span className="hidden text-gt-muted md:block" aria-hidden>
          {open ? "−" : "+"}
        </span>
      </button>
      {open && (
        <div className="grid gap-8 pb-8 md:grid-cols-[1fr_1.4fr]">
          <dl className="grid grid-cols-[1fr_auto] gap-x-6 gap-y-2 text-[14px]">
            <dt className="text-gt-muted">Declared shape</dt>
            <dd className="gt-num">
              {plot.shape}
              {plot.area_ha !== null ? `, ${plot.area_ha} ha` : ""}
            </dd>
            <dt className="text-gt-muted">EU forest in 2020 inside</dt>
            <dd className="gt-num">{typeof m.forest_2020_ha === "number" ? `${m.forest_2020_ha} ha` : "n/a"}</dd>
            <dt className="text-gt-muted">Forest lost after 2020</dt>
            <dd className="gt-num">{typeof m.loss_after_2020_ha === "number" ? `${m.loss_after_2020_ha} ha` : "n/a"}</dd>
            <dt className="text-gt-muted">Rule</dt>
            <dd className="gt-num">{plot.rule ?? "none"}</dd>
          </dl>
          <div>
            <div className="text-[14px] text-gt-muted">Year by year in the 1.2 km frame around the plot (Hansen loss on EU 2020 forest, ha)</div>
            {tlError && <p className="mt-2 text-[14px] text-gt-text/70">{tlError}</p>}
            {!tl && !tlError && <div className="gt-skeleton mt-3 h-24" />}
            {tl && (
              <div className="mt-3 flex items-end gap-2 overflow-x-auto pb-2">
                {tl.years.map((y) => {
                  const ha = y.hansen_loss?.ha_on_2020_forest ?? 0;
                  return (
                    <div key={y.year} className="flex w-16 shrink-0 flex-col items-center gap-1.5">
                      {y.image ? (
                        <img src={y.image.startsWith("/") ? `${API}${y.image}` : y.image} alt={`Scene ${y.scene?.date ?? y.year}`} className="h-12 w-16 rounded object-cover" />
                      ) : (
                        <div className="flex h-12 w-16 items-center justify-center rounded border border-gt-line text-[10px] text-gt-muted" title={y.status}>
                          no scene
                        </div>
                      )}
                      <div className="h-12 w-3 rounded-sm bg-gt-line">
                        <div className="w-full rounded-sm" style={{ height: `${Math.min(100, ha * 30)}%`, marginTop: `${100 - Math.min(100, ha * 30)}%`, background: y.year > 2020 ? "var(--gt-loss)" : "var(--gt-muted)" }} />
                      </div>
                      <span className="gt-num text-[11px] text-gt-muted">{y.year}</span>
                      <span className="gt-num text-[11px]">{y.hansen_loss ? ha.toFixed(2) : "-"}</span>
                    </div>
                  );
                })}
              </div>
            )}
            <NextPasses lat={plot.centre[0]} lon={plot.centre[1]} />
          </div>
        </div>
      )}
    </li>
  );
}
