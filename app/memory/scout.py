"""Meterless Scout Intent, the third engine: guard before a model reads, a signed contract before Ask acts.

The engine runs in meterless/server.ts (POST /scout), implemented from the Scout spec and verified with
its unmodified eval harness (meterless/scout-evals). Two uses in GreenTrace:

- guard(texts): every report paragraph is checked for injection (instruction override, role confusion,
  answer forcing, control bytes...) before the Extractor's model sees it; a blocked paragraph is
  quarantined from the model, named in the trace, and still read by the rules like any other row.
- decide(question): an Ask question becomes a signed execution contract (intent, confidence, scope);
  Ask verifies it before answering, and a blocked question gets no lines at all.

When the server does not answer, each caller says so in the trace and goes on as before: Scout adds a
guard, it is never the only one (the <data> tags stay, and the rules decide the verdict either way).
"""
import json
import os
import urllib.error
import urllib.request
from typing import Any, Optional

from app.memory.world_model import CONNECT_TIMEOUT_S, DEFAULT_URL

TIMEOUT_S = 3.0
ENGINE = "scout-intent"


class ScoutUnavailable(Exception):
    pass


def _send(body: dict[str, Any], url: Optional[str] = None) -> Any:
    base = (url or os.environ.get("WORLD_MODEL_URL") or DEFAULT_URL).rstrip("/")
    request = urllib.request.Request(base + "/scout", data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # this machine: no proxy
    try:
        with opener.open(request, timeout=max(TIMEOUT_S, CONNECT_TIMEOUT_S)) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise ScoutUnavailable(f"{base}/scout: {error}") from error
    if not answer.get("ok"):
        raise ScoutUnavailable(f"{base}/scout: {answer.get('error')}")
    return answer["result"]


def guard(texts: list[str], url: Optional[str] = None) -> list[dict[str, Any]]:
    """One guard result per text: {"level": "low" | "block", "flags": [...], "reason"?}."""
    return _send({"op": "guard_all", "texts": texts}, url)


def decide(question: str, url: Optional[str] = None) -> dict[str, Any]:
    return _send({"op": "decide", "prompt": question, "surface": "ask"}, url)


def verify(contract: dict[str, Any], capability: str, url: Optional[str] = None) -> dict[str, Any]:
    return _send({"op": "verify", "contract": contract, "capability": capability}, url)
