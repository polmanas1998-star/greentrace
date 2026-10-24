import { useEffect, useMemo, useState } from "react";

import worldJson from "../../data/world.json";
import type { TraceLine } from "../../lib/gt";

// What the visitor watches while the engine works: the world, then the supply area, then the plot,
// driven by the run itself. The camera flies to the box the Investigator really queries (the lat/lon
// of Q4, Q10 or a plot file), and the readouts are the trace lines as they arrive. Land: Natural Earth
// 1:110m (public domain), sampled every 2 degrees; country outlines simplified.

type World = { step: number; dots: number[]; west: Record<string, { name: string; d: string }> };
const WORLD = worldJson as unknown as World;

export type Target = { lat: number; lon: number; label?: string };
// The scene needs this long to fly from the world to the plot; a sample run takes ~1.5 s. The result
// waits for it; the trace lines and the run time shown are the real ones.
export const MIN_SCENE_MS = 5600;

const COTE_DIVOIRE: Target = { lat: 7.0, lon: -5.6, label: "Côte d'Ivoire" };

const STAGES = [
  { at: 0, scale: 1, label: "Reading the promise" },
  { at: 1400, scale: 7, label: "Locating the supply area" },
  { at: 3600, scale: 70, label: "Reading the 30 m forest cells" },
] as const;

const BOX = /lat_min=(-?[\d.]+), lat_max=(-?[\d.]+), lon_min=(-?[\d.]+), lon_max=(-?[\d.]+)/;

/** The centre of the last box a trace line queried, if any. */
export function targetFromTrace(lines: TraceLine[]): Target | null {
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const m = BOX.exec(lines[i].input_summary ?? "");
    if (m) return { lat: (Number(m[1]) + Number(m[2])) / 2, lon: (Number(m[3]) + Number(m[4])) / 2 };
  }
  return null;
}

function phaseOf(lines: TraceLine[]): string {
  const last = lines[lines.length - 1];
  if (!last) return "";
  if (last.agent_name === "Evaluator") return "Trying to break the evidence";
  if (last.agent_name === "Writer" || last.agent_name === "Judge") return "Applying the rules";
  if (last.agent_name === "Plaintiff") return "Hearing the supplier's side";
  if (last.action === "query_zetaris") return "Asking the data";
  return "";
}

const fmt = (v: number, pos: string, neg: string) => `${Math.abs(v).toFixed(4)}° ${v >= 0 ? pos : neg}`;

export function AnalysisScene({ lines = [], target, title = "Investigating" }: { lines?: TraceLine[]; target?: Target | null; title?: string }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const start = performance.now();
    const timer = window.setInterval(() => setElapsed(performance.now() - start), 100);
    return () => window.clearInterval(timer);
  }, []);

  // A sample run writes its whole trace in about 1.5 s: the scene plays the real lines at a pace the
  // eye can follow (one every 150 ms), so the readouts match where the camera is.
  const shown = lines.slice(0, Math.min(lines.length, Math.floor(elapsed / 150) + 1));
  const goal = target ?? targetFromTrace(shown) ?? COTE_DIVOIRE;
  const stage = [...STAGES].reverse().find((s) => elapsed >= s.at) ?? STAGES[0];
  const world = stage.scale === 1;
  const centre = world ? { lat: 8, lon: 0 } : goal;
  const cx = centre.lon + 180;
  const cy = 90 - centre.lat;

  // Built once: re-rendering 3,800 circles ten times a second would stutter.
  const dotsLayer = useMemo(() => {
    const out = [];
    for (let i = 0; i < WORLD.dots.length; i += 2) {
      out.push(<circle key={i} cx={WORLD.dots[i] + 180} cy={90 - WORLD.dots[i + 1]} r={0.32} fill="rgba(232,239,234,0.24)" />);
    }
    return out;
  }, []);

  const queries = shown.filter((l) => l.action === "query_zetaris").length;
  const rows = shown.reduce((sum, l) => sum + (typeof l.rows === "number" ? l.rows : 0), 0);
  const recent = shown.slice(-4);
  const label = (elapsed > STAGES[1].at ? phaseOf(shown) : "") || stage.label;
  const zoomed = stage.scale >= 70;
  // The real plot of the home page: its own Sentinel-2 scene comes up under the cells.
  const onP042 = Math.abs(goal.lat - 5.73) < 0.05 && Math.abs(goal.lon + 6.522) < 0.05;

  return (
    <div className="relative aspect-[16/9] w-full overflow-hidden rounded-[14px] border border-gt-line bg-[#07100c]" role="img" aria-label={`${title}: ${label}`}>
      <svg viewBox="0 0 360 180" preserveAspectRatio="xMidYMid slice" className="absolute inset-0 h-full w-full" aria-hidden>
        <g
          style={{
            transform: `translate(180px, 90px) scale(${stage.scale}) translate(${-cx}px, ${-cy}px)`,
            transition: "transform 1.9s cubic-bezier(0.65, 0, 0.2, 1)",
          }}
          className="motion-reduce:!transition-none"
        >
          {Array.from({ length: 17 }, (_, i) => (
            <line key={`m${i}`} x1={i * 22.5} y1={0} x2={i * 22.5} y2={180} stroke="rgba(155,227,180,0.07)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          ))}
          {Array.from({ length: 9 }, (_, i) => (
            <line key={`p${i}`} x1={0} y1={i * 22.5} x2={360} y2={i * 22.5} stroke="rgba(155,227,180,0.07)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          ))}
          <g style={{ opacity: stage.scale >= 70 ? 0 : 1, transition: "opacity 0.9s ease" }}>{dotsLayer}</g>
          {Object.entries(WORLD.west).map(([iso, c]) => (
            <path
              key={iso}
              d={c.d}
              fill={iso === "CIV" ? "rgba(155,227,180,0.10)" : "none"}
              stroke={iso === "CIV" ? "rgba(155,227,180,0.85)" : "rgba(155,227,180,0.28)"}
              strokeWidth={iso === "CIV" ? 1.4 : 0.8}
              vectorEffect="non-scaling-stroke"
              style={{ opacity: world ? 0 : 1, transition: "opacity 1.2s ease" }}
            />
          ))}
        </g>
      </svg>

      {onP042 && (
        <img
          src="/assets/world/p042-2026.jpg"
          alt=""
          aria-hidden
          className="absolute inset-0 h-full w-full object-cover transition-opacity duration-[1600ms]"
          style={{ opacity: zoomed ? 0.55 : 0, filter: "saturate(1.1) contrast(1.05)" }}
        />
      )}

      {/* the reticle sits at the centre: the camera always flies the target there */}
      <div className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2">
        <div className="relative h-24 w-24 md:h-32 md:w-32">
          <span className="absolute inset-0 rounded-full border border-gt-signal/60 motion-safe:animate-[gt-ring_2.4s_ease-out_infinite]" />
          <span className="absolute inset-[30%] rounded-full border border-gt-signal/80" />
          <span className="absolute left-1/2 top-0 h-full w-px -translate-x-1/2 bg-gt-signal/30" />
          <span className="absolute left-0 top-1/2 h-px w-full -translate-y-1/2 bg-gt-signal/30" />
          <span className="absolute left-1/2 top-1/2 h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-gt-signal" />
        </div>
      </div>

      {/* at plot scale: the cells being read, and a sweep */}
      <div className="pointer-events-none absolute inset-0 transition-opacity duration-1000" style={{ opacity: zoomed ? 1 : 0 }}>
        <div className="absolute left-1/2 top-1/2 grid -translate-x-1/2 -translate-y-1/2 grid-cols-12 gap-[3px]">
          {Array.from({ length: 96 }, (_, i) => (
            <span
              key={i}
              className="h-3 w-3 md:h-4 md:w-4 motion-safe:animate-[gt-cell_2.8s_ease-in-out_infinite]"
              style={{ animationDelay: `${((i * 37) % 96) * 29}ms`, background: "rgba(155,227,180,0.22)" }}
            />
          ))}
        </div>
        <div className="absolute inset-y-0 w-1/3 motion-safe:animate-[gt-sweep_2.6s_linear_infinite] bg-[linear-gradient(90deg,transparent,rgba(155,227,180,0.16),transparent)]" />
      </div>

      {/* readouts */}
      <div className="pointer-events-none absolute left-4 top-4 md:left-6 md:top-5">
        <div className="gt-mono text-[11px] uppercase tracking-[0.14em] text-gt-signal">{title}</div>
        <div className="mt-1 text-lg font-semibold tracking-tight text-gt-text md:text-2xl">{label}</div>
        <div className="gt-num mt-2 text-[12px] text-gt-text/70">
          {fmt(centre.lat, "N", "S")} · {fmt(centre.lon, "E", "W")}
        </div>
      </div>
      <div className="pointer-events-none absolute right-4 top-4 text-right md:right-6 md:top-5">
        <div className="gt-num text-[12px] text-gt-text/70">{queries} queries</div>
        <div className="gt-num text-[12px] text-gt-text/70">{rows.toLocaleString("en-GB")} rows read</div>
        <div className="gt-num text-[12px] text-gt-text/70">{(elapsed / 1000).toFixed(1)} s</div>
      </div>
      <ol className="pointer-events-none absolute bottom-3 left-4 right-4 space-y-1 md:bottom-5 md:left-6 md:right-auto md:w-[60%]">
        {recent.map((l, i) => (
          <li key={`${shown.length}-${i}`} className="gt-mono gt-line-in truncate text-[11px] md:text-[12px]" style={{ opacity: 0.45 + (i / Math.max(1, recent.length - 1)) * 0.55 }}>
            <span className="text-gt-signal">{l.agent_name}</span> <span className="text-gt-text/60">{l.action}</span>{" "}
            <span className="text-gt-text/85">{l.output_summary ?? l.input_summary}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
