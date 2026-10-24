"""Write a world into SQLite (sample mode), and run the data-quality rules there.

The reference cells (716 800 rows) do not change between runs: they are built once
into an indexed SQLite file, data/build/reference_cells.sqlite, and attached to each
run's database, so query Q10 runs on SQLite as it runs on Zetaris.
"""
import gzip
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Optional

from app.canonical import sha256_of
from app.checks.landcover import CELLS_FILE, COLUMNS

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
SCHEMA_SQL = DATA_DIR / "schema.sql"
DQ_RULES = DATA_DIR / "dq_rules.json"

TABLES = ("company", "suppliers", "plots", "deliveries", "alerts", "report_paragraphs")
REFERENCE_DB = DATA_DIR / "build" / "reference_cells.sqlite"
REFERENCE_DDL = """
CREATE TABLE reference_cells (
    lat                  REAL NOT NULL,
    lon                  REAL NOT NULL,
    forest_fraction_2020 REAL NOT NULL,
    loss_year            INTEGER NOT NULL,
    PRIMARY KEY (lat, lon)
) WITHOUT ROWID;
CREATE TABLE source (sha256 TEXT NOT NULL);
"""
_built: dict[tuple, Path] = {}


def world_sha256(world: dict[str, Any]) -> str:
    """Fingerprint of the data tables only (metadata excluded)."""
    return sha256_of({table: world[table] for table in TABLES})


def to_sqlite(world: dict[str, Any], path: str | Path = ":memory:") -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).unlink(missing_ok=True)
    # A run is created in one request thread and questioned again by what_if in another.
    connection = sqlite3.connect(str(path), check_same_thread=False)
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


# ---- the reference cells ---------------------------------------------------------------------

def reference_db(cells_file: Path = CELLS_FILE, db: Path = REFERENCE_DB) -> Optional[Path]:
    """The cells in an indexed SQLite file, rebuilt when the committed file changes; None without cells."""
    if not cells_file.exists():
        return None
    stamp = (str(cells_file), cells_file.stat().st_size, cells_file.stat().st_mtime_ns, str(db))
    if stamp in _built and db.exists():
        return db
    digest = hashlib.sha256(cells_file.read_bytes()).hexdigest()
    if _source_sha256(db) != digest:
        _build_reference(cells_file, db, digest)
    _built[stamp] = db
    return db


def attach_reference(connection: sqlite3.Connection) -> bool:
    """Attach the reference cells to a run's database (read only by the queries); False without cells."""
    db = reference_db()
    if db is None:
        return False
    connection.execute("ATTACH DATABASE ? AS reference", (str(db),))
    return True


def _source_sha256(db: Path) -> Optional[str]:
    if not db.exists():
        return None
    try:
        # closing(): "with sqlite3.connect()" commits but leaves the file open, and Windows
        # then refuses to replace it when the cells change.
        with closing(sqlite3.connect(db)) as connection:
            row = connection.execute("SELECT sha256 FROM source").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def _build_reference(cells_file: Path, db: Path, digest: str) -> None:
    """Write to a temporary file, then rename it: a reader never sees a half-built table."""
    db.parent.mkdir(parents=True, exist_ok=True)
    partial = db.with_name(f"{db.name}.{os.getpid()}.partial")
    partial.unlink(missing_ok=True)
    connection = sqlite3.connect(partial)
    try:
        connection.executescript(REFERENCE_DDL)
        with gzip.open(cells_file, "rt", encoding="utf-8", newline="") as handle:
            if handle.readline().strip() != COLUMNS:
                raise ValueError(f"{cells_file.name}: expected the columns {COLUMNS}")
            rows = (line.rstrip("\n").split(",") for line in handle)
            connection.executemany("INSERT INTO reference_cells VALUES (?, ?, ?, ?)",
                                   ((float(a), float(b), float(c), int(d)) for a, b, c, d in rows))
        connection.execute("INSERT INTO source VALUES (?)", (digest,))
        connection.commit()
    finally:
        connection.close()
    os.replace(partial, db)
