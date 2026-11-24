"""scripts/backup_db.py + scripts/restore_db.py round trip (also the SQLite -> Postgres move)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import backup_db  # noqa: E402
import restore_db  # noqa: E402
from agents import feedback_node as fb  # noqa: E402
from db import claims_store, security_store, users_store  # noqa: E402
from db import engine as db_engine  # noqa: E402
from tests.conftest import sqlite_only  # noqa: E402


@sqlite_only
def test_backup_then_restore_into_an_empty_database_round_trips(tmp_path, monkeypatch):
    claim_id = claims_store.insert_claim({"k": 1}, {"status": "ok"}, [], {"recommendation": "approve"}, submitted_by="carol")
    users_store.create_user("carol", "Carol", "customer", "scrypt$hash", "admin", profile={"age": 40})
    security_store.audit("login", actor="carol")
    fb.feedback_node({"x": 1}, {}, {}, "agreed", "N", claim_id)
    data = backup_db.dump()

    monkeypatch.setattr(claims_store, "DB_PATH", tmp_path / "new-claims.db")
    monkeypatch.setattr(fb, "DB_PATH", tmp_path / "new-feedback.db")
    db_engine.reset_for_tests()
    counts = restore_db.restore(data)

    assert counts["claims"] == 1 and counts["users"] == 1 and counts["feedback"] == 1
    assert claims_store.get_claim(claim_id)["submitted_by"] == "carol"
    assert users_store.get_active_user("CAROL")["profile"] == {"age": 40}
    assert claims_store.insert_claim({}, {}, [], {}) == claim_id + 1  # ids continue after the restored ones


def test_restore_refuses_to_touch_a_database_that_has_data():
    claims_store.insert_claim({}, {}, [], {})
    with pytest.raises(SystemExit, match="not empty"):
        restore_db.restore(backup_db.dump())


def test_backup_never_contains_plain_passwords():
    users_store.create_user("dan", "Dan", "investigator", "scrypt$16384$8$1$aa$bb", "admin")
    dumped = str(backup_db.dump())
    assert "scrypt$" in dumped  # hashes only
