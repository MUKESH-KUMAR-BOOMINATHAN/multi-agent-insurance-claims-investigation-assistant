"""
Back up every table (claims, accounts, feedback, lockouts, audit log) to one JSON file.

Works on both backends: with DATABASE_URL set it reads Postgres (e.g. Neon),
otherwise the local SQLite files. Free hosting tiers keep only a short restore
history, so run this regularly and keep the files somewhere safe (they contain
personal data and password HASHES: treat them as confidential).

    python scripts/backup_db.py                       -> backups/claimguard-<UTC time>.json
    python scripts/backup_db.py --out my-backup.json

Restore with scripts/restore_db.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from agents import feedback_node  # noqa: E402
from db import claims_store  # noqa: E402
from db import engine as db_engine  # noqa: E402
from db.schema import CLAIMS_META, FEEDBACK_META  # noqa: E402

FORMAT_VERSION = 1


def dump() -> dict:
    tables: dict[str, list[dict]] = {}
    for url, meta in ((claims_store.database_url(), CLAIMS_META), (feedback_node.database_url(), FEEDBACK_META)):
        engine = db_engine.ensure_schema(url, meta)
        with engine.connect() as conn:
            for table in meta.sorted_tables:
                rows = conn.execute(select(table).order_by(*table.primary_key.columns)).mappings().all()
                tables[table.name] = [dict(r) for r in rows]
    return {
        "format": FORMAT_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "tables": tables,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, help="output file (default: backups/claimguard-<time>.json)")
    args = parser.parse_args()

    data = dump()
    out = args.out or Path("backups") / f"claimguard-{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    counts = ", ".join(f"{name}={len(rows)}" for name, rows in data["tables"].items())
    print(f"Backup written to {out} ({counts})")


if __name__ == "__main__":
    main()
