"""Rebuild every trap case from the seed and write the manifest.

    python -m scripts.build_data           # writes data/build/case_XX.sqlite + data/cases_manifest.json
    python -m scripts.build_data --check   # rebuilds in memory and fails if a fingerprint changed
"""
import json
import sys
from pathlib import Path

from app.world.cases import CASES, build_case
from app.world.store import DATA_DIR, dq_invalid_records, to_sqlite, world_sha256

BUILD_DIR = DATA_DIR / "build"
MANIFEST = DATA_DIR / "cases_manifest.json"


def manifest_entries() -> list[dict]:
    entries = []
    for case in CASES:
        world = build_case(case.number)
        entries.append({
            "case": case.number, "slug": case.slug, "change": case.change,
            "expected": case.expected, "rules_only_forecast": case.rules_only,
            "unavailable_sources": list(case.unavailable_sources),
            "rows": {table: len(world[table]) for table in
                     ("suppliers", "plots", "deliveries", "alerts", "report_paragraphs")},
            "world_sha256": world_sha256(world),
        })
    return entries


def main(argv: list[str]) -> int:
    entries = manifest_entries()
    text = json.dumps({"seed": 2026, "cases": entries}, indent=2, ensure_ascii=False) + "\n"
    if "--check" in argv:
        if not MANIFEST.exists() or MANIFEST.read_text(encoding="utf-8") != text:
            print("MISMATCH: the generator no longer rebuilds the committed manifest")
            return 1
        print(f"OK: {len(entries)} cases rebuilt identically")
        return 0

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        connection = to_sqlite(build_case(case.number), BUILD_DIR / f"case_{case.number:02d}.sqlite")
        issues = dq_invalid_records(connection)
        connection.close()
        print(f"case {case.number:02d} {case.slug:<32} {case.expected:<13} dq_issues={len(issues)}")
    MANIFEST.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {MANIFEST.relative_to(DATA_DIR.parent)} and {len(entries)} SQLite files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
