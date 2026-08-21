"""
retrain_model.py

Manually-triggered retraining script. Combines the original historical
claims (insurance_claims.csv) with confirmed investigator feedback
(data/feedback.db) and refits the fraud model using the exact same
preprocessing and hyperparameters as the original training notebook.

Run manually when you've decided (e.g. via drift monitoring — MLflow is the
natural next step here, not built yet) that it's worth retraining:

    uv run python retrain_model.py

Overwrites models/model.pkl. No versioning/validation gate yet — that's
future work once drift detection (MLflow) is in place.
"""

import json
import sqlite3
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

from agents.fraud_node import ENCODINGS

CSV_PATH = "data/insurance_claims.csv"
FEEDBACK_DB_PATH = Path("data/feedback.db")
MODEL_OUT_PATH = Path("models/model.pkl")

# Columns present in the raw CSV but not used as model features — dropped
# during the original training notebook's cleaning step.
DROP_COLUMNS = [
    "policy_number", "insured_zip", "policy_bind_date",
    "incident_date", "incident_location", "insured_hobbies", "_c39",
]

TARGET_COLUMN = "fraud_reported"

# Winning hyperparameters from the original notebook's RandomizedSearchCV
# (best_params_), refit on the full dataset — reproduced exactly here.
MODEL_PARAMS = dict(
    n_estimators=100,
    max_depth=5,
    min_samples_split=2,
    min_samples_leaf=1,
    max_features=None,
    class_weight="balanced",
    random_state=42,
)


def _load_and_clean_original(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)

    # Same cleaning steps as the original notebook, in the same order.
    df["collision_type"] = df["collision_type"].replace("?", "Not Applicable")
    df["property_damage"] = df["property_damage"].replace("?", "Unknown")
    df["police_report_available"] = df["police_report_available"].replace("?", "Unknown")
    df["authorities_contacted"] = df["authorities_contacted"].fillna("None")

    df = df.drop(columns=[c for c in DROP_COLUMNS if c in df.columns])
    return df


def _load_feedback(db_path: Path) -> pd.DataFrame:
    if not db_path.exists():
        print(f"No feedback DB found at {db_path} — training on original data only.")
        return pd.DataFrame()

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT claim_json, confirmed_fraud_reported FROM feedback").fetchall()
    conn.close()

    if not rows:
        print("Feedback DB exists but has no rows yet — training on original data only.")
        return pd.DataFrame()

    records = []
    skipped = 0
    for claim_json, confirmed_label in rows:
        claim = json.loads(claim_json)

        # Fixed ENCODINGS dict (per project decision) — skip any feedback
        # claim containing a categorical value the model has never seen,
        # same guard fraud_node.py applies at inference time.
        unrecognized = False
        for field, mapping in ENCODINGS.items():
            if field in claim and claim[field] not in mapping:
                unrecognized = True
                break
        if unrecognized:
            skipped += 1
            continue

        claim[TARGET_COLUMN] = confirmed_label
        records.append(claim)

    if skipped:
        print(f"Skipped {skipped} feedback row(s) with unrecognized categorical values.")

    return pd.DataFrame(records)


def main():
    print(f"Loading and cleaning {CSV_PATH} ...")
    original_df = _load_and_clean_original(CSV_PATH)
    print(f"Original data: {len(original_df)} rows.")

    feedback_df = _load_feedback(FEEDBACK_DB_PATH)
    print(f"Feedback data: {len(feedback_df)} usable rows.")

    combined_df = pd.concat([original_df, feedback_df], ignore_index=True) if len(feedback_df) else original_df
    print(f"Combined training set: {len(combined_df)} rows.")

    # Encode every categorical field with the fixed ENCODINGS dict — must
    # match what fraud_node.py uses at inference time exactly.
    for field, mapping in ENCODINGS.items():
        if field in combined_df.columns:
            combined_df[field] = combined_df[field].map(mapping)

    X = combined_df.drop(columns=[TARGET_COLUMN])
    y = combined_df[TARGET_COLUMN]

    print("Training RandomForestClassifier with original best hyperparameters...")
    model = RandomForestClassifier(**MODEL_PARAMS)
    model.fit(X, y)

    MODEL_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_OUT_PATH)
    print(f"\nDone. Model retrained on {len(combined_df)} rows and saved to {MODEL_OUT_PATH}")
    print("Note: this overwrites the previous model with no validation gate — "
          "add MLflow-based drift/versioning before relying on this in production.")


if __name__ == "__main__":
    main()