"""
Restore a backup made by scripts/backup_db.py into an EMPTY database.

Also the way to move data between backends, e.g. local SQLite -> Neon Postgres:

    # 1. on your machine, with DATABASE_URL unset (reads the local SQLite files)
    python scripts/backup_db.py --out move.json
    # 2. point at the target and restore
    DATABASE_URL="postgresql://...neon.tech/neondb?sslmode=require" python scripts/restore_db.py move.json

It refuses to write into a database that already has claims, accounts or
feedback, so it can never merge or overwrite data by accident.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, insert, select  # noqa: E402

from agents import feedback_node  # noqa: E402
from db import claims_store  # noqa: E402
from db import engine as db_engine  # noqa: E402
from db.schema import CLAIMS_META, FEEDBACK_META  # noqa: E402

SUPPORTED_FORMATS = {1}


def restore(data: dict) -> dict[str, int]:
    if data.get("format") not in SUPPORTED_FORMATS:
        raise SystemExit(f"Unsupported backup format: {data.get('format')!r}")
    tables = data["tables"]
    restored: dict[str, int] = {}
    for url, meta in ((claims_store.database_url(), CLAIMS_META), (feedback_node.database_url(), FEEDBACK_META)):
        engine = db_engine.ensure_schema(url, meta)
        with engine.begin() as conn:  # all-or-nothing per database
            for table in meta.sorted_tables:
                if conn.execute(select(func.count()).select_from(table)).scalar_one():
                    raise SystemExit(f"Refusing to restore: table '{table.name}' is not empty.")
            for table in meta.sorted_tables:
                rows = tables.get(table.name, [])
                known = set(table.columns.keys())
                rows = [{k: v for k, v in r.items() if k in known} for r in rows]
                if rows:
                    conn.execute(insert(table), rows)
                restored[table.name] = len(rows)
                # Postgres: move the id sequence past the restored ids.
                if db_engine.is_postgres(url) and "id" in known and rows:
                    sequence = func.pg_get_serial_sequence(table.name, "id")
                    conn.execute(select(func.setval(sequence, select(func.max(table.c.id)).scalar_subquery())))
    return restored


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("backup", type=Path, help="a JSON file written by backup_db.py")
    args = parser.parse_args()
    counts = restore(json.loads(args.backup.read_text(encoding="utf-8")))
    print("Restored: " + ", ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
