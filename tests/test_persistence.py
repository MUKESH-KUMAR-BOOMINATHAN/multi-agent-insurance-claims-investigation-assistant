"""claims_store + feedback_node persistence behaviour."""

import sqlite3

import pytest

from agents import feedback_node as fb
from db import claims_store


def test_insert_and_get_round_trip_including_warnings():
    claim_id = claims_store.insert_claim({"a": 1}, {"status": "ok"}, [{"s": 1}], {"recommendation": "investigate"}, ["w1"])
    row = claims_store.get_claim(claim_id)
    assert row["claim"] == {"a": 1} and row["warnings"] == ["w1"] and row["status"] == "pending"


def test_the_fraud_rate_summary_round_trips():
    claim_id = claims_store.insert_claim({}, {}, [], {}, [], "6 of the 15 most similar past claims were confirmed fraud.")
    assert claims_store.get_claim(claim_id)["similar_claims_summary"].startswith("6 of the 15")
    assert claims_store.get_claim(claims_store.insert_claim({}, {}, [], {}))["similar_claims_summary"] is None


def test_mark_reviewed_is_one_way_and_reports_whether_it_changed_anything():
    claim_id = claims_store.insert_claim({}, {}, [], {})
    assert claims_store.mark_reviewed(claim_id) is True
    assert claims_store.mark_reviewed(claim_id) is False
    assert claims_store.mark_reviewed(9999) is False


def test_list_claims_is_newest_first_and_bounded():
    ids = [claims_store.insert_claim({"n": i}, {}, [], {}) for i in range(5)]
    page = claims_store.list_claims(limit=2)
    assert [c["id"] for c in page] == [ids[4], ids[3]]
    assert len(claims_store.list_claims(limit=10_000)) == 5  # limit is clamped, not trusted


def test_corrupt_json_cell_does_not_break_the_whole_listing():
    good = claims_store.insert_claim({"ok": True}, {}, [], {})
    bad = claims_store.insert_claim({}, {}, [], {})
    conn = sqlite3.connect(claims_store.DB_PATH)
    conn.execute("UPDATE claims SET fraud_result_json = '{not json' WHERE id = ?", (bad,))
    conn.commit()
    conn.close()
    assert {c["id"] for c in claims_store.list_claims()} == {good, bad}
    assert claims_store.get_claim(bad)["fraud_result"] is None


def test_legacy_database_without_warnings_column_is_migrated(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy)
    conn.execute(
        "CREATE TABLE claims (id INTEGER PRIMARY KEY AUTOINCREMENT, claim_json TEXT NOT NULL, "
        "fraud_result_json TEXT, similar_claims_json TEXT, recommendation_json TEXT, "
        "status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, reviewed_at TEXT)"
    )
    conn.execute("INSERT INTO claims (claim_json, created_at) VALUES ('{}', '2025-01-01')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(claims_store, "DB_PATH", legacy)
    monkeypatch.setattr(claims_store, "_initialized_for", None)
    rows = claims_store.list_claims()
    assert len(rows) == 1 and rows[0]["warnings"] == []  # old row survives the migration
    assert rows[0]["similar_claims_summary"] is None  # the later column is migrated in too


def test_connection_is_closed_even_when_a_statement_fails(monkeypatch):
    opened = []
    real_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(claims_store.sqlite3, "connect", tracking_connect)
    with pytest.raises(sqlite3.OperationalError):
        with claims_store._connect() as conn:
            conn.execute("SELECT * FROM table_that_does_not_exist")
    with pytest.raises(sqlite3.ProgrammingError):  # closed connection cannot be used
        opened[-1].execute("SELECT 1")


def _save(claim_id):
    return fb.feedback_node({"x": 1}, {"fraud_probability": 0.9}, {"recommendation": "investigate"}, "agreed", "Y", claim_id)


def test_feedback_for_the_same_claim_is_rejected_not_duplicated():
    assert _save(1)["status"] == "ok"
    with pytest.raises(fb.DuplicateFeedbackError):
        _save(1)
    conn = sqlite3.connect(fb.DB_PATH)
    assert conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 1
    conn.close()


def test_feedback_without_claim_id_is_not_subject_to_uniqueness():
    _save(None)
    _save(None)  # CLI/smoke-test usage must keep working


def test_invalid_label_is_rejected_before_anything_is_written():
    with pytest.raises(ValueError):
        fb.feedback_node({}, {}, {}, "agreed", "maybe", 5)
    assert not fb.DB_PATH.exists() or sqlite3.connect(fb.DB_PATH).execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 0


def test_index_failure_is_reported_but_never_loses_the_feedback(monkeypatch):
    monkeypatch.setattr(fb, "_add_to_index", lambda *_a, **_k: False)
    result = _save(7)
    assert result["indexed"] is False and result["feedback_id"] > 0


def test_successful_index_insert_receives_the_claim_and_label(monkeypatch):
    added = []

    class FakeIndex:
        def add_feedback(self, claim, label):
            added.append((claim, label))

    from retrieval import similarity

    monkeypatch.setattr(similarity, "status", lambda: {"ready": True, "documents": 1})
    monkeypatch.setattr(similarity, "get_index", lambda: FakeIndex())
    claim = {"incident_type": "Parked Car", "auto_make": "Audi"}
    assert fb._add_to_index(claim, "N") is True
    assert added == [(claim, "N")]


def test_feedback_is_not_indexed_twice_when_this_call_creates_the_index(monkeypatch):
    """If the index is first built by this very call, its startup loader already
    reads the row we just saved; adding it again would count it twice."""
    added = []

    class FakeIndex:
        def add_feedback(self, claim, label):
            added.append(claim)

    from retrieval import similarity

    monkeypatch.setattr(similarity, "status", lambda: {"ready": False, "documents": None})
    monkeypatch.setattr(similarity, "get_index", lambda: FakeIndex())
    assert fb._add_to_index({"incident_type": "Parked Car"}, "Y") is True
    assert added == []


def test_load_indexable_feedback_returns_every_row_as_claim_and_label():
    _save(1)
    fb.feedback_node({"incident_type": "Vehicle Theft", "auto_make": "BMW"}, {}, {}, "agreed", "N", 2)
    rows = fb.load_indexable_feedback()
    assert len(rows) == 2
    claim, label = rows[1]
    assert claim["incident_type"] == "Vehicle Theft" and label == "N"
