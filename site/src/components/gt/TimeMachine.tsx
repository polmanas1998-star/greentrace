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

// The signature: the visitor's scroll moves the clock over one real plot, February 2019 to
// March 2026. Six clean dry-season Sentinel-2 scenes (two hazy ones are left out and said so
// below the ruler), the EU 2020 forest cells, and the Hansen loss cells of each year after the
// cut-off. Numbers are the engine's (forest_loss_ha_by_year), never recomputed here.

const CLEAN = new Set(["p042-2019.jpg", "p042-2020.jpg", "p042-2022.jpg", "p042-2024.jpg", "p042-2025.jpg", "p042-2026.jpg"]);
const T0 = Date.UTC(2019, 0, 15);
const T1 = Date.UTC(2026, 3, 1);
const CUTOFF = Date.UTC(2020, 11, 31);
const FOREST_FADE = 1.2e10; // the 2020 forest fades in over the ~4.6 months before the cut-off

const toT = (iso: string) => Date.parse(`${iso}T00:00:00Z`);
const pct = (t: number) => ((t - T0) / (T1 - T0)) * 100;
// A Hansen loss year Y shows once the clock passes 1 October of Y: the next dry-season scene shows it.
const revealOf = (year: number) => Date.UTC(year, 9, 1);

type Chapter = { at: number; title: string; body: string };

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

  const shown = useMemo(() => frames.filter((f) => CLEAN.has(f.file)), [frames]);
  const lossByYear = map.measure.forest_loss_ha_by_year ?? {};
  const lossYears = Object.keys(lossByYear).map(Number).filter((y) => y > map.grid.cutoff_year).sort();
  const totalAfter = map.measure.loss_after_2020_ha ?? 0;

  const { forestCells, lossCells } = useMemo(() => {
    const forest = map.cells.filter((c) => isForest2020(map, c));
    const loss: Record<number, typeof forest> = {};
    for (const c of forest) {
      const year = c[3];
      if (year && year > map.grid.cutoff_year) (loss[year] ??= []).push(c);
    }
    return { forestCells: forest, lossCells: loss };
  }, [map]);

  const chapters: Chapter[] = [
    {
      at: T0,
      title: "February 2019.",
      body: "Ten hectares near Soubré, Côte d'Ivoire. A real plot, seen by Sentinel-2.",
    },
    {
      at: CUTOFF - FOREST_FADE,
      title: "31 December 2020.",
      body: `The EU cut-off. On the EU forest map, ${map.measure.forest_2020_ha?.toFixed(2)} ha inside the outline are forest.`,
    },
    {
      at: revealOf(2021),
      title: "2021.",
      body: `${(lossByYear["2021"] ?? 0).toFixed(2)} ha of that forest is gone in a single year.`,
    },
    {
      at: revealOf(2022),
      title: "2022 and 2023.",
      body: `The clearing goes on: ${((lossByYear["2022"] ?? 0) + (lossByYear["2023"] ?? 0)).toFixed(2)} ha more, one 30 m cell at a time.`,
    },
    {
      at: revealOf(2024),
      title: "By 2024.",
      body: `${totalAfter.toFixed(2)} ha lost after the cut-off, cell by cell, on the Hansen/UMD map.`,
    },
    {
      at: Date.UTC(2026, 1, 20),
      title: "Contradicted.",
      body: "A promise of zero deforestation since 2021 does not survive this plot. Every number above is cited.",
    },
  ];

  useEffect(() => {
    const section = sectionRef.current;
    if (!section) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let frame = 0;

    const paint = () => {
      frame = 0;
      const rect = section.getBoundingClientRect();
      const span = Math.max(1, rect.height - window.innerHeight);
      const p = reduce ? 1 : Math.min(1, Math.max(0, -rect.top / span));
      const t = T0 + p * (T1 - T0);

      // Images: crossfade between the two scenes around t.
      const times = shown.map((f) => toT(f.date));
      let k = 0;
      while (k < times.length - 1 && times[k + 1] <= t) k += 1;
      const next = Math.min(times.length - 1, k + 1);
      const mix = next === k ? 0 : Math.min(1, Math.max(0, (t - times[k]) / (times[next] - times[k])));
      imgRefs.current.forEach((img, i) => {
        if (!img) return;
        img.style.opacity = i === k ? "1" : i === next ? String(mix) : "0";
      });
      const nearest = mix > 0.5 ? next : k;
      if (dateRef.current) dateRef.current.textContent = fmtDate(shown[nearest].date);
      if (sceneRef.current) sceneRef.current.textContent = shown[nearest].scene;

      // Forest of 2020 lights up at the cut-off; each loss year once the clock passes it.
      const forestOn = Math.min(1, Math.max(0, (t - (CUTOFF - FOREST_FADE)) / FOREST_FADE));
      if (forestRef.current) forestRef.current.style.opacity = String(0.85 * forestOn);
      if (forestNumRef.current) forestNumRef.current.style.opacity = String(forestOn);
      let lost = 0;
      for (const year of lossYears) {
        const on = Math.min(1, Math.max(0, (t - revealOf(year)) / 6e9));
        const g = yearRefs.current[year];
        if (g) g.style.opacity = String(on);
        lost += (lossByYear[String(year)] ?? 0) * on;
      }
      if (lostRef.current) lostRef.current.textContent = lost.toFixed(2);
      if (needleRef.current) needleRef.current.style.transform = `translateX(${pct(t)}%)`;

      let active = 0;
      chapters.forEach((c, i) => {
        if (t >= c.at) active = i;
      });
      chapterRefs.current.forEach((el, i) => {
        if (el) el.dataset.active = String(i === active);
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
    // chapters and loss years derive from map, which never changes for a mounted section
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown, map]);

  const years = Array.from({ length: 8 }, (_, i) => 2019 + i);

  return (
    <section ref={sectionRef} aria-label="Seven years over one plot" className="relative h-[560vh] motion-reduce:h-auto">
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
            <div className="text-gt-muted">P-042 · 5.7300 N, 6.5220 W</div>
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
        <div className="absolute bottom-36 left-6 right-6 md:left-12 md:max-w-xl">
          {chapters.map((c, i) => (
            <div
              key={c.title}
              ref={(el) => {
                chapterRefs.current[i] = el;
              }}
              data-active={i === 0}
              className="absolute bottom-0 left-0 right-0 transition-[opacity,transform] duration-700 ease-out data-[active=false]:pointer-events-none data-[active=false]:translate-y-3 data-[active=false]:opacity-0 motion-reduce:static motion-reduce:mb-6 motion-reduce:opacity-100 motion-reduce:translate-y-0"
            >
              <h3 className="gt-display text-4xl text-gt-text md:text-6xl">{c.title}</h3>
              <p className="mt-3 max-w-[46ch] text-lg leading-relaxed text-gt-text/85">{c.body}</p>
            </div>
          ))}
        </div>

        {/* the ruler */}
        <div className="absolute inset-x-6 bottom-10 md:inset-x-12">
          <div className="relative h-10">
            <div className="absolute inset-x-0 top-5 h-px bg-gt-line-strong" />
            {years.map((y) => (
              <div key={y} className="absolute top-0 flex -translate-x-1/2 flex-col items-center" style={{ left: `${pct(Date.UTC(y, 0, 1))}%` }}>
                <span className="gt-mono text-[11px] text-gt-muted">{y}</span>
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
          <p className="gt-mono mt-1 text-[11px] leading-5 text-gt-muted">
            Real Sentinel-2 L2A scenes, one clean dry-season frame per year (the hazy scenes of Feb 2021 and Jan 2023 are left out). Cells: EU JRC GFC2020 v4 forest, Hansen/UMD GFC v1.12 loss year. Contains modified Copernicus Sentinel data 2019-2026.
          </p>
        </div>
      </div>
    </section>
  );
}
