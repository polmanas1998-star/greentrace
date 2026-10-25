// GreenTrace's World Model server: the unmodified Meterless reference
// (./reference, Apache-2.0) behind a small JSON door, so the Python agents can
// share one world per run. Ours, not upstream; same licence.
//
//   npm start            (listens on 127.0.0.1:8787, WORLD_MODEL_PORT to change)
//
// GET  /health  -> engine, conflict policy and source priority
// POST /call    -> {"namespace": run_id, "op": "assertFact", "args": {...}}
//
// Facts are settled by the reference's source-priority policy: a fact from the
// Evaluator supersedes one from the Investigator, which supersedes the
// Extractor's. Nothing is ever deleted; the audit log keeps every write.
//
// POST /hmem   -> {"op": "add" | "query" | "delete" | "history", "args": {...}}
//
// The second engine: the unmodified H-MEM reference (./hmem/reference), memory
// across investigations, kept on disk in HMEM_DIR (state.json + ledger.jsonl,
// default ../data/memory). HMEM_CLOCK_MS pins its clock, for tests only.
//
// POST /scout  -> {"op": "guard", "text": ...} | {"op": "guard_all", "texts": [...]}
//                 | {"op": "decide", "prompt": ..., "surface": "ask"}
//                 | {"op": "verify", "contract": {...}, "capability": "run.lines"}
//
// The third engine: Meterless Scout Intent, implemented from its spec (./scout, verified with the
// unmodified eval harness in ./scout-evals). It guards every report paragraph before a model reads
// it, and turns each Ask question into a signed execution contract that Ask verifies before it acts.

import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { WorldModel } from "./reference/src/index.ts";
import { HMEM } from "./hmem/reference/src/index.ts";
import { Scout } from "./scout/index.ts";

const HOST = "127.0.0.1"; // this machine only: the server has no authentication
const PORT = Number(process.env.WORLD_MODEL_PORT ?? 8787);
const POLICY = "source-priority";
const SOURCE_PRIORITY = ["evaluator", "investigator", "extractor"];
const MAX_BODY_BYTES = 1_000_000;
const MAX_WORLDS = 200; // one world per run; past this the oldest run is dropped
const NAMESPACE = /^[A-Za-z0-9_-]{1,64}$/; // same alphabet as a run_id

type Args = Record<string, any>;

// The only operations the door opens: the reference's writes and its read views.
const OPS: Record<string, (world: WorldModel, args: Args) => unknown> = {
  upsertEntity: (world, args) => world.upsertEntity(args as any),
  relate: (world, args) => world.relate(args as any),
  assertFact: (world, args) => world.assertFact(args as any),
  entity: (world, args) => world.view("graph").entity(args.id) ?? null,
  facts: (world, args) => world.view("facts").about(args.id),
  entityAt: (world, args) => world.view("snapshot").entityAt(args.id, args.at) ?? null,
  timeline: (world) => world.view("timeline").get(),
  audit: (world) => world.audit(),
};

const worlds = new Map<string, WorldModel>();

const HMEM_DIR = process.env.HMEM_DIR ?? path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "data", "memory");
const HMEM_CLOCK = process.env.HMEM_CLOCK_MS ? Number(process.env.HMEM_CLOCK_MS) : undefined;
const TEXT_MAX = 2000;
let hmem: HMEM | undefined; // opened on the first /hmem call: the World Model never waits on the disk

function memory(): HMEM {
  hmem ??= new HMEM({ persistDir: HMEM_DIR, actor: "greentrace", ...(HMEM_CLOCK ? { clock: () => HMEM_CLOCK } : {}) });
  return hmem;
}

function text(value: unknown, what: string): string {
  if (typeof value !== "string" || value.trim() === "" || value.length > TEXT_MAX) {
    throw new BadRequest(`${what} must be a non-empty string of at most ${TEXT_MAX} characters`);
  }
  return value;
}

function strings(value: unknown, what: string, max: number): string[] {
  if (!Array.isArray(value) || value.length > max || !value.every((v) => typeof v === "string" && v.length <= 300)) {
    throw new BadRequest(`${what} must be a list of at most ${max} short strings`);
  }
  return value;
}

// Records go out without their embedding: a long vector nobody on the Python side reads.
const bare = ({ embedding, ...rest }: Record<string, unknown>) => rest;

// The only H-MEM operations the door opens: the store (add, delete), retrieval (query), the ledger (history).
const HMEM_OPS: Record<string, (m: HMEM, args: Args) => unknown> = {
  add: (m, args) => bare(m.add({
    content: text(args.content, "content"),
    source: typeof args.source === "string" ? args.source : "", // the reference itself rejects a record with no source
    type: "factual",
    layer: "working",
    tags: strings(args.tags ?? [], "tags", 20),
    confidence: typeof args.confidence === "number" ? args.confidence : undefined,
    provenance: {
      origin: "plan_completion",
      learnedAt: m.clock(),
      label: typeof args.label === "string" ? args.label.slice(0, 300) : "greentrace",
      refs: strings(args.refs ?? [], "refs", 50),
    },
  }) as never),
  query: (m, args) => {
    const result = m.query(text(args.query, "query"), {
      topN: typeof args.topN === "number" ? args.topN : undefined,
      threshold: typeof args.threshold === "number" ? args.threshold : undefined,
    });
    return { ...result, memories: result.memories.map((r) => ({ relevance: r.relevance, memory: bare(r.memory as never) })) };
  },
  delete: (m, args) => {
    m.store.delete(text(args.id, "id"), text(args.reason, "reason"));
    return { deleted: args.id };
  },
  history: (m, args) => m.ledger.history(text(args.id, "id")),
};

function worldFor(namespace: string): WorldModel {
  let world = worlds.get(namespace);
  if (!world) {
    world = new WorldModel({ namespace, storage: "memory", conflictPolicy: POLICY, sourcePriority: SOURCE_PRIORITY });
    worlds.set(namespace, world);
    if (worlds.size > MAX_WORLDS) worlds.delete(worlds.keys().next().value!);
  }
  return world;
}

class BadRequest extends Error {}

function readBody(req: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    req.on("data", (chunk: Buffer) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(new BadRequest(`body larger than ${MAX_BODY_BYTES} bytes`));
        req.destroy();
      } else {
        chunks.push(chunk);
      }
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf-8")));
    req.on("error", reject);
  });
}

function send(res: http.ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

const scout = new Scout({
  intentRegistry: path.join(path.dirname(fileURLToPath(import.meta.url)), "scout", "greentrace_intents.json"),
  policyPack: "greentrace",
});

async function scoutCall(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
  try {
    const body = JSON.parse(await readBody(req)) ?? {};
    if (body.op === "guard" && typeof body.text === "string") {
      return send(res, 200, { ok: true, result: scout.guard(body.text, undefined, {}, "report") });
    }
    if (body.op === "guard_all" && Array.isArray(body.texts) && body.texts.length <= 500) {
      return send(res, 200, { ok: true, result: body.texts.map((t: unknown) => scout.guard(String(t), undefined, {}, "report")) });
    }
    if (body.op === "decide" && typeof body.prompt === "string") {
      const decision = await scout.decide({ prompt: body.prompt.slice(0, 2000), user: { id: "ask", role: "analyst" },
                                            surface: typeof body.surface === "string" ? body.surface : "ask" });
      return send(res, 200, { ok: true, result: decision });
    }
    if (body.op === "verify") {
      return send(res, 200, { ok: true, result: scout.verify(body.contract, body.capability) });
    }
    throw new BadRequest('op must be "guard" (text), "guard_all" (texts), "decide" (prompt) or "verify" (contract)');
  } catch (err) {
    send(res, err instanceof BadRequest || err instanceof SyntaxError ? 400 : 422, { ok: false, error: (err as Error).message });
  }
}

const server = http.createServer(async (req, res) => {
  if (req.method === "GET" && req.url === "/health") {
    return send(res, 200, { ok: true, engine: "meterless/world-model reference", conflictPolicy: POLICY, sourcePriority: SOURCE_PRIORITY,
                            memoryEngine: "meterless/hmem reference", intentEngine: "meterless/scout-intent (spec implementation)",
                            scoutSelfTestMs: Number(scout.selfTestMs.toFixed(2)) });
  }
  if (req.method === "POST" && req.url === "/hmem") {
    return hmemCall(req, res);
  }
  if (req.method === "POST" && req.url === "/scout") {
    return scoutCall(req, res);
  }
  if (req.method !== "POST" || req.url !== "/call") {
    return send(res, 404, { ok: false, error: "not found: use GET /health, POST /call, POST /hmem or POST /scout" });
  }

  let namespace: unknown, op: unknown, args: unknown;
  try {
    ({ namespace, op, args } = JSON.parse(await readBody(req)) ?? {});
    if (typeof namespace !== "string" || !NAMESPACE.test(namespace)) throw new BadRequest(`namespace must match ${NAMESPACE}`);
    if (typeof op !== "string" || !Object.hasOwn(OPS, op)) throw new BadRequest(`unknown op ${JSON.stringify(op)}; known: ${Object.keys(OPS).join(", ")}`);
  } catch (err) {
    return send(res, 400, { ok: false, error: (err as Error).message });
  }

  try {
    const result = await OPS[op as string](worldFor(namespace as string), (args ?? {}) as Args);
    send(res, 200, { ok: true, result });
  } catch (err) {
    // The engine refused the call (no provenance, missing name...): say why.
    send(res, 422, { ok: false, error: (err as Error).message });
  }
});

async function hmemCall(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
  let op: unknown, args: unknown;
  try {
    ({ op, args } = JSON.parse(await readBody(req)) ?? {});
    if (typeof op !== "string" || !Object.hasOwn(HMEM_OPS, op)) throw new BadRequest(`unknown op ${JSON.stringify(op)}; known: ${Object.keys(HMEM_OPS).join(", ")}`);
    send(res, 200, { ok: true, result: HMEM_OPS[op](memory(), (args ?? {}) as Args) });
  } catch (err) {
    send(res, err instanceof BadRequest || err instanceof SyntaxError ? 400 : 422, { ok: false, error: (err as Error).message });
  }
}

server.listen(PORT, HOST, () => {
  console.log(`Meterless World Model on http://${HOST}:${PORT} (${POLICY}: ${SOURCE_PRIORITY.join(" > ")})`);
});

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
