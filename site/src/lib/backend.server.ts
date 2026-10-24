// Where the GreenTrace engine runs. Server-side only: the browser always calls /api/gt/*.
// VITE_GT_ENGINE, read at build time, points a local preview at a local engine; the deployed
// site leaves it unset and talks to the public demo engine.
const ENGINE_ORIGIN = (import.meta.env.VITE_GT_ENGINE as string | undefined) || "https://greentrace-0qd7.onrender.com";

export function backendOrigin(): string {
  return ENGINE_ORIGIN.replace(/\/+$/, "");
}
