"""What every agent of one run shares: the data gate, the World Model, the trace."""
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.memory.world_model import WorldModel
from app.queries import DataGate
from app.tracing import now_utc, trace


@dataclass
class RunContext:
    run_id: str
    gate: DataGate
    memory: WorldModel
    company_id: str
    log: Callable[[dict], Any] = trace
    model: Optional[Any] = None          # app.adapters.llm client; None = rule fallbacks only
    max_rounds: int = 3
    notes: dict[str, Any] = field(default_factory=dict)

    @property
    def degraded(self) -> bool:
        return self.model is None

    def step(self, agent: str, action: str, input_summary: str, output_summary: str, *,
             target: Optional[str] = None, confidence: Optional[float] = None,
             status: str = "success", provider: Optional[str] = None, rows: Optional[int] = None) -> None:
        self.log({
            "timestamp": now_utc(), "run_id": self.run_id, "agent_name": agent, "action": action,
            "input_summary": input_summary[:300], "output_summary": output_summary[:300],
            "target_agent": target, "confidence": confidence, "retry_count": 0, "status": status,
            "provider": provider or ("rules" if self.degraded else None), "model": None, "rows": rows,
        })
