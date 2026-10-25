import { lazy, Suspense, useEffect, useState } from "react";

import { API, optional, verdictStyle, type RunOutput, type TraceLine } from "../../lib/gt";
import type { Live } from "../../routes/investigate";
import { AnalysisScene } from "./AnalysisScene";
import { OPTIONAL_TABS, WhatIfTab, WhyTab } from "./runTabs";

// The chat is only needed when its tab opens: loaded on demand.
const AskPanel = lazy(() => import("./AskPanel"));

const AGENT_COLOR: Record<string, string> = {
  Planner: "#C9D6CE",
  Extractor: "#B8D8F0",
  Investigator: "#9BE3B4",
  Evaluator: "#F0C27A",
  Writer: "#D9C6F0",
  Plaintiff: "#F0A9A0",
  Judge: "#E8EFEA",
};

export function RunView({ live }: { live: Live }) {
  if (!live.runId && !live.error && !live.running) return null;
  return (
    <section className="mx-auto max-w-[1200px] px-6 pb-32 md:px-12" aria-live="polite">
      {live.error && (
        <div className="mb-10 rounded-[14px] border border-gt-contradicted/40 bg-gt-raised p-6">
          <div className="font-semibold text-gt-contradicted">The investigation stopped.</div>
          <p className="mt-2 text-gt-text/80">{live.error}</p>
        </div>
      )}
      {live.output ? <Result output={live.output} lines={live.lines} /> : <LiveTrace lines={live.lines} running={live.running} />}
    </section>
  );
}

function LiveTrace({ lines, running }: { lines: TraceLine[]; running: boolean }) {
  return (
    <div>
      {running && <AnalysisScene lines={lines} title="Investigating" />}
      <div className="mt-6 rounded-[14px] border border-gt-line bg-gt-raised">
        <div className="flex items-center justify-between border-b border-gt-line px-6 py-4">
          <span className="text-[15px] font-semibold tracking-tight">Live trace</span>
          <span className="gt-mono text-[12px] text-gt-muted">
            {lines.length} line{lines.length === 1 ? "" : "s"}
            {running ? " · running" : ""}
          </span>
        </div>
        {lines.length === 0 && running && (
          <p className="gt-mono px-6 py-5 text-[13px] text-gt-muted">Starting. The first request can take up to a minute while the engine wakes up.</p>
        )}
        <TraceList lines={lines} animate />
      </div>
    </div>
  );
}

export function TraceList({ lines, animate = false }: { lines: TraceLine[]; animate?: boolean }) {
  return (
    <ol className="max-h-[560px] overflow-y-auto">
      {lines.map((l, i) => (
        <li key={i} className={`grid gap-1 border-b border-gt-line px-6 py-3 last:border-b-0 md:grid-cols-[120px_170px_1fr_120px] md:gap-4 ${animate ? "gt-line-in" : ""}`}>
          <span className="gt-mono text-[12px]" style={{ color: AGENT_COLOR[l.agent_name] ?? "var(--gt-text)" }}>
            {l.agent_name}
            {l.target_agent ? <span className="text-gt-muted"> → {l.target_agent}</span> : null}
          </span>
          <span className="gt-mono text-[12px] text-gt-muted">{l.action}</span>
          <span className="text-[14px] leading-relaxed text-gt-text/85">{l.output_summary ?? l.input_summary}</span>
          <span className="gt-mono text-[11px] text-gt-muted md:text-right">
            {l.provider ?? ""}
            {l.rows !== null && l.rows !== undefined ? ` · ${l.rows} rows` : ""}
            {l.status !== "success" ? ` · ${l.status}` : ""}
          </span>
        </li>
      ))}
    </ol>
  );
}

type Plain = { sentence: string; verdict: string | null };

function Result({ output, lines }: { output: RunOutput; lines: TraceLine[] }) {
  const runId = output.run_id;
  const [plain, setPlain] = useState<Plain | null>(null);
  const [extra, setExtra] = useState<Record<string, unknown>>({});
  const [tab, setTab] = useState("why");
  const [more, setMore] = useState(false);
  const style = verdictStyle(output.verdict);

  useEffect(() => {
    let alive = true;
    void optional<Plain>(`/ui/runs/${runId}/plain`).then((p) => alive && setPlain(p));
    // Panels from newer engine versions show up only when the engine answers for them.
    for (const t of OPTIONAL_TABS) {
      void optional<unknown>(t.path(runId))
        .then((data) => {
          if (alive && data) setExtra((e) => ({ ...e, [t.id]: data }));
        })
        .catch(() => undefined);
    }
    return () => {
      alive = false;
    };
  }, [runId]);

  const refresh = (id: string) => {
    const t = OPTIONAL_TABS.find((x) => x.id === id);
    if (!t) return;
    void optional<unknown>(t.path(runId)).then((data) => data && setExtra((e) => ({ ...e, [id]: data })));
  };

  // A judge has minutes: the four views that explain the verdict come first, the rest one click away.
  const PRIMARY = new Set(["court"]);
  const available = OPTIONAL_TABS.filter((t) => extra[t.id]);
  const tabs: Array<{ id: string; label: string }> = [
    { id: "why", label: "Why" },
    { id: "whatif", label: "What would change it" },
    { id: "ask", label: "Ask GreenTrace" },
    ...available.filter((t) => PRIMARY.has(t.id)).map((t) => ({ id: t.id, label: t.label })),
  ];
  const moreTabs: Array<{ id: string; label: string }> = [
    ...available.filter((t) => !PRIMARY.has(t.id)).map((t) => ({ id: t.id, label: t.label })),
    { id: "trace", label: `Trace (${lines.length})` },
  ];
  const active = OPTIONAL_TABS.find((t) => t.id === tab);

  if (output.status === "too_vague" || !output.verdict) {
    return (
      <div className="rounded-[14px] border border-gt-line bg-gt-raised p-8">
        <div className="gt-display text-4xl text-gt-unverifiable">Nothing to test.</div>
        <p className="mt-4 max-w-[60ch] text-lg leading-relaxed text-gt-text/80">
          {plain?.sentence ?? output.summary ?? "This promise names nothing the data can test."}
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="grid gap-10 md:grid-cols-[1.3fr_1fr]">
        <div>
          <div className="gt-mono text-[12px] text-gt-muted">
            verdict · {output.mode ?? "?"} mode{output.degraded ? " · rules in place of the model" : ""}
            {output.execution_time_seconds !== undefined ? ` · ${output.execution_time_seconds}s` : ""}
          </div>
          <div className="gt-display gt-rise mt-2 text-6xl md:text-8xl" style={{ color: style.color }}>
            <span>{style.label}.</span>
          </div>
          <p className="mt-6 max-w-[56ch] text-xl leading-relaxed text-gt-text/90">{plain?.sentence ?? output.summary}</p>
        </div>
        <div className="self-end rounded-[14px] border border-gt-line bg-gt-raised p-6">
          {output.flip && (
            <>
              <div className="text-[14px] text-gt-muted">What would change its mind</div>
              <p className="mt-2 text-lg leading-relaxed">{output.flip}</p>
            </>
          )}
          {output.human_review && <p className="mt-4 text-[14px] text-gt-text/70">Human review: {output.human_review.charAt(0).toLowerCase() + output.human_review.slice(1)}</p>}
          {output.eudr_next && <p className="mt-3 text-[14px] leading-relaxed text-gt-text/80"><span className="font-semibold text-gt-text">What the EUDR asks next:</span> {output.eudr_next}</p>}
          <div className="mt-6 flex flex-wrap gap-4 text-[14px]">
            <a href={`${API}/runs/${runId}/package`} className="text-gt-signal underline-offset-4 hover:underline">
              Audit package (zip)
            </a>
            <a href={`${API}/runs/${runId}/receipt`} className="text-gt-text/80 underline-offset-4 hover:underline">
              Receipt (JSON)
            </a>
          </div>
          {output.verdict_sha256 && <p className="gt-mono mt-4 break-all text-[11px] text-gt-muted">seal {output.verdict_sha256}</p>}
        </div>
      </div>

      <div className="mt-14 flex gap-1 overflow-x-auto border-b border-gt-line" role="tablist">
        {[...tabs, ...(more || moreTabs.some((t) => t.id === tab) ? moreTabs : [])].map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className="-mb-px shrink-0 border-b-2 px-4 py-3 text-[14px] transition-colors"
            style={{ borderColor: tab === t.id ? "var(--gt-signal)" : "transparent", color: tab === t.id ? "var(--gt-text)" : "var(--gt-muted)" }}
          >
            {t.label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setMore((m) => !m)}
          className="-mb-px ml-auto shrink-0 px-4 py-3 text-[14px] text-gt-signal"
          aria-expanded={more}
        >
          {more ? "Fewer views" : `More views (${moreTabs.length})`}
        </button>
      </div>
      <div className="pt-8" role="tabpanel">
        {tab === "why" && <WhyTab output={output} />}
        {tab === "whatif" && <WhatIfTab runId={runId} output={output} onChanged={() => ["world_diff", "graph", "sentences"].forEach(refresh)} />}
        {tab === "ask" && (
          <Suspense fallback={<div className="gt-skeleton h-[560px]" />}>
            <AskPanel runId={runId} />
          </Suspense>
        )}
        {tab === "trace" && (
          <div className="rounded-[14px] border border-gt-line bg-gt-raised">
            <TraceList lines={lines} />
          </div>
        )}
        {active && extra[active.id] ? active.render(extra[active.id], runId) : null}
      </div>
    </div>
  );
}

