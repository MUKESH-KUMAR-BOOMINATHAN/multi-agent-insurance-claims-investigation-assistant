"""
claims_data.py

The one place the historical claims CSV is loaded and cleaned, shared by the
retrieval index (retrieval/similarity.py) and retraining (retrain_model.py) so
both see exactly the same values the fraud model was trained on.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_cleaned_claims(csv_path: Path) -> pd.DataFrame:
    """Read the historical claims and apply the original training notebook's cleaning.

    The dataset marks missing categorical values with '?' (or leaves them blank);
    the model was trained with these replaced by explicit categories, so inference,
    retrieval and retraining must too.
    """
    df = pd.read_csv(csv_path)
    df["collision_type"] = df["collision_type"].replace("?", "Not Applicable")
    df["property_damage"] = df["property_damage"].replace("?", "Unknown")
    df["police_report_available"] = df["police_report_available"].replace("?", "Unknown")
    df["authorities_contacted"] = df["authorities_contacted"].fillna("None")
    return df
