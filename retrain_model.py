"""
retrain_model.py

Manually-triggered, GATED retraining. Combines the original historical
claims (insurance_claims.csv) with confirmed investigator feedback
(data/feedback.db) and refits the fraud model with the original
hyperparameters.

Unlike the first version this never blindly overwrites the live model:

  1. A stratified holdout split is scored (ROC-AUC, precision, recall, F1)
     for the candidate (trained on the train split INCLUDING feedback rows)
     and for a BASELINE (same recipe, same split, original rows only).
  2. The candidate is promoted only if it clears an absolute floor
     (MIN_AUC) and does not regress against the baseline by more than
     MAX_AUC_REGRESSION, i.e. "does the new feedback help or hurt?".
     The model currently in production is scored too but only for
     information: it trained on most of the holdout rows, so its numbers are
     optimistic and must not be used as the bar (doing so would reject every
     retrain).
  3. On promotion the old model is archived to models/archive/ and the new
     one is written atomically (temp file + rename), with a metadata JSON
     beside it. The API loads the model at startup: restart it to pick the
     new model up.

Usage:
    uv run python retrain_model.py             # evaluate + promote if the gate passes
    uv run python retrain_model.py --dry-run   # evaluate only, never writes a model
    uv run python retrain_model.py --force     # promote even if the gate fails
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, UTC
from pathlib import Path

import joblib
import pandas as pd
import sklearn
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split

import config
from agents.fraud_node import ENCODINGS, FEATURE_COLUMNS
from claims_data import load_cleaned_claims

log = logging.getLogger("retrain_model")

CSV_PATH = config.CSV_PATH
FEEDBACK_DB_PATH = config.FEEDBACK_DB_PATH
MODEL_OUT_PATH = config.MODEL_PATH
ARCHIVE_DIR = MODEL_OUT_PATH.parent / "archive"
META_PATH = MODEL_OUT_PATH.with_name("model_meta.json")

# Columns present in the raw CSV but not used as model features — dropped
# during the original training notebook's cleaning step.
DROP_COLUMNS = [
    "policy_number", "insured_zip", "policy_bind_date",
    "incident_date", "incident_location", "insured_hobbies", "_c39",
]

TARGET_COLUMN = "fraud_reported"
POSITIVE_LABEL = "Y"

# Promotion gate.
MIN_AUC = 0.70
MAX_AUC_REGRESSION = 0.03
HOLDOUT_FRACTION = 0.2
RANDOM_STATE = 42

# Winning hyperparameters from the original notebook's RandomizedSearchCV
# (best_params_), refit on the full dataset — reproduced exactly here.
MODEL_PARAMS = dict(
    n_estimators=100,
    max_depth=5,
    min_samples_split=2,
    min_samples_leaf=1,
    max_features=None,
    class_weight="balanced",
    random_state=RANDOM_STATE,
)


def _load_and_clean_original(csv_path: Path) -> pd.DataFrame:
    """The historical claims, cleaned exactly like the original training notebook."""
    df = load_cleaned_claims(csv_path)
    return df.drop(columns=[c for c in DROP_COLUMNS if c in df.columns])


def _load_feedback(db_path: Path) -> pd.DataFrame:
    """Confirmed feedback rows, from Postgres when DATABASE_URL is set, else from ``db_path``."""
    from sqlalchemy import select
    from sqlalchemy.exc import SQLAlchemyError

    from db import engine as db_engine
    from db.schema import feedback

    if not config.DATABASE_URL and not db_path.exists():
        log.info("No feedback DB found at %s — training on original data only.", db_path)
        return pd.DataFrame()

    engine = db_engine.get_engine(db_engine.url_for(db_path))
    try:
        with engine.connect() as conn:
            rows = conn.execute(select(feedback.c.claim_json, feedback.c.confirmed_fraud_reported)).all()
    except SQLAlchemyError:
        rows = []  # table not created yet

    if not rows:
        log.info("Feedback DB has no rows yet — training on original data only.")
        return pd.DataFrame()

    records, skipped = [], 0
    for claim_json, confirmed_label in rows:
        claim = json.loads(claim_json)

        # Fixed ENCODINGS dict: skip any feedback claim with a categorical
        # value the model has never seen, same guard fraud_node applies at inference.
        if any(field in claim and claim[field] not in mapping for field, mapping in ENCODINGS.items()):
            skipped += 1
            continue

        claim[TARGET_COLUMN] = confirmed_label
        records.append(claim)

    if skipped:
        log.warning("Skipped %d feedback row(s) with unrecognized categorical values.", skipped)

    return pd.DataFrame(records)


def build_training_frame(original_df: pd.DataFrame, feedback_df: pd.DataFrame) -> pd.DataFrame:
    """Combine, encode with the fixed ENCODINGS and order columns like the model expects."""
    combined = pd.concat([original_df, feedback_df], ignore_index=True) if len(feedback_df) else original_df.copy()
    for field, mapping in ENCODINGS.items():
        if field in combined.columns:
            combined[field] = combined[field].map(mapping)
    # Feature order must equal the order the model is served with (see FEATURE_COLUMNS).
    return combined[FEATURE_COLUMNS + [TARGET_COLUMN]]


def evaluate(model, X: pd.DataFrame, y: pd.Series) -> dict[str, float]:
    """Holdout metrics for the positive (fraud) class."""
    positive_col = list(model.classes_).index(POSITIVE_LABEL)
    proba = model.predict_proba(X)[:, positive_col]
    pred = model.predict(X)
    return {
        "roc_auc": round(float(roc_auc_score(y == POSITIVE_LABEL, proba)), 4),
        "precision": round(float(precision_score(y, pred, pos_label=POSITIVE_LABEL, zero_division=0)), 4),
        "recall": round(float(recall_score(y, pred, pos_label=POSITIVE_LABEL, zero_division=0)), 4),
        "f1": round(float(f1_score(y, pred, pos_label=POSITIVE_LABEL, zero_division=0)), 4),
    }


def should_promote(candidate: dict[str, float], baseline: dict[str, float] | None) -> tuple[bool, str]:
    """Pure promotion rule (unit-tested): absolute AUC floor + bounded regression
    against a fair baseline (same recipe/split, original rows only)."""
    if candidate["roc_auc"] < MIN_AUC:
        return False, f"candidate AUC {candidate['roc_auc']} is below the floor {MIN_AUC}"
    if baseline is not None and candidate["roc_auc"] < baseline["roc_auc"] - MAX_AUC_REGRESSION:
        return False, (
            f"candidate AUC {candidate['roc_auc']} regresses more than {MAX_AUC_REGRESSION} "
            f"vs the original-data baseline {baseline['roc_auc']}"
        )
    return True, "candidate clears the gate"


def _load_current_model(path: Path):
    try:
        return joblib.load(path) if path.exists() else None
    except Exception:  # noqa: BLE001 - an unreadable current model must not block retraining
        log.warning("Current model at %s could not be loaded; it will be replaced if promoted.", path)
        return None


def _atomic_dump(model, path: Path) -> None:
    """Write to a temp file then rename, so a crash never leaves a half-written model."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".pkl.tmp")
    joblib.dump(model, tmp)
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gated retraining of the fraud model.")
    parser.add_argument("--dry-run", action="store_true", help="evaluate only; never write a model")
    parser.add_argument("--force", action="store_true", help="promote even if the gate fails")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    original_df = _load_and_clean_original(CSV_PATH)
    feedback_df = _load_feedback(FEEDBACK_DB_PATH)
    log.info("Original rows: %d | usable feedback rows: %d", len(original_df), len(feedback_df))

    data = build_training_frame(original_df, feedback_df)
    X, y = data[FEATURE_COLUMNS], data[TARGET_COLUMN]

    X_train, X_hold, y_train, y_hold = train_test_split(
        X, y, test_size=HOLDOUT_FRACTION, stratify=y, random_state=RANDOM_STATE
    )

    candidate_eval_model = RandomForestClassifier(**MODEL_PARAMS).fit(X_train, y_train)
    candidate_metrics = evaluate(candidate_eval_model, X_hold, y_hold)
    log.info("Candidate (original + feedback) holdout metrics: %s", candidate_metrics)

    # Fair baseline: identical recipe and split, but ONLY the original rows
    # (they come first in the combined frame, so their index is < len(original_df)).
    original_mask = X_train.index < len(original_df)
    baseline_model = RandomForestClassifier(**MODEL_PARAMS).fit(X_train[original_mask], y_train[original_mask])
    baseline_metrics = evaluate(baseline_model, X_hold, y_hold)
    log.info("Baseline (original data only) holdout metrics: %s", baseline_metrics)

    # Informational only: the production model trained on most of this holdout.
    current_model = _load_current_model(MODEL_OUT_PATH)
    if current_model is not None:
        log.info(
            "Current production model on the same holdout (optimistic - it saw most of it, "
            "NOT used by the gate): %s",
            evaluate(current_model, X_hold, y_hold),
        )

    ok, reason = should_promote(candidate_metrics, baseline_metrics)
    log.info("Promotion gate: %s (%s)", "PASS" if ok else "FAIL", reason)

    if args.dry_run:
        log.info("--dry-run: no model written.")
        return 0
    if not ok and not args.force:
        log.error("Not promoting. Existing model left untouched (use --force to override).")
        return 1

    # Final model: original recipe, refit on ALL rows (holdout included).
    final_model = RandomForestClassifier(**MODEL_PARAMS).fit(X, y)

    if MODEL_OUT_PATH.exists():
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        archived = ARCHIVE_DIR / f"model_{stamp}.pkl"
        archived.write_bytes(MODEL_OUT_PATH.read_bytes())
        log.info("Archived previous model to %s", archived)

    _atomic_dump(final_model, MODEL_OUT_PATH)
    META_PATH.write_text(
        json.dumps(
            {
                "trained_at": datetime.now(UTC).isoformat(),
                "rows": int(len(data)),
                "feedback_rows": int(len(feedback_df)),
                "holdout_metrics": candidate_metrics,
                "baseline_holdout_metrics": baseline_metrics,
                "sklearn_version": sklearn.__version__,
                "forced": bool(args.force and not ok),
            },
            indent=2,
        )
    )
    log.info("Promoted: model saved to %s. Restart the API to load it.", MODEL_OUT_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
