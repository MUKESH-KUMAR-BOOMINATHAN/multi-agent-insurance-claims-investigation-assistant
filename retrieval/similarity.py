"""
retrieval/similarity.py

"Similar past claims" for the investigator, using an interpretable mixed-type
(Gower) distance over the facts of the incident and the size of the claim.

Why this and not text embeddings
  A first version embedded each claim as text (bge-small). Measured with a
  leave-one-out check on the 1,000 historical claims, the fraud vote of a
  claim's 5 nearest neighbours reached ROC-AUC 0.56 (0.5 = coin flip):
  embeddings find claims that READ alike, not claims that behave alike, and they
  handle numbers poorly. This structured distance reaches 0.71 at 5 neighbours
  and 0.77 at 15 (see scripts/evaluate_retrieval.py, reproducible). It is also
  explainable (we can say which fields matched), needs no model download or
  ONNX runtime, and builds in milliseconds, so it fits a small web instance.

Distance = average over the fields of:
  * categorical field: 0 if equal else 1
  * numeric claim amount: |difference| / (max - min of the historical data), capped at 1
Similarity shown to the user = 1 - distance, in [0, 1].

Two sources of claims:
  * HISTORICAL: data/insurance_claims.csv, loaded once at startup.
  * FEEDBACK: investigator-confirmed outcomes read from the feedback database at
    startup and appended live. The index is a derived cache of SQLite: losing it
    (or an ephemeral host disk) never loses data and there is no second store.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

import numpy as np

import config
from claims_data import load_cleaned_claims

log = logging.getLogger(__name__)

# Fields that describe WHAT happened (categorical) and HOW BIG the claim is (numeric).
CATEGORICAL_FIELDS: tuple[str, ...] = (
    "incident_severity",
    "incident_type",
    "collision_type",
    "authorities_contacted",
    "police_report_available",
    "property_damage",
)
NUMERIC_FIELDS: tuple[str, ...] = ("total_claim_amount", "injury_claim", "property_claim", "vehicle_claim")

# Human-readable names for "why is this claim similar".
_FIELD_LABELS = {
    "incident_severity": "severity",
    "incident_type": "incident type",
    "collision_type": "collision type",
    "authorities_contacted": "authorities contacted",
    "police_report_available": "police report",
    "property_damage": "property damage",
}
_SIMILAR_SIZE_THRESHOLD = 0.10  # total claim within 10% of the dataset's range counts as "similar size"

Record = dict[str, Any]
FeedbackLoader = Callable[[], list[tuple[dict[str, Any], str]]]


class IndexNotBuiltError(RuntimeError):
    """The historical claims could not be loaded (missing or unreadable CSV)."""


def _to_float(value: Any) -> float:
    """float(value), or NaN for anything missing or non-numeric (never raises)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _summary(claim: dict[str, Any]) -> str:
    """One short line describing a past claim for the UI."""
    parts = [str(claim.get("incident_type") or "?"), str(claim.get("incident_severity") or "?")]
    total = _to_float(claim.get("total_claim_amount"))
    if total == total:  # not NaN
        parts.append(f"${total:,.0f} total claim")
    return " · ".join(parts)


class SimilarityIndex:
    """Exact nearest-neighbour search over historical + feedback claims."""

    def __init__(self, historical: list[Record], feedback_loader: FeedbackLoader | None = None) -> None:
        if not historical:
            raise IndexNotBuiltError("No historical claims to index.")
        self._lock = threading.RLock()
        self._records: list[Record] = []  # one dict per claim: the fields above + label + source
        self._codes: dict[str, dict[str, int]] = {f: {} for f in CATEGORICAL_FIELDS}
        self._cat = np.empty((0, len(CATEGORICAL_FIELDS)), dtype=np.int32)
        self._num = np.empty((0, len(NUMERIC_FIELDS)), dtype=np.float64)

        for rec in historical:
            self._append(rec)
        # Range of each numeric field in the HISTORICAL data (stable: feedback never shifts it).
        span = self._num.max(axis=0) - self._num.min(axis=0)
        self._range = np.where(span > 0, span, 1.0)
        self._historical_count = len(self._records)
        if feedback_loader is not None:
            self._load_feedback(feedback_loader)

    # -- building --------------------------------------------------------------
    def _encode(self, claim: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
        """Integer codes for the categorical fields (-1 = missing), floats for numeric (NaN = missing)."""
        cat = np.full(len(CATEGORICAL_FIELDS), -1, dtype=np.int32)
        for i, field in enumerate(CATEGORICAL_FIELDS):
            value = claim.get(field)
            if value is not None and value == value:  # not None / NaN
                cat[i] = self._codes[field].setdefault(str(value), len(self._codes[field]))
        num = np.array([_to_float(claim.get(f)) for f in NUMERIC_FIELDS], dtype=np.float64)
        return cat, num

    def _append(self, record: Record) -> None:
        cat, num = self._encode(record)
        self._records.append(record)
        self._cat = np.vstack([self._cat, cat])
        self._num = np.vstack([self._num, num])

    def _load_feedback(self, loader: FeedbackLoader) -> None:
        """Load every stored feedback claim. Failure degrades to historical-only."""
        try:
            rows = loader()
            for claim, label in rows:
                self._append({**claim, "fraud_reported": label, "source": "investigator_feedback"})
            if rows:
                log.info("Loaded %d feedback claims into the similarity index", len(rows))
        except Exception:  # noqa: BLE001 - feedback is an enhancement, never a blocker
            log.exception("Could not load feedback into the similarity index; using historical claims only")

    # -- public API --------------------------------------------------------------
    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def add_feedback(self, claim: dict[str, Any], label: str) -> None:
        """Make an investigator-confirmed claim searchable immediately."""
        with self._lock:
            self._append({**claim, "fraud_reported": label, "source": "investigator_feedback"})

    def leave_one_out_fraud_votes(self, k: int) -> np.ndarray:
        """For every HISTORICAL claim: the fraction of its ``k`` nearest OTHER claims that
        were confirmed fraud. Used only by scripts/evaluate_retrieval.py to measure how
        informative retrieval is; a claim never counts itself as a neighbour."""
        with self._lock:
            n = self._historical_count
            cat, num = self._cat[:n], self._num[:n]
            fraud = np.array([r.get("fraud_reported") == "Y" for r in self._records[:n]], dtype=np.float64)
        cat_cost = ((cat[:, None, :] != cat[None, :, :]) | (cat[:, None, :] < 0) | (cat[None, :, :] < 0)).sum(axis=2)
        with np.errstate(invalid="ignore"):
            num_cost = np.minimum(np.abs(num[:, None, :] - num[None, :, :]) / self._range, 1.0)
        distance = (cat_cost + np.nan_to_num(num_cost, nan=1.0).sum(axis=2)) / (len(CATEGORICAL_FIELDS) + len(NUMERIC_FIELDS))
        np.fill_diagonal(distance, np.inf)  # a claim must not retrieve itself
        k = max(1, min(k, n - 1))
        neighbours = np.argpartition(distance, k - 1, axis=1)[:, :k]
        return fraud[neighbours].mean(axis=1)

    def search(self, claim: dict[str, Any], k: int = 5) -> list[dict[str, Any]]:
        """The ``k`` most similar claims, best first, with ``score`` in [0, 1]."""
        with self._lock:
            q_cat, q_num = self._encode(claim)
            cat, num, records = self._cat, self._num, self._records  # arrays are replaced, not mutated

        # Categorical: a mismatch (or any missing value) costs 1.
        cat_cost = ((cat != q_cat) | (cat < 0) | (q_cat < 0)).astype(np.float64)
        # Numeric: normalised absolute difference, capped at 1; a missing value costs 1.
        with np.errstate(invalid="ignore"):
            num_cost = np.minimum(np.abs(num - q_num) / self._range, 1.0)
        num_cost = np.where(np.isnan(num_cost), 1.0, num_cost)

        distance = (cat_cost.sum(axis=1) + num_cost.sum(axis=1)) / (len(CATEGORICAL_FIELDS) + len(NUMERIC_FIELDS))
        k = max(1, min(k, len(distance)))
        order = np.argsort(distance, kind="stable")[:k]  # stable: ties keep dataset order (deterministic)

        hits = []
        for i in order:
            matched = [_FIELD_LABELS[f] for j, f in enumerate(CATEGORICAL_FIELDS) if cat_cost[i, j] == 0.0]
            if num_cost[i, 0] <= _SIMILAR_SIZE_THRESHOLD:
                matched.append("claim size")
            rec = records[int(i)]
            hits.append(
                {
                    "score": float(1.0 - distance[i]),
                    "summary": _summary(rec),
                    "fraud_reported": rec.get("fraud_reported"),
                    "incident_type": rec.get("incident_type"),
                    "incident_severity": rec.get("incident_severity"),
                    "matched_fields": matched,
                    "source": rec.get("source", "historical"),
                }
            )
        return hits


# ---------------------------------------------------------------------------
# Loading + process-wide singleton
# ---------------------------------------------------------------------------
def load_historical_records() -> list[Record]:
    """Historical claims as dicts (cleaned like the training data)."""
    path = config.CSV_PATH
    try:
        df = load_cleaned_claims(path)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise IndexNotBuiltError(f"Could not load the historical claims from {path} ({type(exc).__name__}).") from exc
    keep = [c for c in (*CATEGORICAL_FIELDS, *NUMERIC_FIELDS, "fraud_reported") if c in df.columns]
    return df[keep].to_dict(orient="records")


def _default_feedback_loader() -> list[tuple[dict[str, Any], str]]:
    from agents.feedback_node import load_indexable_feedback  # lazy: avoids an import cycle

    return load_indexable_feedback()


_index: SimilarityIndex | None = None
_index_lock = threading.Lock()


def get_index() -> SimilarityIndex:
    """Return the process-wide index, building it on first use (thread-safe).

    Raises IndexNotBuiltError if the historical claims cannot be loaded.
    """
    global _index
    if _index is not None:
        return _index
    with _index_lock:
        if _index is None:
            _index = SimilarityIndex(load_historical_records(), feedback_loader=_default_feedback_loader)
        return _index


def reset_index() -> None:
    """Forget the singleton (tests)."""
    global _index
    with _index_lock:
        _index = None


def warm() -> None:
    """Build the index now so the first request does not pay for it."""
    get_index()


def status() -> dict[str, Any]:
    """Non-secret readiness info for /health (never triggers a build)."""
    return {"ready": _index is not None, "documents": len(_index) if _index is not None else None}
