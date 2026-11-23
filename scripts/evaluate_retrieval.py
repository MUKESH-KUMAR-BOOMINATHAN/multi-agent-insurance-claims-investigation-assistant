"""
scripts/evaluate_retrieval.py

Reproduces the retrieval-quality figures quoted in the README:

    uv run python scripts/evaluate_retrieval.py

For each of the 1,000 historical claims, the fraction of its k nearest OTHER
claims that were confirmed fraud is scored against the claim's own label with
ROC-AUC (0.5 = no better than a coin flip, 1.0 = perfect). It uses the shipping
similarity definition (retrieval/similarity.py) on the historical data only.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
from sklearn.metrics import roc_auc_score  # noqa: E402

from retrieval import similarity  # noqa: E402
from retrieval.retrieval_node import TOP_K, VOTE_K  # noqa: E402


def main() -> int:
    records = similarity.load_historical_records()
    index = similarity.SimilarityIndex(records)  # historical only: no feedback loader
    truth = np.array([r["fraud_reported"] == "Y" for r in records])
    print(f"{len(records)} historical claims, {truth.mean():.1%} fraud")
    print()
    for k, role in ((TOP_K, "shown to the investigator"), (VOTE_K, "used for the fraud-rate statistic")):
        auc = roc_auc_score(truth, index.leave_one_out_fraud_votes(k))
        print(f"k={k:<2d} ({role}): leave-one-out ROC-AUC = {auc:.3f}")
    print()
    print("reference: random = 0.500; the RandomForest scores about 0.83 on a held-out split")
    return 0


if __name__ == "__main__":
    sys.exit(main())
