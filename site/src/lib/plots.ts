// The real plot readings shipped with the site (src/data/plots.json), made by the GreenTrace
// engine itself (app/web/maps.py plot_map and app/checks/landcover.py measure) on 24/10/2026.

import plotsJson from "../data/plots.json";

export type Cell = [lat: number, lon: number, forest2020: number | null, lossYear: number | null];

export type PlotMap = {
  plot: { id: string; lat: number; lon: number; declared_area_ha: number; role: string; vertices: [number, number][] };
  window: { lat_min: number; lat_max: number; lon_min: number; lon_max: number; width_m: number; height_m: number };
  grid: { cell_deg: number; forest_from: number; cutoff_year: number };
  cells: Cell[];
  measure: {
    outline_ha: number;
    forest_2020_ha: number | null;
    loss_after_2020_ha: number | null;
    loss_ha_by_year: Record<string, number>;
    forest_loss_ha_by_year?: Record<string, number> | null;
    main_loss_year: number | null;
  };
  sources: string[];
};

export type Frame = { label_year: number; date: string; scene: string; cloud: number; file: string };

export type PlotsFile = { p042: PlotMap; p041: PlotMap; frames: Frame[] };

export const PLOTS = plotsJson as unknown as PlotsFile;

/** SVG coordinates of the 960 x 640 frame every image and cell is drawn on. */
export const FRAME_W = 960;
export const FRAME_H = 640;

export function project(map: PlotMap, lat: number, lon: number): [number, number] {
  const w = map.window;
  const x = ((lon - w.lon_min) / (w.lon_max - w.lon_min)) * FRAME_W;
  const y = ((w.lat_max - lat) / (w.lat_max - w.lat_min)) * FRAME_H;
  return [x, y];
}

/** One cell as an SVG rect (cell lat/lon are cell centres). */
export function cellRect(map: PlotMap, cell: Cell) {
  const half = map.grid.cell_deg / 2;
  const [x0, y0] = project(map, cell[0] + half, cell[1] - half);
  const [x1, y1] = project(map, cell[0] - half, cell[1] + half);
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

export function outlinePoints(map: PlotMap): string {
  return map.plot.vertices.map(([lat, lon]) => project(map, lat, lon).join(",")).join(" ");
}

export function isForest2020(map: PlotMap, cell: Cell): boolean {
  return cell[2] !== null && cell[2] >= map.grid.forest_from;
}

export function fmtHa(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "n/a";
  return `${value.toFixed(digits)} ha`;
}

export function fmtDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}
