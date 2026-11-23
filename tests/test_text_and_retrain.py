"""The retraining promotion gate and the shared data cleaning."""

import pandas as pd

import retrain_model
from agents.fraud_node import ENCODINGS, FEATURE_COLUMNS
from retrain_model import build_training_frame, should_promote


def test_gate_rejects_a_model_below_the_absolute_floor():
    ok, reason = should_promote({"roc_auc": 0.60}, None)
    assert not ok and "floor" in reason


def test_gate_rejects_a_meaningful_regression_but_tolerates_noise():
    current = {"roc_auc": 0.90}
    assert should_promote({"roc_auc": 0.80}, current)[0] is False
    assert should_promote({"roc_auc": 0.88}, current)[0] is True


def test_gate_accepts_first_model_that_clears_the_floor():
    assert should_promote({"roc_auc": 0.75}, None)[0] is True


def test_training_frame_has_the_serving_column_order_regardless_of_input_order():
    original = pd.DataFrame(
        [{**{c: ("OH" if c == "policy_state" else 0) for c in FEATURE_COLUMNS}, "fraud_reported": "N"}]
    )
    for field, mapping in ENCODINGS.items():
        original[field] = next(iter(mapping))
    shuffled = original[list(reversed(original.columns))]
    frame = build_training_frame(shuffled, pd.DataFrame())
    assert list(frame.columns) == FEATURE_COLUMNS + ["fraud_reported"]


def test_dry_run_never_writes_a_model(tmp_path, monkeypatch):
    monkeypatch.setattr(retrain_model, "MODEL_OUT_PATH", tmp_path / "model.pkl")
    monkeypatch.setattr(retrain_model, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(retrain_model, "META_PATH", tmp_path / "meta.json")
    monkeypatch.setattr(retrain_model, "FEEDBACK_DB_PATH", tmp_path / "none.db")
    assert retrain_model.main(["--dry-run"]) == 0
    assert list(tmp_path.iterdir()) == []


def test_load_cleaned_claims_replaces_the_datasets_missing_markers(tmp_path):
    from claims_data import load_cleaned_claims

    csv = tmp_path / "c.csv"
    csv.write_text(
        "collision_type,property_damage,police_report_available,authorities_contacted\n"
        "?,?,?,\n"
        "Side Collision,YES,NO,Police\n",
        encoding="utf-8",
    )
    first = load_cleaned_claims(csv).iloc[0]
    assert (first.collision_type, first.property_damage, first.police_report_available, first.authorities_contacted) == (
        "Not Applicable", "Unknown", "Unknown", "None",
    )
