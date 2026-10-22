"""Write a world into SQLite (sample mode), and run the data-quality rules there."""
import json
import sqlite3
from pathlib import Path
from typing import Any

from app.canonical import sha256_of

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
SCHEMA_SQL = DATA_DIR / "schema.sql"
DQ_RULES = DATA_DIR / "dq_rules.json"

TABLES = ("company", "suppliers", "plots", "deliveries", "alerts", "report_paragraphs")


def world_sha256(world: dict[str, Any]) -> str:
    """Fingerprint of the data tables only (metadata excluded)."""
    return sha256_of({table: world[table] for table in TABLES})


def to_sqlite(world: dict[str, Any], path: str | Path = ":memory:") -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).unlink(missing_ok=True)
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    with connection:
        for table in TABLES:
            rows = world[table]
            if not rows:
                continue
            columns = list(rows[0])
            placeholders = ", ".join("?" for _ in columns)
            connection.executemany(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                [tuple(row[c] for c in columns) for row in rows],
            )
    return connection


def load_dq_rules() -> list[dict[str, str]]:
    return json.loads(DQ_RULES.read_text(encoding="utf-8"))["rules"]


def dq_invalid_records(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Sample-mode twin of Zetaris RUN DQ + SHOW DQ INVALID RECORD.

    Rule text comes from our own committed file, never from user input.
    """
    found = []
    for rule in load_dq_rules():
        sql = (f"SELECT {rule['key']} AS entity_id FROM {rule['table']} "
               f"WHERE NOT ({rule['valid_when']}) ORDER BY {rule['key']}")
        for row in connection.execute(sql):
            found.append({"rule": rule["name"], "table": rule["table"], "entity_id": row["entity_id"]})
    return found
