import { useRef, useState, type FormEvent } from "react";

import WhyChain, { type Chain } from "./WhyChain";

// "Ask GreenTrace": a chat about one finished investigation. It speaks AG-UI to this site's agent
// (/api/agui): it posts a RunAgentInput with the run id in its context and reads the event stream.
// Answers come from the run's own lines (the engine's POST /runs/{id}/ask), never from elsewhere.

type Turn = { role: "user" | "assistant"; text: string; chain?: Chain };

const SUGGESTIONS = ["Why is this the verdict?", "Which refutations were tried?", "What would change the verdict?"];

export default function AskPanel({ runId }: { runId: string }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const threadId = useRef(crypto.randomUUID());

  async function ask(question: string) {
    const text = question.trim();
    if (!text || busy) return;
    setBusy(true);
    setDraft("");
    const history: Turn[] = [...turns, { role: "user", text }];
    setTurns([...history, { role: "assistant", text: "" }]);
    try {
      const response = await fetch("/api/agui", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "text/event-stream" },
        body: JSON.stringify({
          threadId: threadId.current,
          runId: crypto.randomUUID(),
          messages: history.map((t, i) => ({ id: String(i), role: t.role, content: t.text })),
          context: [{ description: "GreenTrace run id", value: runId }],
          tools: [],
          state: {},
          forwardedProps: {},
        }),
      });
      if (!response.body) throw new Error(`${response.status}`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() ?? "";
        for (const raw of events) {
          const data = raw.replace(/^data: /, "");
          try {
            const event = JSON.parse(data) as { type: string; delta?: string; name?: string; value?: Chain };
            if (event.type === "TEXT_MESSAGE_CONTENT" && event.delta) {
              setTurns((all) => all.map((t, i) => (i === all.length - 1 ? { ...t, text: t.text + event.delta } : t)));
            } else if (event.type === "CUSTOM" && event.name === "greentrace.why_chain" && Array.isArray(event.value?.nodes)) {
              const chain = event.value;
              setTurns((all) => all.map((t, i) => (i === all.length - 1 ? { ...t, chain } : t)));
            }
          } catch {
            /* a partial or unknown event: skipped */
          }
        }
      }
    } catch (error) {
      setTurns((all) =>
        all.map((t, i) => (i === all.length - 1 ? { ...t, text: `The chat could not reach the engine (${error instanceof Error ? error.message : "error"}).` } : t)),
      );
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(draft);
  }

  return (
    <div className="flex h-[680px] flex-col overflow-hidden rounded-[14px] border border-gt-line bg-gt-raised">
      <div className="flex-1 space-y-5 overflow-y-auto p-6" aria-live="polite">
        {turns.length === 0 && (
          <div>
            <p className="text-lg leading-relaxed text-gt-text/85">Ask about this investigation. Answers come only from its own record.</p>
            <div className="mt-5 flex flex-wrap gap-2">
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" onClick={() => void ask(s)} className="rounded-full border border-gt-line-strong px-4 py-2 text-[14px] text-gt-text/85 transition-colors hover:border-gt-signal/60">
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className={t.role === "user" ? "flex justify-end" : ""}>
            <div
              className={
                t.role === "user"
                  ? "max-w-[80%] rounded-[14px] bg-gt-raised-2 px-4 py-3 text-[15px]"
                  : "w-full max-w-full whitespace-pre-wrap text-[15px] leading-relaxed text-gt-text/90"
              }
            >
              {t.text || <span className="gt-mono text-[13px] text-gt-muted">reading the investigation…</span>}
              {t.chain && <WhyChain chain={t.chain} />}
            </div>
          </div>
        ))}
      </div>
      <form onSubmit={submit} className="flex items-center gap-3 border-t border-gt-line p-4">
        <label htmlFor="ask" className="sr-only">
          Your question
        </label>
        <input
          id="ask"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          maxLength={500}
          placeholder="Ask about this verdict, a plot, an alert, a refutation"
          className="flex-1 rounded-[10px] border border-gt-line bg-gt-ground px-4 py-3 text-[15px] text-gt-text placeholder:text-gt-muted/70 focus:border-gt-signal/60 focus:outline-none"
        />
        <button type="submit" disabled={busy || !draft.trim()} className="bg-gt-signal px-5 py-3 text-[15px] font-semibold text-gt-ground transition-transform active:translate-y-px disabled:opacity-50">
          Ask
        </button>
      </form>
    </div>
  );
}
