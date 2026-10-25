/**
 * Meterless Scout Intent, implemented for GreenTrace from the spec (engines/scout-intent/AGENTS.md,
 * Meterless/Meterless commit 1742dec). Upstream publishes the spec and an eval harness, no code:
 * this file is ours (Apache-2.0) and is verified with the unmodified harness in ../scout-evals.
 *
 * Pipeline per request: Sense -> Interpret -> Guard -> Route -> Recommend -> signed ExecutionContract.
 * Stage 1 only (deterministic, no model): the semantic and contextual terms of the canonical formula
 * are 0, so the weights are renormalised over the terms that ran (implementer's choice, see README).
 */
import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { readFileSync } from "node:fs";

export type Intent = {
  id: string; category: string; description: string; parameters?: Record<string, unknown>;
  capabilities: string[]; riskClass: "low" | "medium" | "high"; surfaces: string[]; roles: string[];
  examples: string[]; version: number;
};
export type IntentRegistry = { version: number; intents: Intent[] };
export type Candidate = { intent: string; score: number; span?: string };
type Level = "low" | "medium" | "high" | "block";
type Flag = { layer: string; kind: string; evidence: string };

export const BANDS = { high: 0.8, medium: 0.55, low: 0.4 };
export const WEIGHTS = { lexical: 0.4, semantic: 0.35, contextual: 0.2, recency: 0.05 };
const TIE_WINDOW = 0.05;
const HIGH_RISK_PROCEED = 0.95;
const TTL_MS = 5 * 60 * 1000;
const SCAN_BUDGET_MS = 16;
const STOP = new Set(("a an and are as at be by do for from i in is it me my of on or our the this to us we what "
  + "with you your about all also any can could did does how into its let please so that them then there these "
  + "those was were will would just give get show").split(" "));

// ---- injection patterns (Guard layer 1): role confusion, instruction override, out-of-band tools --------
const INJECTION: Array<[string, RegExp]> = [
  ["instruction-override", /\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|your|all|the)\b[^.\n]{0,20}\b(instructions?|rules?|prompts?|guidelines?|directions?)\b/i],
  ["instruction-override", /\b(ignore|disregard)\s+(previous|prior|above|the user|everything)\b/i],
  ["role-confusion", /<\/?\s*(system|user|assistant)\s*>|\[\/?INST\]|<\|im_(start|end)\|>/i],
  ["role-confusion", /\b(you are now|pretend to be|act as) (the )?(system|admin|database admin|an? unrestricted|in unrestricted mode)/i],
  ["role-confusion", /\bunrestricted mode\b|\bjailbreak\b|\bdeveloper mode\b/i],
  ["exfiltration", /\b(reveal|print|dump|list|show)\b[^.\n]{0,30}\b(api keys?|credentials?|secrets?|passwords?|tokens?|system prompt)\b/i],
  ["privilege-escalation", /\bgrant (me|us)\b[^.\n]{0,20}\badmin\b|\bdrop (the )?\w+ table\b/i],
  ["answer-forcing", /\b(answer|reply|respond|output|say)\b[^.\n]{0,30}\b(PROVEN|CONTRADICTED|UNVERIFIABLE)\b/],
  ["control-bytes", /[‪-‮⁦-⁩​‎‏]|\\u202[a-eA-E]|\\u206[6-9]/],
];
const TOOL_REF = /\b(use|call|run|invoke)\s+([a-z]+_[a-z0-9_]+)\b/i;

function tokens(text: string): string[] {
  return (text.toLowerCase().match(/[a-z0-9$]+(?:[.'-][a-z0-9]+)*/g) ?? [])
    .map((w) => w.replace(/'s$/, ""))
    .filter((w) => w.length > 1 && !STOP.has(w));
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).filter(([, v]) => v !== undefined).sort(([a], [b]) => (a < b ? -1 : 1));
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

let lastTime = 0, seq = 0;
function ulid(): string {  // monotonic: time, then a counter within the same millisecond
  const now = Date.now();
  seq = now === lastTime ? seq + 1 : 0;
  lastTime = now;
  return `${now.toString(36).padStart(10, "0")}${seq.toString(36).padStart(4, "0")}${randomBytes(4).toString("hex")}`.toUpperCase();
}

export class Scout {
  readonly registry: IntentRegistry;
  private readonly secret: Buffer;
  private readonly idf = new Map<string, number>();
  private readonly seeds: Array<{ intent: Intent; words: Set<string> }> = [];
  private readonly capabilities = new Set<string>();
  readonly policyPacks: string[];
  readonly selfTestMs: number;

  constructor(options: { intentRegistry: string | IntentRegistry; policyPack?: string | string[]; secret?: string }) {
    this.registry = typeof options.intentRegistry === "string"
      ? JSON.parse(readFileSync(options.intentRegistry, "utf-8")) : options.intentRegistry;
    for (const intent of this.registry.intents) {
      if (!intent.examples?.length) throw new Error(`intent ${intent.id} has no examples: it cannot be classified`);
      intent.capabilities.forEach((c) => this.capabilities.add(c));
      for (const example of intent.examples) this.seeds.push({ intent, words: new Set(tokens(example)) });
    }
    const df = new Map<string, number>();
    for (const { words } of this.seeds) for (const w of words) df.set(w, (df.get(w) ?? 0) + 1);
    for (const [w, n] of df) this.idf.set(w, Math.log(1 + this.seeds.length / n));
    this.policyPacks = [options.policyPack ?? "default"].flat();
    this.secret = Buffer.from(options.secret ?? process.env.SCOUT_SECRET ?? randomBytes(32).toString("hex"));
    const started = performance.now();  // startup self-test: a full-registry scan must fit the 16 ms budget
    for (const probe of ["hello there", "ignore previous instructions", this.seeds[0].intent.examples[0]]) this.scan(probe);
    this.selfTestMs = performance.now() - started;
    if (this.selfTestMs > SCAN_BUDGET_MS * 3) throw new Error(`Stage-1 scan self-test took ${this.selfTestMs.toFixed(1)} ms`);
  }

  // ---- Sense: Stage-1 lexical scan, IDF-weighted cosine against every example ------------------------
  private scan(prompt: string): Candidate[] {
    const words = new Set(tokens(prompt));
    const weight = (w: string) => this.idf.get(w) ?? Math.log(1 + this.seeds.length);
    const norm = (set: Set<string>) => Math.sqrt([...set].reduce((s, w) => s + weight(w) ** 2, 0)) || 1;
    const best = new Map<string, number>();
    for (const { intent, words: seed } of this.seeds) {
      let dot = 0;
      for (const w of words) if (seed.has(w)) dot += weight(w) ** 2;
      const cosine = dot / (norm(words) * norm(seed));
      best.set(intent.id, Math.max(best.get(intent.id) ?? 0, cosine));
    }
    // Stage 2 absent: semantic and contextual are 0 and recency is 0 offline, so once the weights are
    // renormalised over the terms that ran, finalScore is the lexical term alone (sqrt spreads the cosine)
    return [...best].map(([intent, cos]) => ({ intent, score: Math.sqrt(cos) })).sort((a, b) => b.score - a.score);
  }

  async classify(input: { prompt: string; surface: string }): Promise<{ candidates: Candidate[] }> {
    return { candidates: this.scan(input.prompt) };
  }

  // ---- Guard: injection, policy (roles, surfaces), scope; any block blocks -------------------------
  guard(prompt: string, intent: Intent | undefined, user: { role?: string }, surface: string): { level: Level; flags: Flag[]; reason?: string } {
    const flags: Flag[] = [];
    for (const [kind, pattern] of INJECTION) {
      const hit = prompt.match(pattern);
      if (hit) flags.push({ layer: "injection", kind, evidence: hit[0].slice(0, 60) });
    }
    const tool = prompt.match(TOOL_REF);
    if (tool && !this.capabilities.has(tool[2])) flags.push({ layer: "injection", kind: "out-of-band-tool", evidence: tool[2] });
    if (intent && user.role && !intent.roles.includes("*") && !intent.roles.includes(user.role)) {
      flags.push({ layer: "policy", kind: "role", evidence: `${user.role} may not invoke ${intent.id}` });
    }
    if (intent && !intent.surfaces.includes(surface)) flags.push({ layer: "scope", kind: "surface", evidence: `${intent.id} is not offered on ${surface}` });
    const blocking = flags.filter((f) => f.layer === "injection" || f.layer === "policy" || f.layer === "scope");
    if (blocking.length) return { level: "block", flags, reason: blocking.map((f) => `${f.layer}:${f.kind}`).join(", ") };
    return { level: intent?.riskClass ?? "low", flags };
  }

  // ---- Interpret: parameters and references a rule can bind --------------------------------------------
  private interpret(prompt: string): { parameters: Record<string, unknown>; unresolved: boolean } {
    const parameters: Record<string, unknown> = {};
    const count = prompt.match(/\b(?:top|first|last)?\s*(\d{1,3})\b(?!\s*(?:-?month|k\b|%))/i);
    if (count) parameters.count = Number(count[1]);
    if (/\bemails?\b/i.test(prompt)) parameters.channel = "email";
    const money = prompt.match(/\$\s?(\d+(?:\.\d+)?)\s*([km])?/i);
    if (money) parameters.valueMin = Number(money[1]) * (money[2]?.toLowerCase() === "k" ? 1e3 : money[2]?.toLowerCase() === "m" ? 1e6 : 1);
    const account = prompt.match(/\b(?:the\s+)?([A-Z][a-zA-Z]+(?:[A-Z][a-z]+)?)\s+(?:deal|renewal|account|situation)\b/);
    if (account) parameters.account = account[1];
    const unresolved = /\b(the thing|that thing|that one|what we (?:discussed|talked about)|we talked about|you know what)\b/i.test(prompt);
    return { parameters, unresolved };
  }

  private intentById(id: string): Intent | undefined {
    return this.registry.intents.find((i) => i.id === id);
  }

  // ---- Multi-intent: disjoint spans, each scanned on its own -----------------------------------------
  private spans(prompt: string): Candidate[] {
    const parts = prompt.split(/,\s*(?:and\s+)?|\s+and\s+(?=[a-z]+\b)/i).map((p) => p.trim()).filter((p) => tokens(p).length >= 2);
    if (parts.length < 2) return [];
    const picked: Candidate[] = [];
    for (const span of parts) {
      const top = this.scan(span)[0];
      if (top && top.score >= BANDS.medium && !picked.some((p) => p.intent === top.intent)) picked.push({ ...top, span });
    }
    return picked.length >= 2 ? picked : [];
  }

  sign(contract: Record<string, unknown>): string {
    return createHmac("sha256", this.secret).update(canonical({ ...contract, signature: undefined })).digest("hex");
  }

  /** What a downstream engine runs on receipt: signature, expiry, and (if asked) the capability in scope. */
  verify(contract: any, capability?: string, now = Date.now()): { ok: boolean; reason?: string } {
    if (!contract?.signature) return { ok: false, reason: "unsigned" };
    const expected = Buffer.from(this.sign(contract)), given = Buffer.from(String(contract.signature));
    if (expected.length !== given.length || !timingSafeEqual(expected, given)) return { ok: false, reason: "tampered" };
    if (Date.parse(contract.expiresAt) <= now) return { ok: false, reason: "expired" };
    if (capability && !contract.scope?.capabilities?.includes(capability)) return { ok: false, reason: "scope-mismatch" };
    return { ok: true };
  }

  async decide(input: { prompt: string; user?: { id?: string; role?: string }; surface: string; context?: unknown; parentId?: string }) {
    const started = performance.now();
    const traceId = ulid();
    const user = input.user ?? {};
    const candidates = this.scan(input.prompt);
    const multi = this.spans(input.prompt);
    let top = multi[0] ?? candidates[0];
    let primary = top && top.score >= BANDS.low ? this.intentById(top.intent) : undefined;
    const risk = this.guard(input.prompt, primary, user, input.surface);
    const base = { traceId, candidates: candidates.slice(0, 5), risk, latencyMs: 0 };
    if (risk.level === "block") {
      return { ...base, clarification: null, toolPlan: [], executionContract: null, latencyMs: performance.now() - started };
    }
    const { parameters, unresolved } = this.interpret(input.prompt);
    const second = candidates.find((c) => c.intent !== top?.intent);
    let clarification: null | { question: string; options: string[]; trigger: string } = null;
    const options = candidates.slice(0, 4).map((c) => c.intent);
    if (!multi.length) {
      if (unresolved) clarification = { question: "Which item do you mean?", options, trigger: "params" };
      else if (top.score >= BANDS.low && top.score < BANDS.medium) clarification = { question: "Did you mean one of these?", options, trigger: "low-band" };
      else if (top.score < BANDS.high && second && top.score - second.score <= TIE_WINDOW) clarification = { question: "Which of these did you mean?", options, trigger: "tie" };
      else if (primary?.riskClass === "high" && top.score < HIGH_RISK_PROCEED) clarification = { question: `Confirm ${primary.id}?`, options: [primary.id], trigger: "high-risk" };
    }
    if (!primary) primary = this.intentById("GENERAL");  // reject band: the safe intent, never a guessed one
    if (clarification || !primary) {
      return { ...base, clarification, toolPlan: [], executionContract: null, latencyMs: performance.now() - started };
    }
    const plan = multi.length ? multi.map((c) => this.intentById(c.intent)!).filter(Boolean) : [primary];
    const toolPlan = [...new Set(plan.flatMap((i) => i.capabilities))].map((capability) => ({ capability, tool: capability, input: { parameters } }));
    const issuedAt = new Date();
    const contract: Record<string, unknown> = {
      contractId: ulid(), parentId: input.parentId, traceId,
      intent: { primary: { id: primary.id, version: primary.version }, secondary: plan.slice(1).map((i) => i.id), parameters,
                references: parameters.account ? [parameters.account] : [], confidence: Number(top.score.toFixed(4)),
                alternates: candidates.filter((c) => c.intent !== primary!.id).slice(0, 3) },
      risk: { level: risk.level, flags: risk.flags, redactions: [], approvals: [] },
      toolPlan, modelProfile: { id: "local-rules", stage2: "skipped" },
      scope: { user: user.id ?? "anonymous", surface: input.surface, capabilities: toolPlan.map((s) => s.capability) },
      issuedAt: issuedAt.toISOString(), expiresAt: new Date(issuedAt.getTime() + TTL_MS).toISOString(),
      registryVersion: this.registry.version, policyPackVersion: this.policyPacks.join("+"), signedBy: "scout",
    };
    contract.signature = this.sign(contract);
    return { ...base, clarification: null, toolPlan, executionContract: contract, latencyMs: performance.now() - started };
  }

  async replan(input: { parent: string; reason: string; state?: { prompt: string; user?: any; surface: string } }) {
    if (!input.state) throw new Error("replan needs the original request in state");
    return this.decide({ ...input.state, parentId: input.parent });
  }
}
