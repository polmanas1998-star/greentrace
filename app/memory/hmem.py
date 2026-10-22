"""H-MEM, the second Meterless engine: what GreenTrace remembers from one investigation to the next.

The engine is the unmodified Meterless H-MEM reference (meterless/hmem), behind the door
POST /hmem of meterless/server.ts, on disk in data/memory/ (state.json + ledger.jsonl, never
committed, erasable). GreenTrace uses it for one thing:

  - at the end of an investigation, the Writer stores one note per supplier a finding
    concerns: the supplier's verdict, the finding, the run and its date, the seal of its
    receipt, and a date after which the note is stale (TTL_DAYS);
  - at the next investigation of the same company, the Investigator reads these notes
    first, and the trace says so; the evidence file then shows "Seen before" and what
    changed since.

Memory never decides: the verdict is recomputed by app/rules.py on the day's data, the
notes never enter the evidence, the receipt or its seal, and tests/test_hmem.py proves it.
H-MEM is on only when HMEM_URL is set (the Docker image sets it); unset, or unreachable,
GreenTrace investigates without memory and says so when it was expected.

    python -m app.memory.hmem --forget chocodurable    # erase what is remembered of a company
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional

from app.explain.suppliers import concerned_suppliers

SOURCE = "greentrace:writer"
TTL_DAYS = 90  # a supplier check older than a quarter is stale: investigate again before relying on it
TIMEOUT_S = 5.0
RECALL_LIMIT = 500


class HMemUnavailable(RuntimeError):
    """The H-MEM door did not answer, or refused the call."""


@dataclass(frozen=True)
class Note:
    supplier_id: str
    company_id: str
    run_id: str
    run_date: str          # YYYY-MM-DD
    expires: str           # YYYY-MM-DD
    verdict: str           # the supplier's: CONTRADICTED if one of its findings is, else UNVERIFIABLE
    because: str           # "A-221" or "P-042": what the finding rests on
    finding: str           # the finding's sentence
    findings: tuple[str, ...]  # "code|ref,ref" signatures, to tell what changed
    seal: str              # verdict_sha256 of the run's receipt
    memory_id: str = ""

    def said(self) -> str:
        return (f"remembered from run {self.run_id} on {self.run_date}: {self.supplier_id} "
                f"{self.verdict.lower()} by {self.because}")


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


class HMem:
    """The client of the H-MEM door: store (add, delete), retrieval (query), ledger (history)."""

    def __init__(self, url: str, today: Callable[[], date] = today_utc):
        self.url, self.today = url, today

    def call(self, op: str, args: dict[str, Any]) -> Any:
        body = json.dumps({"op": op, "args": args}).encode("utf-8")
        request = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                answer = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise HMemUnavailable(f"{self.url}: {_error(error)}") from error
        except (OSError, ValueError) as error:
            raise HMemUnavailable(f"{self.url}: {error}") from error
        if not answer.get("ok"):
            raise HMemUnavailable(f"{self.url}: {answer.get('error')}")
        return answer["result"]

    # ---- the one use: notes per supplier ---------------------------------------------------------

    def remember(self, note: Note) -> str:
        tags = ["greentrace", f"company:{note.company_id}", f"supplier:{note.supplier_id}", f"run:{note.run_id}",
                f"date:{note.run_date}", f"expires:{note.expires}", f"verdict:{note.verdict}",
                f"because:{note.because}", f"seal:{note.seal}"]
        record = self.call("add", {
            "content": f"{note.supplier_id}: {note.verdict} on run {note.run_id} of {note.run_date}: {note.finding}",
            "source": SOURCE, "label": f"GreenTrace run {note.run_id}", "tags": tags, "refs": list(note.findings),
        })
        return record["id"]

    def recall(self, company_id: str) -> tuple[list[Note], list[str]]:
        """(the latest live note per supplier of the company, ids of the stale notes forgotten now)."""
        result = self.call("query", {"query": f"supplier notes of company {company_id}", "threshold": 0,
                                     "topN": RECALL_LIMIT})
        notes = [n for n in (_note(r["memory"]) for r in result["memories"])
                 if n and n.company_id == company_id]
        today = self.today().isoformat()
        stale = [n for n in notes if n.expires < today]
        for note in stale:
            self.call("delete", {"id": note.memory_id, "reason": f"stale since {note.expires}"})
        latest: dict[str, Note] = {}
        for note in sorted((n for n in notes if n.expires >= today), key=lambda n: (n.run_date, _seq(n))):
            latest[note.supplier_id] = note
        return [latest[s] for s in sorted(latest)], [n.memory_id for n in stale]

    def forget(self, company_id: str) -> int:
        result = self.call("query", {"query": f"supplier notes of company {company_id}", "threshold": 0,
                                     "topN": RECALL_LIMIT})
        ids = [n.memory_id for n in (_note(r["memory"]) for r in result["memories"])
               if n and n.company_id == company_id]
        for memory_id in ids:
            self.call("delete", {"id": memory_id, "reason": "forgotten on request"})
        return len(ids)

    def history(self, memory_id: str) -> list[dict]:
        return self.call("history", {"id": memory_id})


def from_env(env: Mapping[str, str] = os.environ) -> Optional[HMem]:
    url = env.get("HMEM_URL", "").strip()
    return HMem(url) if url else None


# ---- what a run leaves, and what changed since -----------------------------------------------------------

def notes_for(evidence: Any, decision: dict, run_id: str, seal: str, run_date: date) -> list[Note]:
    """One note per supplier that a finding concerns: through a supplier ref, a plot ref, or untraced volume."""
    by_supplier: dict[str, list[dict]] = {}
    for finding in decision["findings"]:
        for supplier_id in concerned_suppliers(evidence, finding):
            by_supplier.setdefault(supplier_id, []).append(finding)

    expires = (run_date + timedelta(days=TTL_DAYS)).isoformat()
    notes = []
    for supplier_id, findings in sorted(by_supplier.items()):
        main = next((f for f in findings if f["verdict"] == "CONTRADICTED"), findings[0])
        notes.append(Note(supplier_id=supplier_id, company_id=evidence.company["company_id"], run_id=run_id,
                          run_date=run_date.isoformat(), expires=expires, verdict=main["verdict"],
                          because=_because(main), finding=main["detail"],
                          findings=tuple(sorted(_signature(f) for f in findings)), seal=seal))
    return notes


def seen_before(remembered: list[Note], now: list[Note]) -> list[dict[str, Any]]:
    """For each supplier remembered: then, now, and what changed. Information only, never evidence."""
    current = {n.supplier_id: n for n in now}
    out = []
    for old in remembered:
        new = current.get(old.supplier_id)
        then_set, now_set = set(old.findings), set(new.findings) if new else set()
        gone, added = sorted(then_set - now_set), sorted(now_set - then_set)
        if not new:
            change = "no finding concerns this supplier today"
        elif not gone and not added:
            change = "the same finding(s) as then"
        else:
            change = (f"{len(gone)} finding(s) gone, {len(added)} new" + (f"; verdict {old.verdict} then, "
                      f"{new.verdict} now" if old.verdict != new.verdict else ""))
        out.append({"supplier_id": old.supplier_id, "run_id": old.run_id, "date": old.run_date,
                    "verdict_then": old.verdict, "because_then": old.because, "finding_then": old.finding,
                    "verdict_now": new.verdict if new else None, "changed": change,
                    "findings_gone": gone, "findings_new": added, "seal_then": old.seal})
    return out


def _signature(finding: dict) -> str:
    return f"{finding['code']}|{','.join(finding['refs'])}"


def _because(finding: dict) -> str:
    refs = [ref.split(":", 1) for ref in finding["refs"] if ":" in ref]
    for kind in ("alert", "plot", "doc", "supplier", "company"):
        keys = [key for k, key in refs if k == kind]
        if keys:
            return ", ".join(keys)
    return finding["code"]


def _note(record: dict) -> Optional[Note]:
    if record.get("source") != SOURCE:
        return None
    tags = dict(t.split(":", 1) for t in record.get("tags", []) if ":" in t)
    try:
        return Note(supplier_id=tags["supplier"], company_id=tags["company"], run_id=tags["run"],
                    run_date=tags["date"], expires=tags["expires"], verdict=tags["verdict"],
                    because=tags["because"], finding=record["content"].split(": ", 2)[-1],
                    findings=tuple(record.get("provenance", {}).get("refs", [])), seal=tags["seal"],
                    memory_id=record["id"])
    except KeyError:
        return None


def _seq(note: Note) -> int:
    return int(note.memory_id.rsplit("-", 1)[-1]) if note.memory_id.rsplit("-", 1)[-1].isdigit() else 0


def _error(error: urllib.error.HTTPError) -> str:
    try:
        return json.loads(error.read().decode("utf-8"))["error"]
    except (OSError, ValueError, KeyError, TypeError):
        return f"HTTP {error.code}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="GreenTrace's H-MEM notes")
    parser.add_argument("--forget", metavar="COMPANY_ID", required=True, help="erase every note about this company")
    args = parser.parse_args(argv)
    memory = from_env()
    if memory is None:
        print("HMEM_URL is not set: there is no memory to erase (or delete data/memory/ by hand)")
        return 2
    print(f"forgot {memory.forget(args.forget)} note(s) about {args.forget}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
