"""Load one case of the synthetic world into Zetaris (live mode).

    python -m scripts.load_zetaris            # case 2, the demo
    python -m scripts.load_zetaris --case 16

1. The case's tables go into a `greentrace` database inside the Postgres that
   ships with the Zetaris stack, readable only by a dedicated read-only role.
2. Zetaris attaches that database (CREATE DATASOURCE gt_pg), builds the Unified
   Semantic Layer `eudr` over it (data/usl_eudr.sql), activates every table and
   registers the data-quality rules of data/dq_rules.json.
Re-running is safe: objects that already exist are kept, the data is replaced.
"""
import argparse
import os
import secrets
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

from app.adapters.zetaris import USL, Zetaris, ZetarisError
from app.world.cases import build_case
from app.world.store import DATA_DIR, SCHEMA_SQL, TABLES, load_dq_rules

ROOT = DATA_DIR.parent
ENV_FILE = ROOT / ".env"
PG_CONTAINER = os.environ.get("ZETARIS_PG_CONTAINER", "zetaris-postgres-1")
PG_ROLE, PG_DB, DATASOURCE = "greentrace_reader", "greentrace", "gt_pg"

ACTIVATE_COLUMNS = {
    "company": "company_id, name, fiscal_year, total_tonnes",
    "suppliers": "supplier_id, company_id, name, cooperative_group, country",
    "plots": "plot_id, supplier_id, lat, lon, polygon_wkt, area_ha, declared_by",
    "deliveries": "delivery_id, supplier_id, plot_id, year, tonnes, certified",
    "alerts": "alert_id, lat, lon, alert_date, confidence, area_ha, system",
    "report_paragraphs": "CONCAT(doc_id, ':', section, ':', CAST(paragraph_no AS STRING)) AS paragraph_key, "
                         "doc_id, section, paragraph_no, paragraph_text",
}


def reader_password() -> str:
    """A local, read-only database password, generated once and kept in .env (never printed)."""
    load_dotenv(ENV_FILE)
    if not os.environ.get("GT_PG_PASSWORD"):
        password = secrets.token_urlsafe(24)
        with open(ENV_FILE, "a", encoding="utf-8") as env:
            env.write(f"\n# Read-only role on the local greentrace database (scripts/load_zetaris.py)\n"
                      f"GT_PG_PASSWORD={password}\n")
        os.environ["GT_PG_PASSWORD"] = password
    return os.environ["GT_PG_PASSWORD"]


def psql(sql: str, database: str = "postgres") -> None:
    """Run SQL in the Zetaris Postgres through stdin, so nothing secret reaches the command line."""
    result = subprocess.run(
        ["docker", "exec", "-i", PG_CONTAINER, "psql", "-U", "zetaris", "-d", database,
         "-v", "ON_ERROR_STOP=1", "-q"],
        input=sql, text=True, encoding="utf-8", capture_output=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"psql failed: {result.stderr.strip()[:400]}")


def literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def load_postgres(world: dict, password: str) -> None:
    psql(f"""
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{PG_ROLE}') THEN
    CREATE ROLE {PG_ROLE} LOGIN PASSWORD {literal(password)};
  ELSE
    ALTER ROLE {PG_ROLE} PASSWORD {literal(password)};
  END IF;
END $$;
SELECT 'CREATE DATABASE {PG_DB}' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '{PG_DB}')\\gexec
""")
    statements = [f"DROP TABLE IF EXISTS {', '.join(reversed(TABLES))} CASCADE;",
                  SCHEMA_SQL.read_text(encoding="utf-8")]
    for table in TABLES:
        for row in world[table]:
            values = ", ".join(literal(v) for v in row.values())
            statements.append(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({values});")
    statements += [f"GRANT CONNECT ON DATABASE {PG_DB} TO {PG_ROLE};",
                   f"GRANT USAGE ON SCHEMA public TO {PG_ROLE};",
                   f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {PG_ROLE};"]
    psql("BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;\n", database=PG_DB)


def step(zetaris: Zetaris, label: str, sql: str) -> None:
    try:
        zetaris.run(sql, timeout_s=300)
        print(f"  ok      {label}")
    except ZetarisError as error:
        if "already exist" not in str(error).lower():
            raise
        print(f"  kept    {label} (already there)")


def setup_zetaris(zetaris: Zetaris, password: str) -> None:
    step(zetaris, f"datasource {DATASOURCE}", f"""CREATE DATASOURCE {DATASOURCE}
DESCRIBE BY "GreenTrace_synthetic_world" OPTIONS (
  jdbcdriver "org.postgresql.Driver",
  jdbcurl "jdbc:postgresql://postgres:5432/{PG_DB}",
  username "{PG_ROLE}",
  password "{password}",
  fetchsize "1000"
)""")
    step(zetaris, "fresh table metadata", f"UNCACHE DATASOURCE {DATASOURCE}")
    step(zetaris, "source tables", f"REGISTER DATASOURCE TABLES FROM {DATASOURCE}")
    # A fresh instance has no lightning.metastore yet: create the parent first.
    step(zetaris, "metastore namespace", "CREATE NAMESPACE IF NOT EXISTS lightning.metastore")
    step(zetaris, "namespace", "CREATE NAMESPACE IF NOT EXISTS lightning.metastore.greentrace")
    step(zetaris, "semantic layer eudr", (DATA_DIR / "usl_eudr.sql").read_text(encoding="utf-8").strip())
    for table, columns in ACTIVATE_COLUMNS.items():
        step(zetaris, f"activate {table}",
             f"ACTIVATE USL TABLE {USL}.{table} AS SELECT {columns} FROM {DATASOURCE}.{table}")
    for rule in load_dq_rules():
        step(zetaris, f"dq rule {rule['name']}",
             f"REGISTER DQ {rule['name']} TABLE {USL}.{rule['table']} AS {rule['valid_when']}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", type=int, default=2)
    case = parser.parse_args(argv).case

    password = reader_password()
    world = build_case(case)
    print(f"case {case:02d} ({world['meta']['slug']}): loading Postgres")
    load_postgres(world, password)
    print("setting up Zetaris")
    zetaris = Zetaris.from_env()
    setup_zetaris(zetaris, password)
    counts = {t: zetaris.run(f"SELECT COUNT(*) AS total FROM {USL}.{t}")[0]["total"] for t in TABLES}
    print("rows seen through the semantic layer:", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
