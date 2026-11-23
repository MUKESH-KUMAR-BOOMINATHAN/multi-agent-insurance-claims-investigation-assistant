"""
scripts/hash_password.py

Generate the two secrets the API needs, without ever writing a password to disk:

    uv run python scripts/hash_password.py

Prompts for a password (hidden input), then prints:
  * ADMIN_PASSWORD_HASH  - scrypt hash to paste into your host's environment settings
  * JWT_SECRET           - a fresh random signing secret (use a DIFFERENT one per environment)

Paste both into Render's Environment tab (or your local .env). Do not commit them.
"""

import getpass
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auth  # noqa: E402

MIN_LENGTH = 12


def main() -> int:
    password = getpass.getpass("New password: ")
    if len(password) < MIN_LENGTH:
        print(f"Password must be at least {MIN_LENGTH} characters.", file=sys.stderr)
        return 1
    if password != getpass.getpass("Repeat password: "):
        print("Passwords do not match.", file=sys.stderr)
        return 1

    print("\nSet these in your environment (never commit them):\n")
    print(f"ADMIN_PASSWORD_HASH={auth.hash_password(password)}")
    print(f"JWT_SECRET={secrets.token_urlsafe(48)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
