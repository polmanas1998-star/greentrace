import { createFileRoute } from "@tanstack/react-router";

import { backendOrigin } from "../../lib/backend.server";

// "Ask GreenTrace" as an AG-UI agent (the open Agent-User Interaction protocol CopilotKit speaks):
// POST a RunAgentInput, read back a Server-Sent Events stream of AG-UI events. Any AG-UI client
// (a CopilotKit HttpAgent, this site's own chat) can talk to it. The agent has no model of its own:
// it forwards the last user question to the engine's POST /runs/{id}/ask, which answers from that
// run's lines only, and streams the answer back word by word.

const RUN_CONTEXT = "GreenTrace run id";
const RUN_ID = /^[A-Za-z0-9_-]{1,64}$/;

type Message = { role?: string; content?: unknown };
type RunAgentInput = {
  threadId?: string;
  runId?: string;
  messages?: Message[];
  context?: Array<{ description?: string; value?: unknown }>;
};
type Ask = {
  answer: string;
  lines: Array<{ n: number; text: string; section: string }>;
  provider: string | null;
  model: string | null;
  status: string;
};

function textOf(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map((part) => (part && typeof part === "object" && "text" in part ? String(part.text) : "")).join(" ");
  }
  return "";
}

function runIdOf(input: RunAgentInput): string | null {
  for (const entry of input.context ?? []) {
    if (entry.description !== RUN_CONTEXT) continue;
    let value: unknown = entry.value;
    try {
      value = JSON.parse(String(entry.value));
    } catch {
      /* a plain string */
    }
    if (typeof value === "string" && RUN_ID.test(value)) return value;
  }
  return null;
}

function render(ask: Ask): string {
  const by = ask.status === "model" ? `Answered by ${ask.model ?? ask.provider}` : "Answered by the rules, no model";
  const lines = ask.lines.map((l) => `- (${l.section}) ${l.text}`).join("\n");
  return `${ask.answer}${lines ? `\n\n${lines}` : ""}\n\n${by}, from this investigation's own lines.`;
}

async function answerFor(input: RunAgentInput): Promise<string> {
  const question = textOf([...(input.messages ?? [])].reverse().find((m) => m.role === "user")?.content).trim();
  const runId = runIdOf(input);
  if (!runId) return "Run an investigation first: I answer questions about one investigation, from its own record.";
  if (!question) return "Ask me about this verdict, a plot, an alert or a refutation.";
  try {
    const response = await fetch(`${backendOrigin()}/runs/${runId}/ask`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question: question.slice(0, 500) }),
    });
    const body = (await response.json()) as Ask & { detail?: string };
    if (!response.ok) return `The engine could not answer: ${body.detail ?? response.status}`;
    return render(body);
  } catch {
    return "The GreenTrace engine did not answer. It may be waking up: ask again in a minute.";
  }
}

async function run({ request }: { request: Request }) {
  let input: RunAgentInput;
  try {
    input = (await request.json()) as RunAgentInput;
  } catch {
    return Response.json({ detail: "body must be an AG-UI RunAgentInput (JSON)" }, { status: 400 });
  }
  const threadId = input.threadId ?? crypto.randomUUID();
  const runId = input.runId ?? crypto.randomUUID();
  const messageId = crypto.randomUUID();
  const encoder = new TextEncoder();

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (event: Record<string, unknown>) => controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
      send({ type: "RUN_STARTED", threadId, runId });
      const text = await answerFor(input);
      send({ type: "TEXT_MESSAGE_START", messageId, role: "assistant" });
      for (const chunk of text.match(/\S+\s*/g) ?? [text]) send({ type: "TEXT_MESSAGE_CONTENT", messageId, delta: chunk });
      send({ type: "TEXT_MESSAGE_END", messageId });
      send({ type: "RUN_FINISHED", threadId, runId });
      controller.close();
    },
  });
  return new Response(stream, { headers: { "content-type": "text/event-stream", "cache-control": "no-cache" } });
}

export const Route = createFileRoute("/api/agui")({
  server: { handlers: { POST: run } },
});
