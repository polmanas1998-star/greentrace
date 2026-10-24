import { createFileRoute } from "@tanstack/react-router";

import { backendOrigin } from "../../../lib/backend.server";

// The page talks to its own origin; this route relays to the GreenTrace engine (FastAPI on
// Render), so the engine needs no CORS rule and the engine address never reaches the browser.
// Only the engine's public routes are relayed: anything else is a 404, not an open proxy.
const ALLOWED = [
  /^health$/,
  /^run$/,
  /^runs$/,
  /^runs\/[A-Za-z0-9_-]{1,64}(\/[A-Za-z0-9_.-]+)*$/,
  /^ui\/runs(\/[A-Za-z0-9_-]{1,64}\/(plain|where))?$/,
  /^citizen$/,
  /^api\/(check-plots|timelapse|plot-map|plot-measure|lens|plots)(\/[A-Za-z0-9_.-]+)*$/,
  /^static\/(example_plots\.geojson|maps\/[A-Za-z0-9_.-]+)$/,
  /^\.well-known\/agent(-card)?\.json$/,
];

const PASS_RESPONSE_HEADERS = ["content-type", "content-disposition", "cache-control", "last-modified", "etag"];
const MAX_BODY_BYTES = 4 * 1024 * 1024;

async function relay({ request, params }: { request: Request; params: { _splat?: string } }) {
  const path = (params._splat ?? "").replace(/^\/+/, "");
  if (!ALLOWED.some((pattern) => pattern.test(path))) {
    return Response.json({ detail: `not an engine route: /${path}` }, { status: 404 });
  }
  const incoming = new URL(request.url);
  const target = `${backendOrigin()}/${path}${incoming.search}`;

  const headers = new Headers();
  for (const name of ["content-type", "accept", "last-event-id"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  let body: ArrayBuffer | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    body = await request.arrayBuffer();
    if (body.byteLength > MAX_BODY_BYTES) {
      return Response.json({ detail: "file too large (4 MB at most)" }, { status: 413 });
    }
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, { method: request.method, headers, body, redirect: "manual" });
  } catch {
    return Response.json(
      { detail: "The GreenTrace engine did not answer. It may be waking up: try again in a minute." },
      { status: 502 },
    );
  }

  const out = new Headers();
  for (const name of PASS_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  if ((upstream.headers.get("content-type") ?? "").includes("text/event-stream")) {
    out.set("cache-control", "no-cache");
    out.set("x-accel-buffering", "no");
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export const Route = createFileRoute("/api/gt/$")({
  server: {
    handlers: {
      GET: relay,
      POST: relay,
    },
  },
});
