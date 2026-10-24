"""The only questions agents may ask the data (kit, section 3.1), on two engines.

The same SQL runs on Zetaris (live, through the semantic layer `eudr`) and on
SQLite (sample mode, the synthetic world in memory). Every answer keeps its SQL,
its rows and a fingerprint of the rows: that is what the verdict receipt replays.

Values are inlined, because the Zetaris REST API takes no bound parameters, so each
one is checked against a strict pattern first: nothing a user types reaches SQL as is.
"""
import re
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.adapters.zetaris import USL, Zetaris, ZetarisError
from app.canonical import sha256_of
from app.tracing import now_utc, trace
from app.world.store import dq_invalid_records, load_dq_rules

TEMPLATES = {
    "Q1": ("SELECT doc_id, section, paragraph_no, paragraph_text FROM {t}report_paragraphs "
           "WHERE doc_id = {doc_id} ORDER BY section DESC, paragraph_no"),
    "Q2": ("SELECT s.supplier_id, s.name, s.cooperative_group, c.total_tonnes, c.fiscal_year "
           "FROM {t}suppliers s JOIN {t}company c ON s.company_id = c.company_id "
           "WHERE c.company_id = {company_id} ORDER BY s.supplier_id"),
    "Q3": ("SELECT plot_id, supplier_id, lat, lon, polygon_wkt, area_ha FROM {t}plots "
           "WHERE supplier_id IN ({supplier_ids}) ORDER BY plot_id, supplier_id"),
    "Q4": ("SELECT alert_id, lat, lon, alert_date, confidence FROM {t}alerts "
           "WHERE lat BETWEEN {lat_min} AND {lat_max} AND lon BETWEEN {lon_min} AND {lon_max} "
           "ORDER BY alert_id"),
    "Q5": ("SELECT plot_id, year, tonnes, supplier_id FROM {t}deliveries "
           "WHERE plot_id = {plot_id} ORDER BY year, supplier_id"),
    "Q6": ("SELECT d.delivery_id, d.supplier_id, d.plot_id, d.year, d.tonnes, d.certified "
           "FROM {t}deliveries d JOIN {t}suppliers s ON d.supplier_id = s.supplier_id "
           "WHERE s.company_id = {company_id} AND d.year BETWEEN {year_from} AND {year_to} "
           "ORDER BY d.delivery_id"),
    "Q7": ("SELECT p.plot_id, p.supplier_id FROM {t}plots p JOIN {t}suppliers s ON p.supplier_id = s.supplier_id "
           "WHERE s.company_id = {company_id} AND p.plot_id IN "
           "(SELECT plot_id FROM {t}plots GROUP BY plot_id HAVING COUNT(*) > 1) "
           "ORDER BY p.plot_id, p.supplier_id"),
    "Q8": ("SELECT supplier_id, name, cooperative_group, company_id FROM {t}suppliers "
           "WHERE name = {name} ORDER BY supplier_id"),
    "Q9": ("SELECT alert_id, alert_date, confidence, system FROM {t}alerts "
           "WHERE alert_id = {alert_id}"),
    "Q10": ("SELECT lat, lon, forest_fraction_2020, loss_year FROM {t}reference_cells "
            "WHERE lat BETWEEN {lat_min} AND {lat_max} AND lon BETWEEN {lon_min} AND {lon_max} "
            "ORDER BY lat DESC, lon"),
}
SOURCE_OF = {"Q0": "registry", "Q1": "documents", "Q2": "registry", "Q3": "registry", "Q4": "alerts",
             "Q5": "registry", "Q6": "registry", "Q7": "registry", "Q8": "registry", "Q9": "alerts",
             "Q10": "reference_maps"}

_SAFE_TEXT = re.compile(r"^[\w .,-]{1,80}$")


class SourceUnavailable(RuntimeError):
    """A source did not answer after its retry: the claim part becomes UNVERIFIABLE."""


def _literal(value: Any) -> str:
    if isinstance(value, bool):
        raise ValueError("booleans are not query parameters")
    if isinstance(value, (int, float)):
        return repr(float(value)) if isinstance(value, float) else str(int(value))
    if isinstance(value, str) and _SAFE_TEXT.match(value):
        return f"'{value}'"
    raise ValueError(f"unsafe query parameter: {value!r}")


def render(query_id: str, table_prefix: str, **params: Any) -> str:
    values = {k: ", ".join(_literal(v) for v in p) if isinstance(p, (list, tuple)) else _literal(p)
              for k, p in params.items()}
    return TEMPLATES[query_id].format(t=table_prefix, **values)


@dataclass(frozen=True)
class Answer:
    query_id: str
    sql: str
    rows: list[dict[str, Any]]
    agent: str = ""  # who asked: the verdict receipt rebuilds the evidence from the Investigator's answers

    @property
    def rows_sha256(self) -> str:
        return sha256_of(self.rows)


# ---- the two engines ----------------------------------------------------------------

class SQLiteEngine:
    """Sample mode: the synthetic world in memory, the reference cells attached. Mirrors a source that is down."""
    name, prefix = "sqlite", ""

    def __init__(self, connection: sqlite3.Connection, unavailable: tuple[str, ...] = ()):
        self.connection, self.unavailable = connection, set(unavailable)
        self._lock = threading.Lock()  # the connection is shared across request threads

    def run(self, query_id: str, sql: str) -> list[dict[str, Any]]:
        if SOURCE_OF[query_id] in self.unavailable:
            raise SourceUnavailable(f"source '{SOURCE_OF[query_id]}' did not answer")
        try:
            with self._lock:
                return [dict(row) for row in self.connection.execute(sql)]
        except sqlite3.OperationalError as error:
            if "no such table" not in str(error):
                raise
            raise SourceUnavailable(f"source '{SOURCE_OF[query_id]}' is not loaded: {error}") from error

    def data_quality(self) -> list[dict[str, Any]]:
        return dq_invalid_records(self.connection)


class ZetarisEngine:
    """Live mode: Zetaris, through the semantic layer only."""
    name, prefix = "zetaris", f"{USL}."

    def __init__(self, zetaris: Zetaris):
        self.zetaris = zetaris

    def run(self, query_id: str, sql: str) -> list[dict[str, Any]]:
        try:
            return self.zetaris.run(sql)
        except ZetarisError as error:
            raise SourceUnavailable(str(error)) from error

    def data_quality(self) -> list[dict[str, Any]]:
        found = []
        for rule in load_dq_rules():
            table = f"{USL}.{rule['table']}"
            for row in self.run("Q0", f"SHOW DQ INVALID RECORD {rule['name']} TABLE {table}"):
                found.append({"rule": rule["name"], "table": rule["table"], "entity_id": row[rule["key"]]})
        return found


# ---- the gate the agents use ----------------------------------------------------------

class DataGate:
    """Runs one allowed query, retries once, and writes one trace line per attempt."""

    def __init__(self, engine: SQLiteEngine | ZetarisEngine, run_id: str,
                 log: Optional[Callable[[dict], Any]] = None, retries: int = 1):
        self.engine, self.run_id, self.retries = engine, run_id, retries
        self.log = log or trace
        self.answers: list[Answer] = []

    def ask(self, agent: str, query_id: str, **params: Any) -> Answer:
        sql = render(query_id, self.engine.prefix, **params) if query_id != "Q0" else "RUN DQ + SHOW DQ INVALID RECORD"
        for attempt in range(self.retries + 1):
            try:
                rows = self.engine.data_quality() if query_id == "Q0" else self.engine.run(query_id, sql)
            except SourceUnavailable as error:
                last = attempt == self.retries
                self._line(agent, "error" if last else "retry", query_id, params, str(error)[:120],
                           attempt, "failed" if last else "retrying", None)
                if last:
                    raise
                continue
            answer = Answer(query_id, sql, rows, agent)
            self.answers.append(answer)
            self._line(agent, "query_zetaris", query_id, params, f"{len(rows)} row(s)", attempt, "success",
                       len(rows))
            return answer
        raise AssertionError("unreachable")

    def _line(self, agent, action, query_id, params, output, attempt, status, rows):
        shown = ", ".join(f"{k}={v}" for k, v in params.items()) or "no parameter"
        self.log({
            "timestamp": now_utc(), "run_id": self.run_id, "agent_name": agent, "action": action,
            "input_summary": f"{query_id}({shown})"[:200], "output_summary": output,
            "target_agent": None, "confidence": None, "retry_count": attempt, "status": status,
            "provider": self.engine.name, "model": None, "rows": rows,
        })
