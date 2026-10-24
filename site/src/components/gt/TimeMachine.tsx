import { useEffect, useMemo, useRef } from "react";

import {
  FRAME_H,
  FRAME_W,
  cellRect,
  fmtDate,
  isForest2020,
  outlinePoints,
  type Frame,
  type PlotMap,
} from "../../lib/plots";

// The signature: one real plot, one scroll step per real Sentinel-2 scene (six clean dry-season
// scenes; the hazy ones of Feb 2021 and Jan 2023 are left out and said so below the ruler). At each
// step the date, the ruler, the text, the cells and the counter all describe the SAME scene: the EU
// 2020 forest from the 2020 scene on, and the Hansen loss years that scene can already show. Numbers
// are the engine's (forest_loss_ha_by_year), never recomputed here.

const CLEAN = new Set(["p042-2019.jpg", "p042-2020.jpg", "p042-2022.jpg", "p042-2024.jpg", "p042-2025.jpg", "p042-2026.jpg"]);
const T0 = Date.UTC(2019, 0, 15);
const T1 = Date.UTC(2026, 3, 1);
const CUTOFF = Date.UTC(2020, 11, 31);

const toT = (iso: string) => Date.parse(`${iso}T00:00:00Z`);
const pct = (t: number) => ((t - T0) / (T1 - T0)) * 100;
// A loss year Y is visible on a scene taken from December of Y on (dry-season scenes come after the clearing).
const visibleOn = (year: number, sceneDate: string) => toT(sceneDate) >= Date.UTC(year, 11, 1);
const monthYear = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
// Hold each scene for most of its step, then cross to the next one.
const ease = (f: number) => {
  const x = Math.min(1, Math.max(0, (f - 0.55) / 0.45));
  return x * x * (3 - 2 * x);
};
const lerp = (a: number, b: number, m: number) => a + (b - a) * m;

type Chapter = { title: string; body: string };

export function TimeMachine({ map, frames }: { map: PlotMap; frames: Frame[] }) {
  const sectionRef = useRef<HTMLElement>(null);
  const imgRefs = useRef<Array<HTMLImageElement | null>>([]);
  const yearRefs = useRef<Record<number, SVGGElement | null>>({});
  const forestRef = useRef<SVGGElement>(null);
  const needleRef = useRef<HTMLDivElement>(null);
  const dateRef = useRef<HTMLSpanElement>(null);
  const sceneRef = useRef<HTMLSpanElement>(null);
  const lostRef = useRef<HTMLSpanElement>(null);
  const forestNumRef = useRef<HTMLDivElement>(null);
  const chapterRefs = useRef<Array<HTMLDivElement | null>>([]);

  const shown = useMemo(() => frames.filter((f) => CLEAN.has(f.file)).sort((a, b) => toT(a.date) - toT(b.date)), [frames]);
  const lossByYear = map.measure.forest_loss_ha_by_year ?? {};
  const lossYears = Object.keys(lossByYear).map(Number).filter((y) => y > map.grid.cutoff_year).sort();
  const forest2020 = map.measure.forest_2020_ha ?? 0;

  const { forestCells, lossCells } = useMemo(() => {
    const forest = map.cells.filter((c) => isForest2020(map, c));
    const loss: Record<number, typeof forest> = {};
    for (const c of forest) {
      const year = c[3];
      if (year && year > map.grid.cutoff_year) (loss[year] ??= []).push(c);
    }
    return { forestCells: forest, lossCells: loss };
  }, [map]);

  // What each scene shows: the forest map from the 2020 scene on, and the loss years it can show.
  const steps = useMemo(
    () =>
      shown.map((f) => {
        const years = lossYears.filter((y) => visibleOn(y, f.date));
        return {
          forest: toT(f.date) >= Date.UTC(2020, 0, 1) ? 1 : 0,
          years,
          lost: years.reduce((sum, y) => sum + (lossByYear[String(y)] ?? 0), 0),
        };
      }),
    // derived from the map and frames only
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [shown, map],
  );

  const chapters: Chapter[] = shown.map((f, k) => {
    if (k === shown.length - 1) {
      return {
        title: "Contradicted.",
        body: `${monthYear(f.date)}: ${steps[k].lost.toFixed(2)} ha of the ${forest2020.toFixed(2)} ha of 2020 forest are gone. A promise of zero deforestation since 2021 does not survive this plot.`,
      };
    }
    if (k === 0) return { title: `${monthYear(f.date)}.`, body: "Ten hectares near Soubré, Côte d'Ivoire. A real plot, seen by Sentinel-2." };
    if (steps[k].forest && !steps[k - 1].forest) {
      return {
        title: `${monthYear(f.date)}.`,
        body: `A year before the EU cut-off of 31 December 2020, the EU forest map counts ${forest2020.toFixed(2)} ha of forest inside the outline.`,
      };
    }
    const fresh = steps[k].years.filter((y) => !steps[k - 1].years.includes(y));
    const freshHa = fresh.reduce((sum, y) => sum + (lossByYear[String(y)] ?? 0), 0);
    const when = fresh.length === 1 ? `in ${fresh[0]}` : `in ${fresh.slice(0, -1).join(", ")} and ${fresh[fresh.length - 1]}`;
    return {
      title: `${monthYear(f.date)}.`,
      body: fresh.length
        ? `${freshHa.toFixed(2)} ha of that forest cleared ${when}: ${steps[k].lost.toFixed(2)} ha lost since the cut-off.`
        : `No new loss on the map since the last scene: ${steps[k].lost.toFixed(2)} ha lost since the cut-off.`,
    };
  });

  useEffect(() => {
    const section = sectionRef.current;
    if (!section || shown.length === 0) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const last = shown.length - 1;
    let frame = 0;

    const paint = () => {
      frame = 0;
      const rect = section.getBoundingClientRect();
      const span = Math.max(1, rect.height - window.innerHeight);
      const p = reduce ? 1 : Math.min(1, Math.max(0, -rect.top / span));
      const position = p * last;
      const i = Math.min(last, Math.floor(position));
      const next = Math.min(last, i + 1);
      const mix = next === i ? 0 : ease(position - i);
      const current = mix > 0.5 ? next : i;

      imgRefs.current.forEach((img, k) => {
        if (img) img.style.opacity = k === i ? "1" : k === next ? String(mix) : "0";
      });
      if (dateRef.current) dateRef.current.textContent = fmtDate(shown[current].date);
      if (sceneRef.current) sceneRef.current.textContent = shown[current].scene;

      const forestOn = lerp(steps[i].forest, steps[next].forest, mix);
      if (forestRef.current) forestRef.current.style.opacity = String(0.85 * forestOn);
      if (forestNumRef.current) forestNumRef.current.style.opacity = String(forestOn);
      for (const year of lossYears) {
        const g = yearRefs.current[year];
        if (g) g.style.opacity = String(lerp(steps[i].years.includes(year) ? 1 : 0, steps[next].years.includes(year) ? 1 : 0, mix));
      }
      if (lostRef.current) lostRef.current.textContent = lerp(steps[i].lost, steps[next].lost, mix).toFixed(2);
      if (needleRef.current) {
        needleRef.current.style.transform = `translateX(${pct(lerp(toT(shown[i].date), toT(shown[next].date), mix))}%)`;
      }
      chapterRefs.current.forEach((el, k) => {
        if (el) el.dataset.active = String(k === current);
      });
    };

    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(paint);
    };
    paint();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
    // steps, chapters and loss years derive from map and frames, which never change for a mounted section
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown, map]);

  const years = Array.from({ length: 8 }, (_, i) => 2019 + i);

  return (
    <section ref={sectionRef} aria-label="Seven years over one plot" className="relative h-[600vh] motion-reduce:h-auto">
      <div className="sticky top-0 h-dvh overflow-hidden motion-reduce:relative">
        {/* the scenes */}
        <div className="absolute inset-0">
          {shown.map((f, i) => (
            <img
              key={f.file}
              ref={(el) => {
                imgRefs.current[i] = el;
              }}
              src={`/assets/world/${f.file}`}
              alt={i === 0 ? `Sentinel-2 true colour scene of plot P-042, ${fmtDate(f.date)}` : ""}
              aria-hidden={i === 0 ? undefined : true}
              className="absolute inset-0 h-full w-full object-cover"
              style={{ opacity: i === 0 ? 1 : 0, imageRendering: "auto", filter: "saturate(1.08) contrast(1.06)" }}
              decoding="async"
            />
          ))}
          <div className="absolute inset-0 bg-[radial-gradient(120%_90%_at_50%_45%,transparent_35%,rgba(11,21,16,0.78)_100%)]" />
        </div>

        {/* the cells and the outline, on the same 960 x 640 frame as the scenes */}
        <svg
          viewBox={`0 0 ${FRAME_W} ${FRAME_H}`}
          preserveAspectRatio="xMidYMid slice"
          className="absolute inset-0 h-full w-full"
          aria-hidden
        >
          <g ref={forestRef} style={{ opacity: 0 }}>
            {forestCells.map((c, i) => {
              const r = cellRect(map, c);
              return (
                <rect
                  key={i}
                  x={r.x + 0.6}
                  y={r.y + 0.6}
                  width={r.w - 1.2}
                  height={r.h - 1.2}
                  fill="rgba(155,227,180,0.16)"
                  stroke="rgba(155,227,180,0.55)"
                  strokeWidth={0.8}
                />
              );
            })}
          </g>
          {Object.entries(lossCells).map(([year, cells]) => (
            <g
              key={year}
              ref={(el) => {
                yearRefs.current[Number(year)] = el;
              }}
              style={{ opacity: 0 }}
            >
              {cells.map((c, i) => {
                const r = cellRect(map, c);
                return <rect key={i} x={r.x + 0.6} y={r.y + 0.6} width={r.w - 1.2} height={r.h - 1.2} fill="rgba(217,98,43,0.78)" />;
              })}
            </g>
          ))}
          <polygon points={outlinePoints(map)} fill="none" stroke="#E8EFEA" strokeWidth={2.2} strokeLinejoin="round" />
        </svg>

        {/* readings */}
        <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-6 px-6 pt-24 md:px-12">
          <div className="gt-mono text-[12px] leading-5 text-gt-text/80">
            <div>
              <span ref={dateRef}>{fmtDate(shown[0]?.date ?? "2019-02-12")}</span>
            </div>
            <div className="text-gt-muted">
              <span ref={sceneRef}>{shown[0]?.scene}</span>
            </div>
            <div className="text-gt-muted">
              P-042<span className="max-md:hidden"> · 5.7300 N, 6.5220 W</span>
            </div>
          </div>
          <div className="text-right">
            <div ref={forestNumRef} style={{ opacity: 0 }} className="mb-4">
              <div className="gt-mono text-[11px] uppercase tracking-[0.14em] text-gt-signal">EU forest in 2020</div>
              <div className="gt-num text-2xl text-gt-text">{map.measure.forest_2020_ha?.toFixed(2)} ha</div>
            </div>
            <div className="gt-mono text-[11px] uppercase tracking-[0.14em] text-gt-loss">Lost after the cut-off</div>
            <div className="gt-num text-5xl font-medium tracking-tight text-gt-text md:text-7xl">
              <span ref={lostRef}>0.00</span>
              <span className="ml-2 text-2xl text-gt-muted md:text-3xl">ha</span>
            </div>
          </div>
        </div>

        {/* chapters */}
        <div className="absolute bottom-40 left-6 right-6 md:bottom-36 md:left-12 md:max-w-xl">
          {chapters.map((c, i) => (
            <div
              key={`${i}-${c.title}`}
              ref={(el) => {
                chapterRefs.current[i] = el;
              }}
              data-active={i === 0}
              className="absolute bottom-0 left-0 right-0 transition-[opacity,transform] duration-700 ease-out data-[active=false]:pointer-events-none data-[active=false]:translate-y-3 data-[active=false]:opacity-0 motion-reduce:static motion-reduce:mb-6 motion-reduce:opacity-100 motion-reduce:translate-y-0"
            >
              <h3 className="gt-display text-3xl text-gt-text md:text-6xl">{c.title}</h3>
              <p className="mt-2 max-w-[46ch] text-base leading-relaxed text-gt-text/85 md:mt-3 md:text-lg">{c.body}</p>
            </div>
          ))}
        </div>

        {/* the ruler */}
        <div className="absolute inset-x-6 bottom-10 md:inset-x-12">
          <div className="relative h-10">
            <div className="absolute inset-x-0 top-5 h-px bg-gt-line-strong" />
            {years.map((y) => (
              <div key={y} className="absolute top-0 flex -translate-x-1/2 flex-col items-center" style={{ left: `${pct(Date.UTC(y, 0, 1))}%` }}>
                <span className={`gt-mono text-[11px] text-gt-muted ${y % 2 === 0 ? "max-md:invisible" : ""}`}>{y}</span>
                <span className="mt-1 h-2 w-px bg-gt-line-strong" />
              </div>
            ))}
            <div className="absolute top-3 h-5 w-[2px] -translate-x-1/2 bg-gt-loss" style={{ left: `${pct(CUTOFF)}%` }} title="EU cut-off, 31 December 2020" />
            <div className="absolute inset-x-0 top-0 h-full">
              <div ref={needleRef} className="absolute left-0 top-[14px] h-3 w-full" style={{ transform: "translateX(0%)" }}>
                <span className="absolute -left-[7px] block h-3.5 w-3.5 rounded-full border-2 border-gt-ground bg-gt-signal shadow-[0_0_0_4px_rgba(155,227,180,0.18)]" />
              </div>
            </div>
          </div>
          <p className="gt-mono mt-1 text-[11px] leading-5 text-gt-muted md:hidden">Sentinel-2 L2A · EU JRC GFC2020 · Hansen/UMD. Contains modified Copernicus Sentinel data.</p>
          <p className="gt-mono mt-1 hidden text-[11px] leading-5 text-gt-muted md:block">
            Real Sentinel-2 L2A scenes, one clean dry-season frame per year (the hazy scenes of Feb 2021 and Jan 2023 are left out). Cells: EU JRC GFC2020 v4 forest, Hansen/UMD GFC v1.12 loss year. Contains modified Copernicus Sentinel data 2019-2026.
          </p>
        </div>
      </div>
    </section>
  );
}
