import { useState, type ReactNode } from "react";

import { API, engine, verdictStyle, type RunOutput } from "../../lib/gt";

// ---- Why: the findings, the evidence they cite, and every refutation tried ---------------------

type Finding = {
  finding?: string;
  verdict?: string;
  rule?: number;
  code?: string;
  evidence?: Array<{ source: string; id: string }>;
  checked_by?: string[];
  refutations_tried?: Array<{ id: string; result: string; detail: string }>;
};

export function WhyTab({ output }: { output: RunOutput }) {
  const findings = (output.findings ?? []) as Finding[];
  return (
    <div className="space-y-6">
      {findings.length === 0 && <p className="text-gt-text/70">{output.summary}</p>}
      {findings.map((f, i) => {
        const s = verdictStyle(f.verdict);
        return (
          <article key={i} className="rounded-[14px] border border-gt-line bg-gt-raised p-7">
            <div className="flex flex-wrap items-baseline gap-3">
              <span className="font-semibold" style={{ color: s.color }}>
                {s.label}
              </span>
              {f.rule !== undefined && <span className="gt-mono text-[12px] text-gt-muted">rule {f.rule}</span>}
              {f.code && <span className="gt-mono text-[12px] text-gt-muted">{f.code}</span>}
            </div>
            <p className="mt-3 text-lg leading-relaxed">{f.finding}</p>
            {f.evidence && f.evidence.length > 0 && (
              <div className="mt-4 flex flex-wrap gap-2">
                {f.evidence.map((e) => (
                  <span key={`${e.source}:${e.id}`} className="gt-mono rounded-md border border-gt-line px-2.5 py-1 text-[12px] text-gt-text/80">
                    {e.source} {e.id}
                  </span>
                ))}
              </div>
            )}
            {f.refutations_tried && f.refutations_tried.length > 0 && (
              <div className="mt-6">
                <div className="text-[14px] text-gt-muted">The Evaluator tried to break it</div>
                <ul className="mt-2 divide-y divide-gt-line">
                  {f.refutations_tried.map((r) => (
                    <li key={r.id} className="grid gap-1 py-2.5 md:grid-cols-[60px_110px_1fr]">
                      <span className="gt-mono text-[13px]">{r.id}</span>
                      <span className="gt-mono text-[12px]" style={{ color: r.result === "rejected" ? "var(--gt-muted)" : "var(--gt-contradicted)" }}>
                        {r.result === "rejected" ? "did not hold" : r.result}
                      </span>
                      <span className="text-[14px] text-gt-text/80">{r.detail}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </article>
        );
      })}
    </div>
  );
}

// ---- What would change it: the candidates flip.py tried, each one a real what_if -------------

type FlipTry = { change: { entity: string; field: string; from: unknown; to: unknown; relative_to?: string }; verdict: string };
type WhatIfAnswer = { verdict?: string; summary?: string; flip?: string | null; changed?: boolean } & Record<string, unknown>;

function show(value: unknown): string {
  if (Array.isArray(value)) return value.map((v) => (typeof v === "number" ? v.toFixed(4) : String(v))).join(", ");
  return String(value);
}

export function WhatIfTab({ runId, output, onChanged }: { runId: string; output: RunOutput; onChanged: () => void }) {
  const tried = ((output as unknown as { flip_tried?: FlipTry[] }).flip_tried ?? []).filter((t) => t.change && t.change.to !== undefined);
  const [answers, setAnswers] = useState<Record<number, WhatIfAnswer | string>>({});
  const [busy, setBusy] = useState<number | null>(null);

  async function apply(i: number, t: FlipTry) {
    setBusy(i);
    try {
      const answer = await engine<WhatIfAnswer>(`/runs/${runId}/what_if`, {
        json: { entity: t.change.entity, field: t.change.field, value: t.change.to },
      });
      setAnswers((a) => ({ ...a, [i]: answer }));
      onChanged();
    } catch (error) {
      setAnswers((a) => ({ ...a, [i]: error instanceof Error ? error.message : String(error) }));
    } finally {
      setBusy(null);
    }
  }

  if (tried.length === 0) return <p className="text-gt-text/70">No single change to the evidence turns this verdict around.</p>;
  return (
    <div>
      <p className="max-w-[64ch] text-gt-text/75">
        Each line is a change flip.py tried on the evidence. Apply one: the engine re-runs the checks on the changed fact and
        answers with the new verdict.
      </p>
      <ul className="mt-6 space-y-3">
        {tried.map((t, i) => {
          const answer = answers[i];
          const target = verdictStyle(t.verdict);
          return (
            <li key={i} className="rounded-[14px] border border-gt-line bg-gt-raised p-5">
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div className="text-[15px]">
                  <span className="gt-mono text-gt-text">{t.change.entity}</span>
                  <span className="text-gt-muted"> · {t.change.field}: </span>
                  <span className="gt-mono text-gt-text/70 line-through decoration-gt-muted/60">{show(t.change.from)}</span>
                  <span className="text-gt-muted"> → </span>
                  <span className="gt-mono text-gt-text">{show(t.change.to)}</span>
                  {t.change.relative_to && <span className="text-gt-muted"> (moved away from {t.change.relative_to})</span>}
                </div>
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void apply(i, t)}
                  className="rounded-full border px-4 py-2 text-[14px] transition-colors disabled:opacity-50"
                  style={{ borderColor: target.color, color: target.color }}
                >
                  {busy === i ? "Re-checking…" : `Apply: would be ${target.label.toLowerCase()}`}
                </button>
              </div>
              {answer !== undefined && (
                <div className="mt-4 border-t border-gt-line pt-4 text-[15px]">
                  {typeof answer === "string" ? (
                    <span className="text-gt-contradicted">{answer}</span>
                  ) : (
                    <>
                      <span className="font-semibold" style={{ color: verdictStyle(answer.verdict).color }}>
                        {verdictStyle(answer.verdict).label}.
                      </span>{" "}
                      <span className="text-gt-text/85">{String(answer.summary ?? "")}</span>
                    </>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ---- Panels from newer engine versions (shown only when the engine answers) -------------------

type Court = {
  rounds: Array<{
    n: number;
    plaintiff: Array<{ id: string; claim: string; status: string; cites: Array<{ source: string; ref: string; quote?: string }> }>;
    defense: Array<{ id: string; answers: string; refutation: string; query_id: string; sql?: string; holds: boolean }>;
    judge: { rule: string; finding: string; standing: string[]; refuted: string[] };
  }>;
  judgment: string;
  provider: string;
};

function CourtTab({ data }: { data: Court }) {
  return (
    <div className="space-y-10">
      {data.rounds.map((r) => (
        <div key={r.n}>
          <div className="gt-mono text-[12px] text-gt-muted">round {r.n}</div>
          <div className="mt-3 grid gap-4 md:grid-cols-2">
            <div className="rounded-[14px] border border-gt-line bg-gt-raised p-6">
              <div className="font-semibold tracking-tight">Plaintiff, for the supplier</div>
              <ul className="mt-4 space-y-4">
                {r.plaintiff.map((p) => (
                  <li key={p.id}>
                    <div className="text-[15px] leading-relaxed">
                      <span className="gt-mono text-gt-muted">{p.id} </span>
                      {p.claim}
                    </div>
                    <div className="gt-mono mt-1 text-[12px]" style={{ color: p.status === "standing" ? "var(--gt-proven)" : "var(--gt-contradicted)" }}>
                      {p.status} · {p.cites.map((c) => `${c.source} ${c.ref}`).join(", ")}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
            <div className="rounded-[14px] border border-gt-line bg-gt-raised p-6">
              <div className="font-semibold tracking-tight">Defense, the Evaluator</div>
              <ul className="mt-4 space-y-4">
                {r.defense.map((d) => (
                  <li key={d.id}>
                    <div className="text-[15px]">
                      <span className="gt-mono text-gt-muted">{d.id} </span>
                      answers {d.answers} with {d.refutation} ({d.query_id}): {d.holds ? "holds" : "does not hold"}
                    </div>
                    {d.sql && <pre className="gt-mono mt-2 overflow-x-auto rounded-md bg-gt-ground p-3 text-[11px] text-gt-text/70">{d.sql}</pre>}
                  </li>
                ))}
              </ul>
            </div>
          </div>
          <div className="mt-4 rounded-[14px] border border-gt-signal/30 p-5 text-[15px]">
            <span className="font-semibold">Judge (the rules), {r.judge.rule}: </span>
            {r.judge.finding}
          </div>
        </div>
      ))}
      <p className="text-lg leading-relaxed">{data.judgment}</p>
    </div>
  );
}

type Attacks = { attacks: Array<{ refutation: string; question: string; query_id: string; result: string; rows: number; retry_count: number }> };

function AttacksTab({ data }: { data: Attacks }) {
  return (
    <ul className="divide-y divide-gt-line border-y border-gt-line">
      {data.attacks.map((a, i) => (
        <li key={i} className="grid gap-1 py-3 md:grid-cols-[60px_1fr_170px_80px]">
          <span className="gt-mono text-[13px]">{a.refutation}</span>
          <span className="text-[14px] text-gt-text/85">{a.question}</span>
          <span className="gt-mono text-[12px]" style={{ color: a.result === "refuted_evidence" ? "var(--gt-contradicted)" : "var(--gt-muted)" }}>
            {a.result.replaceAll("_", " ")}
          </span>
          <span className="gt-mono text-[12px] text-gt-muted">
            {a.query_id} · {a.rows} rows
          </span>
        </li>
      ))}
    </ul>
  );
}

type Sentences = { sentences: Array<{ n: number; text: string; unsupported?: boolean; evidence: Array<{ query_id: string; engine: string; sql: string; row_count: number }> }> };

function SentencesTab({ data }: { data: Sentences }) {
  const [open, setOpen] = useState<number | null>(0);
  return (
    <div className="space-y-2">
      <p className="mb-4 text-gt-text/70">Click a sentence of the report to see the query that supports it.</p>
      {data.sentences.map((s) => (
        <div key={s.n} className="rounded-[14px] border border-gt-line bg-gt-raised">
          <button type="button" onClick={() => setOpen(open === s.n ? null : s.n)} className="w-full p-5 text-left text-[16px] leading-relaxed" aria-expanded={open === s.n}>
            {s.text}
            {s.unsupported && <span className="ml-2 text-[13px] text-gt-contradicted">no supporting row</span>}
          </button>
          {open === s.n && (
            <div className="space-y-3 border-t border-gt-line p-5">
              {s.evidence.map((e, i) => (
                <div key={i}>
                  <div className="gt-mono text-[12px] text-gt-muted">
                    {e.query_id} on {e.engine} · {e.row_count} row(s)
                  </div>
                  <pre className="gt-mono mt-1 overflow-x-auto rounded-md bg-gt-ground p-3 text-[12px] leading-5 text-gt-text/80">{e.sql}</pre>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

type Graph = { nodes: Array<{ id: string; kind: string; label: string }>; edges: Array<{ from: string; to: string; relation: string }>; verdict_node: string };

const KIND_ORDER = ["source", "query", "fact", "rule", "sentence", "verdict"];

function GraphTab({ data }: { data: Graph }) {
  const [focus, setFocus] = useState<string | null>(null);
  const columns = KIND_ORDER.map((k) => data.nodes.filter((n) => n.kind === k)).filter((c) => c.length > 0);
  const linked = new Set<string>();
  if (focus) {
    for (const e of data.edges) {
      if (e.from === focus) linked.add(e.to);
      if (e.to === focus) linked.add(e.from);
    }
  }
  return (
    <div>
      <p className="mb-6 text-gt-text/70">Every node is a fact, a query, a rule or a sentence. Click one to see what it supports and what supports it.</p>
      <div className="grid gap-4 overflow-x-auto" style={{ gridTemplateColumns: `repeat(${columns.length}, minmax(180px, 1fr))` }}>
        {columns.map((col) => (
          <div key={col[0].kind} className="space-y-2">
            <div className="gt-mono text-[12px] text-gt-muted">{col[0].kind}</div>
            {col.map((n) => {
              const on = focus === n.id || linked.has(n.id);
              return (
                <button
                  key={n.id}
                  type="button"
                  onClick={() => setFocus(focus === n.id ? null : n.id)}
                  className="block w-full rounded-[10px] border p-3 text-left text-[13px] leading-snug transition-colors"
                  style={{
                    borderColor: on ? "var(--gt-signal)" : "var(--gt-line)",
                    background: n.id === data.verdict_node ? "rgba(155,227,180,0.08)" : "var(--gt-raised)",
                    opacity: focus && !on ? 0.45 : 1,
                  }}
                >
                  {n.label}
                </button>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

type WorldDiff = {
  changed: Array<{ fact: string; before: unknown; after: unknown }>;
  superseded: Array<{ fact: string; by: string; reason: string }>;
  invalidated: Array<{ fact: string; because_of: string }>;
  verdict: { before: string; after: string };
};

function WorldDiffTab({ data }: { data: WorldDiff }) {
  return (
    <div className="grid gap-4 md:grid-cols-3">
      <div className="rounded-[14px] border border-gt-line bg-gt-raised p-6 md:col-span-3">
        <span style={{ color: verdictStyle(data.verdict.before).color }}>{verdictStyle(data.verdict.before).label}</span>
        <span className="text-gt-muted"> → </span>
        <span style={{ color: verdictStyle(data.verdict.after).color }}>{verdictStyle(data.verdict.after).label}</span>
        <span className="ml-3 text-[14px] text-gt-muted">in the Meterless World Model after your what-if</span>
      </div>
      {(
        [
          ["Changed", data.changed.map((c) => `${c.fact}: ${show(c.before)} → ${show(c.after)}`)],
          ["Superseded", data.superseded.map((s) => `${s.fact} by ${s.by}: ${s.reason}`)],
          ["Invalidated", data.invalidated.map((s) => `${s.fact} (because of ${s.because_of})`)],
        ] as Array<[string, string[]]>
      ).map(([title, items]) => (
        <div key={title} className="rounded-[14px] border border-gt-line bg-gt-raised p-6">
          <div className="font-semibold tracking-tight">{title}</div>
          {items.length === 0 ? (
            <p className="mt-3 text-[14px] text-gt-muted">nothing</p>
          ) : (
            <ul className="gt-mono mt-3 space-y-2 text-[12px] leading-5 text-gt-text/80">
              {items.map((t) => (
                <li key={t}>{t}</li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}

type Calendar = { events: Array<{ kind: string; title: string; start: string; google_calendar_url: string }> };

function CalendarTab({ data, runId }: { data: Calendar; runId: string }) {
  return (
    <div>
      <a href={`${API}/runs/${runId}/calendar.ics`} className="text-gt-signal underline-offset-4 hover:underline">
        Download all as a calendar file (.ics)
      </a>
      <ul className="mt-6 divide-y divide-gt-line border-y border-gt-line">
        {data.events.map((e, i) => (
          <li key={i} className="grid gap-1 py-3 md:grid-cols-[110px_1fr_200px_auto] md:items-baseline">
            <span className="gt-mono text-[12px] text-gt-muted">{e.kind.replaceAll("_", " ")}</span>
            <span className="text-[15px]">{e.title}</span>
            <span className="gt-num text-[13px] text-gt-text/70">{new Date(e.start).toUTCString().slice(0, 22)}</span>
            <a href={e.google_calendar_url} target="_blank" rel="noreferrer" className="text-[14px] text-gt-signal underline-offset-4 hover:underline">
              Add to Google Calendar
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

type Radar = { items?: Array<{ claim: string; status: string; needs?: string; would_flip?: boolean }> } & Record<string, unknown>;

function RadarTab({ data }: { data: Radar }) {
  const groups = Object.entries(data).filter(([, v]) => Array.isArray(v)) as Array<[string, Array<Record<string, unknown>>]>;
  return (
    <div className="grid gap-4 md:grid-cols-3">
      {groups.map(([name, items]) => (
        <div key={name} className="rounded-[14px] border border-gt-line bg-gt-raised p-6">
          <div className="font-semibold tracking-tight">{name.replaceAll("_", " ")}</div>
          <ul className="mt-3 space-y-3 text-[14px] leading-relaxed text-gt-text/80">
            {items.map((it, i) => (
              <li key={i}>
                {String(it.claim ?? it.document ?? it.text ?? JSON.stringify(it))}
                {it.would_flip !== undefined && (
                  <span className="gt-mono ml-2 text-[12px]" style={{ color: it.would_flip ? "var(--gt-signal)" : "var(--gt-muted)" }}>
                    {it.would_flip ? "would flip the verdict" : "would not flip it"}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

export const OPTIONAL_TABS: Array<{ id: string; label: string; path: (runId: string) => string; render: (data: unknown, runId: string) => ReactNode }> = [
  { id: "court", label: "Court", path: (r) => `/runs/${r}/court`, render: (d) => <CourtTab data={d as Court} /> },
  { id: "attacks", label: "Attack log", path: (r) => `/runs/${r}/attacks`, render: (d) => <AttacksTab data={d as Attacks} /> },
  { id: "sentences", label: "Deep trace", path: (r) => `/runs/${r}/sentences`, render: (d) => <SentencesTab data={d as Sentences} /> },
  { id: "graph", label: "Evidence graph", path: (r) => `/runs/${r}/graph`, render: (d) => <GraphTab data={d as Graph} /> },
  { id: "world_diff", label: "Memory before / after", path: (r) => `/runs/${r}/world_diff`, render: (d) => <WorldDiffTab data={d as WorldDiff} /> },
  { id: "radar", label: "Contradiction radar", path: (r) => `/runs/${r}/radar`, render: (d) => <RadarTab data={d as Radar} /> },
  { id: "calendar", label: "Calendar", path: (r) => `/runs/${r}/calendar`, render: (d, r) => <CalendarTab data={d as Calendar} runId={r} /> },
];
