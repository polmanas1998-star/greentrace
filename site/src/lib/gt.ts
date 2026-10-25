// Client side calls to the GreenTrace engine, always through this site's relay (/api/gt/*).

export type Verdict = "PROVEN" | "CONTRADICTED" | "UNVERIFIABLE" | "NO_ISSUE";

export const API = "/api/gt";

export class EngineError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function detailOf(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    return typeof detail === "string" ? detail : JSON.stringify(detail);
  }
  return fallback;
}

export async function engine<T>(path: string, init?: { method?: string; json?: unknown; signal?: AbortSignal }): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    method: init?.method ?? (init?.json === undefined ? "GET" : "POST"),
    headers: init?.json === undefined ? undefined : { "content-type": "application/json" },
    body: init?.json === undefined ? undefined : JSON.stringify(init.json),
    signal: init?.signal,
  });
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!response.ok) throw new EngineError(response.status, detailOf(body, `${response.status} ${response.statusText}`));
  return body as T;
}

/** A feature the engine may not have yet: null on 404 instead of an error. */
export async function optional<T>(path: string): Promise<T | null> {
  try {
    return await engine<T>(path);
  } catch (error) {
    if (error instanceof EngineError && error.status === 404) return null;
    throw error;
  }
}

export const VERDICT_STYLE: Record<Verdict, { label: string; color: string; word: string }> = {
  CONTRADICTED: { label: "Contradicted", color: "var(--gt-contradicted)", word: "The promise does not hold." },
  PROVEN: { label: "Proven", color: "var(--gt-proven)", word: "The promise holds on this evidence." },
  UNVERIFIABLE: { label: "Unverifiable", color: "var(--gt-unverifiable)", word: "Not enough evidence to decide." },
  NO_ISSUE: { label: "No issue found", color: "var(--gt-proven)", word: "Nothing on the reference maps." },
};

export function verdictStyle(verdict: string | null | undefined) {
  return VERDICT_STYLE[(verdict ?? "UNVERIFIABLE") as Verdict] ?? VERDICT_STYLE.UNVERIFIABLE;
}

// ---- shapes the engine answers with (only the fields this site reads) -----------------------

export type TraceLine = {
  timestamp: string;
  run_id: string;
  agent_name: string;
  action: string;
  input_summary: string | null;
  output_summary: string | null;
  target_agent: string | null;
  confidence: number | null;
  retry_count: number;
  status: string;
  provider: string | null;
  model: string | null;
  rows: number | null;
};

export type RunOutput = {
  run_id: string;
  status: string;
  mode?: string;
  degraded?: boolean;
  verdict?: Verdict;
  summary?: string;
  flip?: string | null;
  findings?: Array<Record<string, unknown>>;
  limitations?: string[];
  recommended_actions?: unknown[];
  human_review?: string;
  eudr_next?: string;
  verdict_sha256?: string;
  fingerprints?: Record<string, string>;
  execution_time_seconds?: number;
};

export type PlotResult = {
  plot_id: string;
  supplier: string | null;
  shape: string;
  area_ha: number | null;
  centre: [number, number];
  verdict: Verdict;
  rule: number | null;
  code: string | null;
  sentence: string;
  measure: Record<string, unknown> | null;
  risk_rank: number;
};

export type PlotCheck = {
  batch_id: string;
  summary: { plots: Partial<Record<Verdict, number>>; refused: number; sentence: string };
  plots: PlotResult[];
  suppliers: Array<{ supplier: string; plots: number; rank: number; why: string; forest_lost_after_2020_ha: number }>;
  errors: unknown[];
  warnings: unknown[];
  rules_version: string;
  exports: Record<string, string>;
  seal: string;
};

export const DEMO_CASE = {
  input: {
    claim: "100% of our cocoa comes from deforestation-free farms since 2021.",
    company_id: "chocodurable",
  },
  options: { case: 2 },
};

export const REAL_PLOT_CASE = {
  input: {
    claim: "100% of our cocoa comes from deforestation-free farms since 2021.",
    plot: {
      type: "Feature",
      properties: { plot_id: "P-042", note: "10 ha square near Soubré, real JRC GFC2020 and Hansen/UMD cells" },
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [-6.523427, 5.72857],
            [-6.520573, 5.72857],
            [-6.520573, 5.73143],
            [-6.523427, 5.73143],
            [-6.523427, 5.72857],
          ],
        ],
      },
    },
  },
  options: { case: 1 },
};
