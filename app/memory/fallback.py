"""In-memory twin of the Meterless World Model, used only when its server is not running.

meterless/server.ts serves the unmodified Meterless reference. This file mirrors
the part of it GreenTrace calls, with the same rules and the same JSON shapes:
provenance on every write, content-addressed ids, facts settled by
source-priority, an edge closed by a newer edge with the same from, type and
context, one audit entry per write, snapshots by replaying the event log.
Entities are always externally keyed here (type:system:id); the reference's
fuzzy name merging is not needed by GreenTrace and is not copied.
"""
import copy
import hashlib
import json
from typing import Any, Callable

POLICY = "source-priority"
SOURCE_PRIORITY = ("evaluator", "investigator", "extractor")  # highest priority first
MAX_WORLDS = 200  # one world per run, as on the server

OPS = ("upsertEntity", "relate", "assertFact", "entity", "facts", "entityAt", "timeline", "audit")


class EngineError(ValueError):
    """The engine refused a call: no provenance, a missing argument, an unknown operation."""


def _plain_numbers(value: Any) -> Any:
    """JavaScript has one number type: 1.0 prints as 1. Match it so ids hash the same."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _plain_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_numbers(item) for item in value]
    return value


def _js_json(value: Any) -> str:
    """The reference's canonicalJson / JSON.stringify for plain JSON values."""
    return json.dumps(_plain_numbers(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def fact_id(about: str, predicate: str, value: Any, at: str, kind: str, url: str | None) -> str:
    return "fact_" + _sha16(f"{about}|{predicate}|{_js_json(value)}|{at}|{kind}|{url or ''}")


def relationship_id(frm: str, type_: str, to: str, context: str | None, valid_from: str | None) -> str:
    return "rel_" + _sha16(f"{frm}|{type_}|{to}|{context or ''}|{valid_from or ''}")


def _without_none(record: dict[str, Any]) -> dict[str, Any]:
    """JSON drops undefined members; so do we."""
    return {key: value for key, value in record.items() if value is not None}


def _text(args: dict[str, Any], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value:
        raise EngineError(f"{name} must be a non-empty string")
    return value


class _State:
    def __init__(self) -> None:
        self.entities: dict[str, dict] = {}
        self.relationships: dict[str, dict] = {}
        self.facts: dict[str, dict] = {}

    def fold(self, event: dict[str, Any]) -> None:
        """Apply one event, exactly as the reference's EventStore.fold does."""
        kind, payload = event["kind"], event["payload"]
        if kind == "upsertEntity":
            existing = self.entities.get(payload["id"])
            if existing:
                existing["attrs"] = {**existing["attrs"], **payload["attrs"]}
                existing["updatedAt"] = payload["updatedAt"]
                existing["source"] = payload["source"]
                name = payload.get("name")
                if name and name not in existing["aliases"]:
                    existing["aliases"].append(name)
                if name:
                    existing["name"] = name
            else:
                self.entities[payload["id"]] = copy.deepcopy(payload)
        elif kind == "relate":
            for other in self.relationships.values():
                if (other["from"] == payload["from"] and other["type"] == payload["type"]
                        and (other.get("context") or "") == (payload.get("context") or "")
                        and other["to"] != payload["to"]
                        and not other.get("validTo") and not other.get("supersededBy")):
                    other["validTo"] = payload.get("validFrom") or payload["createdAt"]
                    other["supersededBy"] = payload["id"]
            if payload["id"] not in self.relationships:
                self.relationships[payload["id"]] = copy.deepcopy(payload)
        elif kind == "assertFact":
            if payload["id"] not in self.facts:
                self.facts[payload["id"]] = copy.deepcopy(payload)


class _World:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.audit_log: list[dict] = []
        self.state = _State()

    def _append(self, kind: str, payload: dict, at: str) -> dict:
        seq = len(self.events) + 1
        event = {"id": f"evt_{seq}", "seq": seq, "kind": kind, "payload": payload, "schemaVersion": 1, "at": at}
        self.events.append(event)
        self.state.fold(event)
        return event

    def _log(self, kind: str, source: dict, summary: str, event_id: str) -> None:
        self.audit_log.append(_without_none({
            "seq": len(self.audit_log) + 1, "at": source["at"], "kind": kind, "actor": source.get("by") or "system",
            "summary": summary, "eventId": event_id, "runId": source.get("runId"),
        }))

    @staticmethod
    def _provenance(source: Any) -> dict:
        if not isinstance(source, dict) or not source.get("kind") or source.get("at") is None:
            raise EngineError("World Model: every write carries provenance (kind + at). No provenance, no write.")
        return dict(source)

    # -------------------------------------------------------------- writes

    def upsertEntity(self, args: dict) -> dict:
        source = self._provenance(args.get("source"))
        type_ = _text(args, "type")
        key = args.get("externalKey")
        if not isinstance(key, dict) or not key.get("system") or not key.get("id"):
            raise EngineError("this engine stores externally keyed entities only (externalKey {system, id})")
        entity_id = f"{type_}:{key['system']}:{key['id']}"
        attrs = dict(args.get("attrs") or {})
        name = args.get("name") or attrs.get("name")  # the reference reads a display name from attrs too
        entity = _without_none({
            "type": type_, "id": entity_id, "attrs": attrs, "schemaVersion": 1, "source": source,
            "createdAt": source["at"], "updatedAt": source["at"], "name": name or None,
            "aliases": [name] if name else [], "state": args.get("state") or "active",
        })
        event = self._append("upsertEntity", entity, source["at"])
        self._log("upsertEntity", source, f"{entity_id} ({type_})", event["id"])
        return self.state.entities[entity_id]

    def relate(self, args: dict) -> dict:
        source = self._provenance(args.get("source"))
        frm, type_, to = _text(args, "from"), _text(args, "type"), _text(args, "to")
        context = args.get("context")
        rel_id = relationship_id(frm, type_, to, context, args.get("validFrom"))
        if rel_id in self.state.relationships:
            return self.state.relationships[rel_id]  # content-addressed no-op
        relationship = _without_none({
            "id": rel_id, "from": frm, "type": type_, "to": to, "context": context, "attrs": args.get("attrs"),
            "validFrom": args.get("validFrom") or source["at"], "validTo": args.get("validTo"),
            "schemaVersion": 1, "source": source, "createdAt": source["at"],
        })
        event = self._append("relate", relationship, source["at"])
        self._log("relate", source, f"{frm} -[{type_}]-> {to}", event["id"])
        return self.state.relationships[rel_id]

    def assertFact(self, args: dict) -> dict:
        source = self._provenance(args.get("source"))
        about, predicate = _text(args, "about"), _text(args, "predicate")
        if "value" not in args:
            raise EngineError("value is required")
        value, at = args["value"], source["at"]
        new_id = fact_id(about, predicate, value, at, source["kind"], source.get("url"))
        if new_id in self.state.facts:
            return self.state.facts[new_id]

        fact = _without_none({
            "id": new_id, "about": about, "predicate": predicate, "value": value,
            "confidence": args.get("confidence", 0.5), "context": args.get("context"),
            "source": source, "assertedAt": at,
        })
        rivals = [f for f in self.state.facts.values()
                  if f["about"] == about and f["predicate"] == predicate and not f.get("supersededBy")]
        winner = fact
        for rival in rivals:
            if _rank(rival) < _rank(winner):
                winner = rival
        event = self._append("assertFact", fact, at)
        for loser in [fact, *rivals]:
            if loser["id"] != winner["id"]:
                self.state.facts[loser["id"]]["supersededBy"] = winner["id"]

        if about not in self.state.entities:
            self._append("upsertEntity", {
                "type": about.split(":")[0] if ":" in about else "entity", "id": about, "attrs": {},
                "schemaVersion": 1, "source": source, "createdAt": at, "updatedAt": at, "aliases": [],
                "state": "emerging",
            }, at)
        entity = self.state.entities[about]
        entity["attrs"][predicate] = winner["value"]
        entity["updatedAt"] = at

        self._log("assertFact", source, f"{about}.{predicate} = {_js_json(winner['value'])} ({POLICY})", event["id"])
        return self.state.facts[new_id]

    # --------------------------------------------------------------- reads

    def entity(self, args: dict) -> dict | None:
        return self.state.entities.get(_text(args, "id"))

    def facts(self, args: dict) -> list[dict]:
        about = _text(args, "id")
        return sorted((f for f in self.state.facts.values() if f["about"] == about), key=lambda f: f["assertedAt"])

    def entityAt(self, args: dict) -> dict | None:
        entity_id, at = _text(args, "id"), _text(args, "at")
        until = f"{at}T23:59:59.999Z" if len(at) == 10 else at
        replay = _State()
        for event in self.events:
            if event["at"] <= until:
                replay.fold(event)
        return replay.entities.get(entity_id)

    def timeline(self, args: dict) -> list[dict]:
        return self.events

    def audit(self, args: dict) -> list[dict]:
        return self.audit_log


def _rank(fact: dict) -> int:
    kind = fact["source"]["kind"]
    return SOURCE_PRIORITY.index(kind) if kind in SOURCE_PRIORITY else len(SOURCE_PRIORITY)


class MemoryEngine:
    """Same calls and answers as meterless/server.ts, inside this Python process."""

    provider = "memory"
    name = "world-model-fallback"

    def __init__(self) -> None:
        self._worlds: dict[str, _World] = {}

    def call(self, namespace: str, op: str, args: dict[str, Any]) -> Any:
        if op not in OPS:
            raise EngineError(f"unknown op {op!r}; known: {', '.join(OPS)}")
        try:
            args = json.loads(json.dumps(args))  # what crosses the wire to the server, nothing more
        except (TypeError, ValueError) as error:
            raise EngineError(f"arguments are not plain JSON: {error}") from error
        world = self._worlds.get(namespace)
        if world is None:
            world = self._worlds[namespace] = _World()
            if len(self._worlds) > MAX_WORLDS:
                del self._worlds[next(iter(self._worlds))]
        handler: Callable[[dict], Any] = getattr(world, op)
        return json.loads(json.dumps(handler(args)))  # a copy, as if it came back over HTTP
