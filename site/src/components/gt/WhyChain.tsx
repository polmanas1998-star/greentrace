// The why-chain of one investigation, drawn under an Ask GreenTrace answer: the evidence rows, the
// refutations the Evaluator tried on them, the finding, the rule that decided, the verdict, and the one
// change that would flip it. The engine builds it from the run's output (never from a model); the
// nodes the answer's lines name are in focus, the rest stay dim.

export type ChainNode = { id: string; kind: string; label: string; detail: string; status: string; focus: boolean };
export type Chain = { nodes: ChainNode[]; edges: string[][] };

const STAGES: Array<{ kind: string; title: string }> = [
  { kind: "evidence", title: "Evidence" },
  { kind: "check", title: "Tried to break it" },
  { kind: "finding", title: "Finding" },
  { kind: "rule", title: "Rule" },
  { kind: "verdict", title: "Verdict" },
];
const LABEL_CLASS: Record<string, string> = { verdict: "text-[15px] font-semibold", finding: "text-[14px] leading-snug" };
const VERDICT_COLOR: Record<string, string> = {
  CONTRADICTED: "var(--gt-contradicted)",
  PROVEN: "var(--gt-proven)",
  UNVERIFIABLE: "var(--gt-unverifiable)",
};

function Box({ node, delay }: { node: ChainNode; delay: number }) {
  const color = VERDICT_COLOR[node.status] ?? (node.status === "held" ? "var(--gt-contradicted)" : undefined);
  return (
    <div
      className="gt-chain-in rounded-[10px] border px-3 py-2"
      style={{
        animationDelay: `${delay}ms`,
        borderColor: node.focus ? "var(--gt-signal)" : "var(--gt-line)",
        background: node.focus ? "var(--gt-raised-2)" : "transparent",
        opacity: node.focus ? 1 : 0.62,
      }}
    >
      <div className={LABEL_CLASS[node.kind] ?? "gt-mono text-[12.5px]"} style={{ color }}>
        {node.label}
      </div>
      {node.detail && node.kind !== "verdict" && <div className="mt-1 text-[12.5px] leading-snug text-gt-muted">{node.detail}</div>}
    </div>
  );
}

function Arrow() {
  return (
    <div aria-hidden className="flex shrink-0 items-center justify-center text-gt-muted md:px-1">
      <span className="md:hidden">↓</span>
      <span className="hidden md:inline">→</span>
    </div>
  );
}

export default function WhyChain({ chain }: { chain: Chain }) {
  const stages = STAGES.map((s) => ({ ...s, nodes: chain.nodes.filter((n) => n.kind === s.kind) })).filter((s) => s.nodes.length);
  const flip = chain.nodes.find((n) => n.kind === "flip");
  if (!stages.length) return null;
  return (
    <figure className="mt-4 rounded-[12px] border border-gt-line bg-gt-ground/60 p-4" aria-label="Why-chain of this verdict">
      <div className="flex flex-col gap-2 md:flex-row md:items-stretch">
        {stages.map((stage, i) => (
          <div key={stage.kind} className="contents">
            {i > 0 && <Arrow />}
            <div className="min-w-0 flex-1">
              <div className="gt-mono mb-2 text-[11px] uppercase tracking-[0.08em] text-gt-muted">{stage.title}</div>
              <div className="flex flex-col gap-2">
                {stage.nodes.map((node, j) => (
                  <Box key={node.id} node={node} delay={i * 220 + j * 60} />
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>
      {flip && (
        <div
          className="gt-chain-in mt-3 rounded-[10px] border border-dashed px-3 py-2 text-[13px] leading-snug"
          style={{ animationDelay: `${stages.length * 220}ms`, borderColor: flip.focus ? "var(--gt-signal)" : "var(--gt-line-strong)" }}
        >
          <span className="gt-mono text-[12px] text-gt-signal">What would change it · </span>
          <span className="text-gt-text/85">{flip.detail}</span>
        </div>
      )}
      <figcaption className="mt-3 text-[12px] text-gt-muted">Drawn from this investigation's output by the rules. Highlighted: what the answer's lines name.</figcaption>
    </figure>
  );
}
