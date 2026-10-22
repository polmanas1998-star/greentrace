"""The agents' shared memory: Meterless's World Model, one world per run.

Every entity, fact and edge the agents share lives here, with who wrote it and
which Zetaris query it was read from. The engine is the unmodified Meterless
reference, served by meterless/server.ts on this machine. Conflicting facts
are settled by the engine's source-priority policy (Evaluator over
Investigator over Extractor), so a refutation supersedes what it refutes and
nothing is deleted.

Ours, on top of the engine: depends_on. A fact is written with the entities or
facts it was derived from (based_on edges). When one of them changes ("try to
fool it"), invalidate() marks every fact that depended on it, directly or
through another fact, with an invalidated_by edge: only those need re-checking.

If the server does not answer, the same calls run on app/memory/fallback.py and
every trace line says "degraded". Record times are logical, one tick per write
call and never the wall clock, so the same case builds the same world twice.
"""
import json
import os
import re
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Protocol, TextIO

from app.memory.fallback import POLICY, SOURCE_PRIORITY, EngineError, MemoryEngine
from app.tracing import now_utc, trace

AGENTS = ("Extractor", "Planner", "Investigator", "Evaluator", "Writer", "Pipeline")
SYSTEM = "eudr"  # ids follow Meterless's type:system:id grammar; eudr is the Zetaris semantic layer
DEFAULT_URL = "http://127.0.0.1:8787"
CONNECT_TIMEOUT_S = 1.0
CALL_TIMEOUT_S = 5.0

BASED_ON = "based_on"  # fact -> what it was derived from
INVALIDATED_BY = "invalidated_by"  # fact -> the changed entity that made it stale
_RESERVED_EDGES = (BASED_ON, INVALIDATED_BY)

_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_QUERY_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_LISTED = 8  # ids spelled out in one trace summary


class WorldModelUnavailable(ConnectionError):
    """The World Model server did not answer (not running, stopped mid-run, or not a World Model)."""


class Engine(Protocol):
    provider: str
    name: str

    def call(self, namespace: str, op: str, args: dict[str, Any]) -> Any: ...


class HttpEngine:
    """meterless/server.ts: the Meterless reference behind JSON over HTTP."""

    provider = "meterless"
    name = "world-model-reference"

    def __init__(self, url: str = DEFAULT_URL, timeout: float = CALL_TIMEOUT_S) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        # The server is on this machine: never send its traffic through a proxy.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def health(self) -> dict[str, Any]:
        return self._send("GET", "/health", None, CONNECT_TIMEOUT_S)

    def call(self, namespace: str, op: str, args: dict[str, Any]) -> Any:
        return self._send("POST", "/call", {"namespace": namespace, "op": op, "args": args}, self.timeout)["result"]

    def _send(self, method: str, path: str, body: Optional[dict], timeout: float) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.url + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            message = _error_message(error)
            if error.code < 500:
                raise EngineError(message) from error  # the engine refused the call, and says why
            raise WorldModelUnavailable(f"{self.url}: {message}") from error
        except (OSError, ValueError) as error:  # refused, timed out, or not JSON
            raise WorldModelUnavailable(f"{self.url}: {error}") from error


def _error_message(error: urllib.error.HTTPError) -> str:
    try:
        return json.loads(error.read().decode("utf-8"))["error"]
    except (OSError, ValueError, KeyError, TypeError):
        return f"HTTP {error.code}"


def connect(url: Optional[str] = None) -> tuple[Engine, Optional[str]]:
    """The Meterless server if it answers with our conflict policy, else the fallback and why."""
    engine = HttpEngine(url or os.environ.get("WORLD_MODEL_URL") or DEFAULT_URL)
    try:
        health = engine.health()
    except (WorldModelUnavailable, EngineError) as error:
        return MemoryEngine(), f"server unreachable ({error})"
    if health.get("conflictPolicy") != POLICY or health.get("sourcePriority") != list(SOURCE_PRIORITY):
        return MemoryEngine(), (f"server at {engine.url} runs {health.get('conflictPolicy')} "
                                f"{health.get('sourcePriority')}, expected {POLICY} {list(SOURCE_PRIORITY)}")
    return engine, None


@dataclass(frozen=True)
class Source:
    """Who writes a record, and the Zetaris query or document it was read from."""

    agent: str
    query_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.agent not in AGENTS:
            raise ValueError(f"source agent must be one of {AGENTS}, got {self.agent!r}")
        if self.query_id is not None and not (isinstance(self.query_id, str) and _QUERY_ID.match(self.query_id)):
            raise ValueError(f"query_id must match {_QUERY_ID.pattern}, got {self.query_id!r}")


def _checked_name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _NAME.match(value):
        raise ValueError(f"{what} must match {_NAME.pattern}, got {value!r}")
    return value


def entity_id(type_: str, key: str) -> str:
    """plot, P-014 -> plot:eudr:P-014, the id Meterless gives an externally keyed entity."""
    return f"{_checked_name(type_, 'entity type')}:{SYSTEM}:{_checked_name(key, 'entity key')}"


def tick_at(tick: int) -> str:
    """Logical clock: write call n is stamped n milliseconds after the epoch, never the wall clock."""
    return (_EPOCH + timedelta(milliseconds=tick)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _listed(ids: Iterable[str]) -> str:
    ids = list(ids)
    shown = ", ".join(ids[:_LISTED])
    return shown + (f" (+{len(ids) - _LISTED} more)" if len(ids) > _LISTED else "")


class WorldModel:
    """One run's world. Writes take a Source; reads name the agent reading (Pipeline by default)."""

    def __init__(
        self,
        run_id: str,
        *,
        engine: Optional[Engine] = None,
        log_dir: Optional[Path] = None,
        stream: Optional[TextIO] = None,
    ) -> None:
        self.run_id = run_id
        self._log_dir, self._stream = log_dir, stream
        self._tick = 0
        self._known: set[str] = set()  # ids written in this run: depends_on may only name these
        reason = None
        if engine is None:
            engine, reason = connect()
        self._engine = engine
        self.degraded = engine.provider != HttpEngine.provider
        where = getattr(engine, "url", None)
        self._trace("Pipeline", "connect_world_model", "shared memory for this run",
                    f"in-memory fallback: {reason or 'engine given'}" if self.degraded
                    else f"Meterless World Model at {where}, {POLICY}")

    @property
    def tick(self) -> int:
        """Number of write calls so far: entity_at(id, tick) replays the world up to that write."""
        return self._tick

    # ------------------------------------------------------------- writes

    def upsert_entity(self, type_: str, key: str, attrs: dict[str, Any], source: Source) -> dict[str, Any]:
        new_id = entity_id(type_, key)
        if not isinstance(attrs, dict):
            raise ValueError("attrs must be a dict")
        args = {"type": type_, "externalKey": {"system": SYSTEM, "id": key}, "attrs": attrs,
                "source": self._provenance(source)}
        entity = self._traced(
            source.agent, "upsert_entity", f"{new_id} attrs {sorted(attrs)}",
            lambda: self._call("upsertEntity", args),
            lambda e: f"{e['id']} now has {len(e['attrs'])} attr(s)",
        )
        self._known.add(entity["id"])
        return entity

    def assert_fact(
        self,
        about: str,
        predicate: str,
        value: Any,
        *,
        source: Source,
        confidence: float,
        depends_on: Iterable[str] = (),
    ) -> dict[str, Any]:
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
            raise ValueError(f"confidence must be a number from 0 to 1, got {confidence!r}")
        depends = sorted(set(depends_on))
        unknown = [d for d in depends if d not in self._known]
        if unknown:
            raise ValueError(f"depends_on names ids this run never wrote: {unknown}")
        provenance = self._provenance(source)

        def work() -> dict[str, Any]:
            fact = self._call("assertFact", {"about": about, "predicate": predicate, "value": value,
                                             "confidence": confidence, "source": provenance})
            for dependency in depends:
                # One context per dependency: a newer edge with the same from, type and
                # context would close the previous one (Meterless's bitemporal rule).
                self._call("relate", {"from": fact["id"], "type": BASED_ON, "to": dependency,
                                      "context": dependency, "source": provenance})
            return {**fact, "depends_on": depends, "status": "superseded" if fact.get("supersededBy") else "valid"}

        fact = self._traced(
            source.agent, "assert_fact", f"{about}.{predicate} = {json.dumps(value, default=str)} on {len(depends)} dependency(ies)",
            work, lambda f: f"{f['id']} {f['status']}", confidence=confidence,
        )
        self._known.update((fact["id"], about))
        return fact

    def relate(self, from_id: str, type_: str, to_id: str, *, source: Source,
               context: Optional[str] = None) -> dict[str, Any]:
        """An edge between two ids of this run. A newer edge with the same from, type and
        context closes the previous one: pass a context for many-to-many edges."""
        if type_ in _RESERVED_EDGES:
            raise ValueError(f"{type_} edges are written by assert_fact and invalidate only")
        unknown = [i for i in (from_id, to_id) if i not in self._known]
        if unknown:
            raise ValueError(f"relate names ids this run never wrote: {unknown}")
        args = {"from": from_id, "type": _checked_name(type_, "edge type"), "to": to_id,
                "source": self._provenance(source)}
        if context is not None:
            args["context"] = context
        return self._traced(
            source.agent, "relate", f"{from_id} -[{type_}]-> {to_id}",
            lambda: self._call("relate", args), lambda r: f"{r['id']}",
        )

    def invalidate(self, changed_id: str, *, source: Source) -> list[str]:
        """Mark every fact that depends on changed_id, directly or through another fact, as stale."""
        if changed_id not in self._known:
            raise ValueError(f"invalidate names an id this run never wrote: {changed_id!r}")
        provenance = self._provenance(source)

        def work() -> list[str]:
            based_on, invalidated = self._edges()
            dependents: dict[str, list[str]] = defaultdict(list)
            for fact, dependencies in based_on.items():
                for dependency in dependencies:
                    dependents[dependency].append(fact)
            stale, seen, frontier = [], {changed_id}, [changed_id]
            while frontier:
                node = frontier.pop(0)
                for fact in sorted(dependents[node]):
                    if fact not in seen:
                        seen.add(fact)
                        frontier.append(fact)
                        if fact not in invalidated:
                            stale.append(fact)
            for fact in stale:
                self._call("relate", {"from": fact, "type": INVALIDATED_BY, "to": changed_id,
                                      "context": changed_id, "source": provenance})
            return stale

        return self._traced(
            source.agent, "invalidate_facts", f"{changed_id} changed",
            work, lambda stale: f"{len(stale)} fact(s) invalidated" + (f": {_listed(stale)}" if stale else ""),
        )

    # -------------------------------------------------------------- reads

    def view(self, name: str, node_id: Optional[str] = None, *, by: str = "Pipeline") -> Any:
        """graph: one entity. facts: the facts about it, each with status and depends_on. timeline: every event."""
        if name in ("graph", "facts") and not node_id:
            raise ValueError(f"view {name!r} needs an entity id")
        if name == "graph":
            return self._read(by, "view", f"graph {node_id}", lambda: self._call("entity", {"id": node_id}),
                              lambda e: "not found" if e is None else f"{e['id']} with {len(e['attrs'])} attr(s)")
        if name == "facts":
            def facts() -> list[dict[str, Any]]:
                based_on, invalidated = self._edges()
                return [self._annotate(f, based_on, invalidated) for f in self._call("facts", {"id": node_id})]
            return self._read(by, "view", f"facts about {node_id}", facts, _count_statuses)
        if name == "timeline":
            return self._read(by, "view", "timeline", lambda: self._call("timeline", {}),
                              lambda events: f"{len(events)} event(s)")
        raise ValueError(f"unknown view {name!r}: use graph, facts or timeline")

    def audit(self, *, by: str = "Pipeline") -> list[dict[str, Any]]:
        return self._read(by, "audit", "every write of this run", lambda: self._call("audit", {}),
                          lambda entries: f"{len(entries)} audit entr(ies)")

    def entity_at(self, node_id: str, tick: int, *, by: str = "Pipeline") -> Optional[dict[str, Any]]:
        """The entity as it stood after write call number `tick` (see the tick property)."""
        if not isinstance(tick, int) or isinstance(tick, bool) or not 0 <= tick <= self._tick:
            raise ValueError(f"tick must be an integer from 0 to {self._tick}, got {tick!r}")
        return self._read(by, "entity_at", f"{node_id} at tick {tick}",
                          lambda: self._call("entityAt", {"id": node_id, "at": tick_at(tick)}),
                          lambda e: "not found" if e is None else f"{len(e['attrs'])} attr(s)")

    # ----------------------------------------------------------- internals

    def _provenance(self, source: Source) -> dict[str, Any]:
        if not isinstance(source, Source):
            raise ValueError("every write carries a Source (who wrote it, from which query)")
        self._tick += 1
        provenance = {"kind": source.agent.lower(), "by": source.agent, "at": tick_at(self._tick), "runId": self.run_id}
        if source.query_id:
            provenance["url"] = source.query_id
        return provenance

    def _call(self, op: str, args: dict[str, Any]) -> Any:
        return self._engine.call(self.run_id, op, args)

    def _edges(self) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
        """based_on: fact -> what it depends on. invalidated: fact -> what changed under it."""
        based_on: dict[str, set[str]] = defaultdict(set)
        invalidated: dict[str, set[str]] = defaultdict(set)
        for event in self._call("timeline", {}):
            edge = event["payload"]
            if event["kind"] == "relate" and edge["type"] == BASED_ON:
                based_on[edge["from"]].add(edge["to"])
            elif event["kind"] == "relate" and edge["type"] == INVALIDATED_BY:
                invalidated[edge["from"]].add(edge["to"])
        return based_on, invalidated

    @staticmethod
    def _annotate(fact: dict[str, Any], based_on: dict[str, set[str]],
                  invalidated: dict[str, set[str]]) -> dict[str, Any]:
        if fact["id"] in invalidated:
            status = "invalidated"
        elif fact.get("supersededBy"):
            status = "superseded"
        else:
            status = "valid"
        return {**fact, "depends_on": sorted(based_on.get(fact["id"], ())), "status": status}

    def _read(self, by: str, action: str, input_summary: str, work: Callable[[], Any],
              summarize: Callable[[Any], str]) -> Any:
        if by not in AGENTS:
            raise ValueError(f"reader must be one of {AGENTS}, got {by!r}")
        return self._traced(by, action, input_summary, work, summarize)

    def _traced(self, agent: str, action: str, input_summary: str, work: Callable[[], Any],
                summarize: Callable[[Any], str], confidence: Optional[float] = None) -> Any:
        """Run one call on the engine and write exactly one trace line for it, success or failure."""
        try:
            result = work()
        except (EngineError, WorldModelUnavailable) as error:
            self._trace(agent, action, input_summary, f"{type(error).__name__}: {error}", status="error")
            raise
        self._trace(agent, action, input_summary, summarize(result), confidence=confidence)
        return result

    def _trace(self, agent: str, action: str, input_summary: str, output_summary: str, *,
               confidence: Optional[float] = None, status: Optional[str] = None) -> None:
        trace(
            {
                "timestamp": now_utc(),
                "run_id": self.run_id,
                "agent_name": agent,
                "action": action,
                "input_summary": input_summary,
                "output_summary": output_summary,
                "target_agent": None,
                "confidence": confidence,
                "retry_count": 0,
                "status": status or ("degraded" if self.degraded else "success"),
                "provider": self._engine.provider,
                "model": self._engine.name,
                "rows": None,
            },
            log_dir=self._log_dir,
            stream=self._stream,
        )


def _count_statuses(facts: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for fact in facts:
        counts[fact["status"]] += 1
    return f"{len(facts)} fact(s)" + (": " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) if facts else "")
