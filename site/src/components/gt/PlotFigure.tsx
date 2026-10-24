import { FRAME_H, FRAME_W, cellRect, isForest2020, outlinePoints, type PlotMap } from "../../lib/plots";

// One real plot on one real scene: the outline, and if asked the cells the engine measured.
export function PlotFigure({
  map,
  image,
  layers = "loss",
  draw = false,
  className = "",
  label,
}: {
  map: PlotMap;
  image: string;
  layers?: "none" | "loss" | "forest+loss" | "cover-loss";
  draw?: boolean;
  className?: string;
  label: string;
}) {
  const cutoff = map.grid.cutoff_year;
  const forest = layers === "forest+loss" ? map.cells.filter((c) => isForest2020(map, c)) : [];
  const loss =
    layers === "none"
      ? []
      : layers === "cover-loss"
        ? map.cells.filter((c) => c[3] !== null && (c[3] as number) > cutoff)
        : map.cells.filter((c) => isForest2020(map, c) && c[3] !== null && (c[3] as number) > cutoff);
  const lossFill = layers === "cover-loss" ? "rgba(167,177,188,0.55)" : "rgba(217,98,43,0.8)";

  return (
    <figure className={`relative overflow-hidden ${className}`} aria-label={label}>
      <img src={`/assets/world/${image}`} alt={label} className="block h-full w-full object-cover" style={{ filter: "saturate(1.08) contrast(1.06)" }} />
      <svg viewBox={`0 0 ${FRAME_W} ${FRAME_H}`} preserveAspectRatio="xMidYMid slice" className="absolute inset-0 h-full w-full" aria-hidden>
        {forest.map((c, i) => {
          const r = cellRect(map, c);
          return <rect key={`f${i}`} x={r.x + 0.6} y={r.y + 0.6} width={r.w - 1.2} height={r.h - 1.2} fill="rgba(155,227,180,0.14)" stroke="rgba(155,227,180,0.5)" strokeWidth={0.8} />;
        })}
        {loss.map((c, i) => {
          const r = cellRect(map, c);
          return <rect key={`l${i}`} x={r.x + 0.6} y={r.y + 0.6} width={r.w - 1.2} height={r.h - 1.2} fill={lossFill} />;
        })}
        <polygon
          points={outlinePoints(map)}
          fill="none"
          stroke="#E8EFEA"
          strokeWidth={2.4}
          strokeLinejoin="round"
          pathLength={1}
          className={draw ? "gt-outline-draw" : undefined}
        />
      </svg>
    </figure>
  );
}
