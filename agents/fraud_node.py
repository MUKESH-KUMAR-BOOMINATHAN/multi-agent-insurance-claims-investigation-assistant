"""
fraud_node.py

First LangGraph node for the multi-agent insurance claims investigation assistant.
Wraps the trained Random Forest fraud-detection model (from the
Random-forest-fraud-detection-model repo) as a callable node.

Place this file in: agents/fraud_node.py
Model file expected at: models/model.pkl (override with MODEL_PATH, see config.py)

Failure behaviour: this node never raises. A missing/corrupt model file, an
unseen category, a missing field or a scoring error each come back as a
non-"ok" status with requires_investigation=True, i.e. manual review.
"""

import logging
from typing import Any, TypedDict

import joblib
import pandas as pd

import config

log = logging.getLogger(__name__)

MODEL_PATH = config.MODEL_PATH


class FraudResult(TypedDict, total=False):
    """Result returned by :func:`fraud_node`."""

    status: str  # ok | unrecognized_category | missing_field | model_unavailable | scoring_error
    fraud_probability: float  # only when status == "ok"
    fraud_signals: list[str]
    requires_investigation: bool
    out_of_range_warnings: list[str]  # only when status == "ok"


def _load_model() -> Any | None:
    """Load the model once at import time. Returns None (and logs) on failure
    so the API still boots and routes every claim to manual review."""
    try:
        return joblib.load(MODEL_PATH)
    except Exception:  # noqa: BLE001 - any load failure must degrade, not crash
        log.exception("Could not load fraud model from %s; claims will go to manual review", MODEL_PATH)
        return None


model = _load_model()

# Encoding maps — copied exactly from the original repo's main.py.
# Must match what LabelEncoder produced during training.
ENCODINGS = {
    "policy_state": {'IL': 0, 'IN': 1, 'OH': 2},
    "policy_csl": {'100/300': 0, '250/500': 1, '500/1000': 2},
    "insured_sex": {'FEMALE': 0, 'MALE': 1},
    "insured_education_level": {'Associate': 0, 'College': 1, 'High School': 2, 'JD': 3, 'MD': 4, 'Masters': 5, 'PhD': 6},
    "insured_occupation": {'adm-clerical': 0, 'armed-forces': 1, 'craft-repair': 2, 'exec-managerial': 3, 'farming-fishing': 4, 'handlers-cleaners': 5, 'machine-op-inspct': 6, 'other-service': 7, 'priv-house-serv': 8, 'prof-specialty': 9, 'protective-serv': 10, 'sales': 11, 'tech-support': 12, 'transport-moving': 13},
    "insured_relationship": {'husband': 0, 'not-in-family': 1, 'other-relative': 2, 'own-child': 3, 'unmarried': 4, 'wife': 5},
    "incident_type": {'Multi-vehicle Collision': 0, 'Parked Car': 1, 'Single Vehicle Collision': 2, 'Vehicle Theft': 3},
    "collision_type": {'Front Collision': 0, 'Not Applicable': 1, 'Rear Collision': 2, 'Side Collision': 3},
    "incident_severity": {'Major Damage': 0, 'Minor Damage': 1, 'Total Loss': 2, 'Trivial Damage': 3},
    "authorities_contacted": {'Ambulance': 0, 'Fire': 1, 'None': 2, 'Other': 3, 'Police': 4},
    "incident_state": {'NC': 0, 'NY': 1, 'OH': 2, 'PA': 3, 'SC': 4, 'VA': 5, 'WV': 6},
    "incident_city": {'Arlington': 0, 'Columbus': 1, 'Hillsdale': 2, 'Northbend': 3, 'Northbrook': 4, 'Riverwood': 5, 'Springfield': 6},
    "property_damage": {'NO': 0, 'Unknown': 1, 'YES': 2},
    "police_report_available": {'NO': 0, 'Unknown': 1, 'YES': 2},
    "auto_make": {'Accura': 0, 'Audi': 1, 'BMW': 2, 'Chevrolet': 3, 'Dodge': 4, 'Ford': 5, 'Honda': 6, 'Jeep': 7, 'Mercedes': 8, 'Nissan': 9, 'Saab': 10, 'Suburu': 11, 'Toyota': 12, 'Volkswagen': 13},
    "auto_model": {'3 Series': 0, '92x': 1, '93': 2, '95': 3, 'A3': 4, 'A5': 5, 'Accord': 6, 'C300': 7, 'CRV': 8, 'Camry': 9, 'Civic': 10, 'Corolla': 11, 'E400': 12, 'Escape': 13, 'F150': 14, 'Forrestor': 15, 'Fusion': 16, 'Grand Cherokee': 17, 'Highlander': 18, 'Impreza': 19, 'Jetta': 20, 'Legacy': 21, 'M5': 22, 'MDX': 23, 'ML350': 24, 'Malibu': 25, 'Maxima': 26, 'Neon': 27, 'Passat': 28, 'Pathfinder': 29, 'RAM': 30, 'RSX': 31, 'Silverado': 32, 'TL': 33, 'Tahoe': 34, 'Ultima': 35, 'Wrangler': 36, 'X5': 37, 'X6': 38},
}

# Exact feature order the model was trained with (== model.feature_names_in_).
# scikit-learn rejects a DataFrame whose columns are in a different order, so
# the inference row is always re-indexed to this list (the API's field order
# or a feedback row's key order must never matter).
FEATURE_COLUMNS = [
    "months_as_customer", "age", "policy_state", "policy_csl", "policy_deductable",
    "policy_annual_premium", "umbrella_limit", "insured_sex", "insured_education_level",
    "insured_occupation", "insured_relationship", "capital-gains", "capital-loss",
    "incident_type", "collision_type", "incident_severity", "authorities_contacted",
    "incident_state", "incident_city", "incident_hour_of_the_day",
    "number_of_vehicles_involved", "property_damage", "bodily_injuries", "witnesses",
    "police_report_available", "total_claim_amount", "injury_claim", "property_claim",
    "vehicle_claim", "auto_make", "auto_model", "auto_year",
]

# Min/max for key numeric fields, taken from the training CSV
# (data/insurance_claims.csv) with a small margin. Values outside the range
# mean the model is extrapolating, so the claim is forced to human review.
NUMERIC_RANGES = {
    "months_as_customer": (0, 480),
    "age": (18, 65),
    "policy_annual_premium": (400, 2100),
    "total_claim_amount": (0, 115000),
    "injury_claim": (0, 22000),
    "property_claim": (0, 24000),
    "vehicle_claim": (0, 80000),
    "auto_year": (1995, 2016),
}

# Threshold above which a claim is flagged for human investigation.
INVESTIGATION_THRESHOLD = 0.5


class UnrecognizedCategoryError(Exception):
    """Raised when a claim contains a categorical value the model was never trained on."""
    def __init__(self, field: str, value: str, allowed: list[str]):
        self.field = field
        self.value = value
        self.allowed = allowed
        super().__init__(
            f"Unrecognized value '{value}' for '{field}'. Allowed: {allowed}"
        )


def _encode_claim(claim: dict) -> pd.DataFrame:
    """Encode a raw claim dict into the numeric row format the model expects.

    Raises UnrecognizedCategoryError if a categorical field has a value the
    model has never seen during training — rather than silently guessing.
    """
    data = dict(claim)  # shallow copy so we don't mutate the caller's dict

    for field, mapping in ENCODINGS.items():
        if field not in data:
            raise KeyError(f"Missing required field: '{field}'")
        value = data[field]
        if not isinstance(value, str) or value not in mapping:  # unhashable/odd types too
            raise UnrecognizedCategoryError(field, str(value), list(mapping.keys()))
        data[field] = mapping[value]

    # Re-index to the training order (see FEATURE_COLUMNS); extra keys are dropped.
    return pd.DataFrame([data])[FEATURE_COLUMNS]


def _check_out_of_range(claim: dict) -> list[str]:
    """Flag numeric fields that fall outside the range seen during training.
    The model can still produce a number for these, but it's extrapolating
    beyond what it learned, so the prediction should be treated with less
    confidence.
    """
    warnings = []
    for field, (low, high) in NUMERIC_RANGES.items():
        value = claim.get(field)
        if value is not None and not (low <= value <= high):
            warnings.append(
                f"'{field}' value {value} is outside the training range ({low}-{high}) — "
                f"prediction confidence may be reduced"
            )
    return warnings


def _build_signals(claim: dict, fraud_probability: float) -> list[str]:
    """Produce a short, human-readable list of signals driving the score."""
    signals = []

    if claim.get("incident_severity") == "Major Damage":
        signals.append("Major damage severity — historically 60%+ fraud rate")

    if claim.get("incident_type") in ("Multi-vehicle Collision", "Single Vehicle Collision"):
        signals.append(f"Incident type is a collision ({claim['incident_type']})")

    total_claim = claim.get("total_claim_amount")
    if total_claim is not None and total_claim > 50000:
        signals.append(f"High total claim amount (${total_claim:,})")

    if not signals:
        signals.append("No strong individual risk signals — probability driven by combined features")

    return signals


def fraud_node(claim: dict) -> FraudResult:
    """
    LangGraph-compatible node function.

    Args:
        claim: dict of raw claim fields, matching the ClaimData schema
               from the original FastAPI service (human-readable strings
               for categorical fields, e.g. incident_severity="Major Damage").

    Returns:
        dict with:
          - status: "ok" | "unrecognized_category" | "missing_field"
                    | "model_unavailable" | "scoring_error"
          - fraud_probability (float, only present if status == "ok")
          - fraud_signals (list[str])
          - requires_investigation (bool) — True whenever the claim can't
            be confidently auto-scored, in addition to high-probability cases
          - out_of_range_warnings (list[str]) — numeric fields outside the
            training distribution, only present if status == "ok"
    """
    if model is None:
        return {
            "status": "model_unavailable",
            "fraud_signals": ["Cannot auto-score: fraud model is unavailable. Routed to manual review."],
            "requires_investigation": True,
        }

    try:
        input_df = _encode_claim(claim)
    except UnrecognizedCategoryError as e:
        # Deliberately do NOT echo the client-supplied value: this string is
        # later placed in an LLM prompt, so it must contain only our own text.
        return {
            "status": "unrecognized_category",
            "fraud_signals": [
                f"Cannot auto-score: '{e.field}' has a value that was never seen during "
                f"training. Routed to manual review."
            ],
            "requires_investigation": True,
        }
    except KeyError as e:
        return {
            "status": "missing_field",
            "fraud_signals": [f"Cannot auto-score: {e}. Routed to manual review."],
            "requires_investigation": True,
        }

    try:
        fraud_probability = float(model.predict_proba(input_df)[0][1])
        fraud_signals = _build_signals(claim, fraud_probability)
        out_of_range_warnings = _check_out_of_range(claim)
    except Exception:  # noqa: BLE001 - e.g. sklearn/pandas version mismatch; never crash the graph
        log.exception("Fraud model scoring failed; routing claim to manual review")
        return {
            "status": "scoring_error",
            "fraud_signals": ["Cannot auto-score: the fraud model failed to run. Routed to manual review."],
            "requires_investigation": True,
        }

    requires_investigation = fraud_probability >= INVESTIGATION_THRESHOLD

    if out_of_range_warnings:
        # Out-of-range numeric inputs mean we should route to a human even if
        # the raw probability looks low, since the model is extrapolating.
        requires_investigation = True

    return {
        "status": "ok",
        "fraud_probability": round(fraud_probability, 3),
        "fraud_signals": fraud_signals,
        "requires_investigation": requires_investigation,
        "out_of_range_warnings": out_of_range_warnings,
    }
