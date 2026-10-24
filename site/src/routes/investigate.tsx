import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import { RunView } from "../components/gt/RunView";
import { Page } from "../components/gt/shell";
import { API, DEMO_CASE, REAL_PLOT_CASE, engine, type RunOutput, type TraceLine } from "../lib/gt";

type Search = { demo?: "real-plot" | "case-2" };

export const Route = createFileRoute("/investigate")({
  validateSearch: (search: Record<string, unknown>): Search => ({
    demo: search.demo === "real-plot" || search.demo === "case-2" ? search.demo : undefined,
  }),
  head: () => ({
    meta: [
      { title: "Investigate a claim · GreenTrace" },
      { name: "description", content: "Watch five agents and fixed rules test a deforestation-free promise, step by step." },
    ],
  }),
  component: InvestigatePage,
});

export type Live = {
  runId: string | null;
  lines: TraceLine[];
  output: RunOutput | null;
  error: string | null;
  running: boolean;
};

const EMPTY: Live = { runId: null, lines: [], output: null, error: null, running: false };

function InvestigatePage() {
  const { demo } = Route.useSearch();
  const [claim, setClaim] = useState(DEMO_CASE.input.claim);
  const [live, setLive] = useState<Live>(EMPTY);
  const source = useRef<EventSource | null>(null);
  const started = useRef(false);

  useEffect(() => () => source.current?.close(), []);

  async function start(body: unknown) {
    source.current?.close();
    setLive({ ...EMPTY, running: true });
    let runId: string;
    try {
      runId = (await engine<{ run_id: string }>("/runs", { json: body })).run_id;
    } catch (error) {
      setLive({ ...EMPTY, error: error instanceof Error ? error.message : String(error) });
      return;
    }
    setLive((l) => ({ ...l, runId }));
    const es = new EventSource(`${API}/runs/${runId}/events`);
    source.current = es;
    es.addEventListener("trace", (event) => {
      try {
        const line = JSON.parse((event as MessageEvent).data) as TraceLine;
        setLive((l) => ({ ...l, lines: [...l.lines, line] }));
      } catch {
        /* a line that is not JSON is skipped, the next one still arrives */
      }
    });
    es.addEventListener("result", (event) => {
      es.close();
      setLive((l) => ({ ...l, output: JSON.parse((event as MessageEvent).data) as RunOutput, running: false }));
    });
    es.addEventListener("failed", (event) => {
      es.close();
      const detail = JSON.parse((event as MessageEvent).data) as { error: string };
      setLive((l) => ({ ...l, error: detail.error, running: false }));
    });
    es.onerror = () => {
      // EventSource retries by itself; only give up once the run is known to be over.
      setLive((l) => (l.output || l.error ? l : { ...l, error: l.running ? null : "The connection to the engine dropped." }));
    };
  }

  useEffect(() => {
    if (!demo || started.current) return;
    started.current = true;
    if (demo === "real-plot") {
      setClaim(REAL_PLOT_CASE.input.claim);
      void start(REAL_PLOT_CASE);
    } else {
      void start(DEMO_CASE);
    }
    // start is stable enough for a one-shot demo launch
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [demo]);

  return (
    <Page>
      <section className="mx-auto max-w-[1200px] px-6 pb-12 pt-32 md:px-12">
        <h1 className="gt-display max-w-[16ch] text-5xl md:text-7xl">Investigate a claim.</h1>
        <p className="mt-6 max-w-[60ch] text-lg leading-relaxed text-gt-text/75">
          Type a sustainability promise. GreenTrace plans the checks, asks the data, tries to break its own evidence, and
          only then decides. The demo company, ChocoDurable, is fictional.
        </p>
        <form
          className="mt-10"
          onSubmit={(e) => {
            e.preventDefault();
            void start({ input: { claim: claim.trim(), company_id: DEMO_CASE.input.company_id }, options: DEMO_CASE.options });
          }}
        >
          <label htmlFor="claim" className="text-[14px] text-gt-text">
            The promise
          </label>
          <textarea
            id="claim"
            value={claim}
            onChange={(e) => setClaim(e.target.value)}
            rows={3}
            maxLength={1500}
            className="mt-2 w-full rounded-[14px] border border-gt-line bg-gt-raised p-5 text-xl leading-relaxed tracking-tight text-gt-text focus:border-gt-signal/60 focus:outline-none"
          />
          <div className="mt-5 flex flex-wrap items-center gap-3">
            <button
              type="submit"
              disabled={live.running || !claim.trim()}
              className="group inline-flex items-center gap-3 bg-gt-signal px-6 py-3.5 text-[15px] font-semibold text-gt-ground transition-transform active:translate-y-px disabled:opacity-50"
            >
              Check this claim
              <span className="transition-transform duration-300 group-hover:translate-x-1" aria-hidden>
                →
              </span>
            </button>
            <button
              type="button"
              disabled={live.running}
              onClick={() => {
                setClaim(REAL_PLOT_CASE.input.claim);
                void start(REAL_PLOT_CASE);
              }}
              className="gt-mono rounded-[10px] border border-gt-line-strong bg-gt-ground px-4 py-3 text-[13px] text-gt-text shadow-[0_3px_0_0_var(--gt-line-strong)] transition-[transform,box-shadow] active:translate-y-[3px] active:shadow-none disabled:opacity-50"
            >
              Run the real plot P-042
            </button>
            <button
              type="button"
              disabled={live.running}
              onClick={() => {
                setClaim(DEMO_CASE.input.claim);
                void start(DEMO_CASE);
              }}
              className="gt-mono rounded-[10px] border border-gt-line-strong bg-gt-ground px-4 py-3 text-[13px] text-gt-text shadow-[0_3px_0_0_var(--gt-line-strong)] transition-[transform,box-shadow] active:translate-y-[3px] active:shadow-none disabled:opacity-50"
            >
              Run the demo case
            </button>
          </div>
        </form>
      </section>
      <RunView live={live} />
    </Page>
  );
}
