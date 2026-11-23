"""
retrieval_node.py

LangGraph node: given the claim under investigation, finds the most similar past
claims (see similarity.py) and returns them as structured evidence for the
recommendation step.

Expects graph state to contain a "claim" dict (same shape used by fraud_node)
and returns "similar_claims", "similar_claims_summary" and, if retrieval could
not run, a "warnings" entry. This node never raises: if the historical data
cannot be loaded the pipeline continues without evidence and says so.

Why two numbers: the investigator is SHOWN the 5 closest claims, but the
fraud-rate statistic (and what the recommendation step is told) is computed over
the 15 closest. Measured on the historical data, that vote is a clearly better
predictor with 15 neighbours (ROC-AUC 0.77) than with 5 (0.71).
"""

import logging

from retrieval.similarity import IndexNotBuiltError, get_index

log = logging.getLogger(__name__)

TOP_K = 5  # claims shown to the investigator
VOTE_K = 15  # claims used for the "x of N were confirmed fraud" statistic


def _unavailable(reason: str, detail: str) -> dict:
    return {
        "similar_claims": [],
        "similar_claims_summary": f"Similar-claims evidence unavailable ({reason}).",
        "warnings": [detail],
    }


def retrieval_node(state: dict) -> dict:
    """
    LangGraph node function. Never raises.

    Input state keys used: "claim" (dict of the incoming claim's fields)
    Output keys: "similar_claims" (list of dicts), "similar_claims_summary",
        and "warnings" (only when retrieval was skipped/failed)
    """
    try:
        neighbours = get_index().search(state["claim"], VOTE_K)
    except IndexNotBuiltError as exc:
        log.warning("Retrieval skipped: %s", exc)
        return _unavailable("history not loaded", "Historical claims could not be loaded; no similar-claims evidence.")
    except Exception:  # noqa: BLE001 - any unexpected failure must degrade, not crash the pipeline
        log.exception("Retrieval failed; continuing without similar-claims evidence")
        return _unavailable("retrieval error", "Similar-claims retrieval failed; recommendation uses model score only.")

    similar_claims = [
        {
            "score": round(float(n["score"]), 4),
            "summary": n.get("summary", ""),
            "fraud_reported": n.get("fraud_reported"),
            "incident_type": n.get("incident_type"),
            "incident_severity": n.get("incident_severity"),
            "matched_fields": n.get("matched_fields", []),
        }
        for n in neighbours[:TOP_K]
    ]
    labelled = [n for n in neighbours if n.get("fraud_reported") in ("Y", "N")]
    fraud_count = sum(1 for n in labelled if n["fraud_reported"] == "Y")
    return {
        "similar_claims": similar_claims,
        "similar_claims_summary": (
            f"{fraud_count} of the {len(labelled)} most similar past claims were confirmed fraud."
        ),
    }


if __name__ == "__main__":
    # quick manual smoke test — adjust fields to match a real claim from your dataset
    test_claim = {
        "incident_type": "Multi-vehicle Collision",
        "incident_severity": "Major Damage",
        "collision_type": "Rear Collision",
        "authorities_contacted": "Police",
        "police_report_available": "YES",
        "property_damage": "YES",
        "total_claim_amount": 58000,
        "injury_claim": 6000,
        "property_claim": 10000,
        "vehicle_claim": 42000,
    }
    result = retrieval_node({"claim": test_claim})
    print(result["similar_claims_summary"], result.get("warnings", ""))
    for c in result["similar_claims"]:
        print(f"  {c['score']:.0%}  fraud={c['fraud_reported']}  {c['summary']}  matched: {', '.join(c['matched_fields'])}")
