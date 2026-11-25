"""Create or remove the demo's sample data by hand.

The API seeds itself on its first start when DEMO_ENABLED is true, so you normally
never run this. It exists for two cases: seeding a database from your own machine
(with DATABASE_URL set to Neon), and wiping the demo rows when the project goes real.

    uv run python scripts/seed_demo.py              # create the samples if missing
    uv run python scripts/seed_demo.py --reset      # delete all demo rows, then re-seed
    uv run python scripts/seed_demo.py --purge      # delete all demo rows (sandboxes, templates, samples)

The samples are real rows of the public dataset run through the real pipeline (with a
Groq key set, the recommendations are real AI output, paced to stay inside the
per-minute budget). They are marked as demo data and never feed the retraining data
or the similar-claims evidence.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import demo  # noqa: E402
from db import claims_store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--reset", action="store_true", help="delete all demo rows, then seed again")
    group.add_argument("--purge", action="store_true", help="delete all demo rows and stop")
    args = parser.parse_args()

    target = "Postgres (DATABASE_URL)" if config.DATABASE_URL else f"SQLite ({claims_store.DB_PATH})"
    print(f"Database: {target}")
    claims_store.init_db()

    if args.reset or args.purge:
        print(f"Deleted {claims_store.delete_demo_data(include_seed=True)} demo rows.")
        if args.purge:
            return 0

    print("Building the sample claims (this runs the real pipeline and can take a few minutes with Groq)...")
    outcome = demo.ensure_seed()
    print(
        f"{outcome}: {claims_store.count_tagged(claims_store.SEED)} samples, "
        f"{claims_store.count_tagged(claims_store.TEMPLATE)} templates."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
