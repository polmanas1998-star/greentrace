"""Zetaris over REST: one SQL statement in, typed rows out.

API from the official Vibe Coding Starter Guide: POST {ZETARIS_REST_URL}/sql-editor/sqls/run
with a bearer key, the organisation id and a fresh request id. Zetaris returns every
value as text, so rows are typed here from the column names we know.
"""
import os
import uuid
from dataclasses import dataclass
from typing import Any

import requests

USL = "lightning.metastore.greentrace.eudr"
TIMEOUT_S = 20.0

FLOAT_COLUMNS = {"lat", "lon", "area_ha", "tonnes", "total_tonnes", "forest_fraction_2020"}
INT_COLUMNS = {"year", "fiscal_year", "paragraph_no", "loss_year", "total", "valid", "invalid"}


class ZetarisError(RuntimeError):
    """Zetaris answered with an error, or did not answer in time."""


def _typed(column: str, value: Any) -> Any:
    if value is None or value == "null":
        return None
    if column in FLOAT_COLUMNS:
        return float(value)
    if column in INT_COLUMNS:
        return int(float(value))
    return value


@dataclass(frozen=True)
class Zetaris:
    rest_url: str
    api_key: str
    org_id: str
    timeout_s: float = TIMEOUT_S

    @classmethod
    def from_env(cls) -> "Zetaris":
        missing = [n for n in ("ZETARIS_REST_URL", "ZETARIS_API_KEY", "ZETARIS_ORG_ID") if not os.environ.get(n)]
        if missing:
            raise ZetarisError(f"missing in .env: {', '.join(missing)}")
        return cls(os.environ["ZETARIS_REST_URL"].rstrip("/"), os.environ["ZETARIS_API_KEY"],
                   os.environ["ZETARIS_ORG_ID"])

    def run(self, sql: str, limit: int = 10_000, timeout_s: float | None = None) -> list[dict[str, Any]]:
        try:
            response = requests.post(
                f"{self.rest_url}/sql-editor/sqls/run",
                headers={"Authorization": f"Bearer {self.api_key}", "X-Org-ID": self.org_id,
                         "X-Request-ID": str(uuid.uuid4())},
                json={"queryId": str(uuid.uuid4()), "sql": sql, "source": "SqlEditor", "limit": limit},
                timeout=timeout_s or self.timeout_s,
            )
        except requests.RequestException as error:
            raise ZetarisError(f"no answer from Zetaris: {type(error).__name__}") from error
        if response.status_code != 200:
            raise ZetarisError(f"Zetaris {response.status_code}: {response.text[:300]}")
        body = response.json()
        headers = body.get("headers") or []
        return [{c: _typed(c, v) for c, v in zip(headers, row)} for row in body.get("data") or []]
