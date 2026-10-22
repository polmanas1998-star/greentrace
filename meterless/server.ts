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

import http from "node:http";
import { WorldModel } from "./reference/src/index.ts";

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

const server = http.createServer(async (req, res) => {
  if (req.method === "GET" && req.url === "/health") {
    return send(res, 200, { ok: true, engine: "meterless/world-model reference", conflictPolicy: POLICY, sourcePriority: SOURCE_PRIORITY });
  }
  if (req.method !== "POST" || req.url !== "/call") {
    return send(res, 404, { ok: false, error: "not found: use GET /health or POST /call" });
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

server.listen(PORT, HOST, () => {
  console.log(`Meterless World Model on http://${HOST}:${PORT} (${POLICY}: ${SOURCE_PRIORITY.join(" > ")})`);
});

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
