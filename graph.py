"""
graph.py

LangGraph orchestration for the multi-agent insurance claims investigation
assistant. Wires together:

    fraud_node           -> scores the claim, flags out-of-range / unrecognized inputs
    retrieval_node       -> (skipped if fraud_node couldn't score the claim)
                            pulls similar historical claims as evidence
    recommendation_node  -> Groq turns fraud score + similar-claims
                            evidence into a structured investigator recommendation,
                            or a deterministic rules fallback if Groq is unavailable

Failure policy: every step is wrapped so an unexpected exception degrades that
step (and appends a human-readable entry to ``warnings``) instead of aborting
the graph. A claim submitted to this graph always comes back with a
recommendation.
"""

import logging
import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, StateGraph

from agents.fraud_node import fraud_node
from agents.recommendation_node import recommendation_node as _recommendation_node_fn
from agents.recommendation_node import rules_recommendation
from retrieval.retrieval_node import retrieval_node as _retrieval_node_fn

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared graph state
# ---------------------------------------------------------------------------
class GraphState(TypedDict, total=False):
    claim: dict
    # False = do not call the LLM for this claim (rules-based recommendation instead).
    use_llm: bool
    fraud_result: dict
    similar_claims: list
    similar_claims_summary: str
    recommendation: dict
    # Reducer: every node's warnings are appended, never overwritten.
    warnings: Annotated[list[str], operator.add]


# ---------------------------------------------------------------------------
# Node wrappers — adapt each node's own signature to (state) -> partial update
# ---------------------------------------------------------------------------
def fraud_node_step(state: GraphState) -> dict:
    """Score the claim; on any unexpected error route it to manual review."""
    try:
        return {"fraud_result": fraud_node(state["claim"])}
    except Exception:  # noqa: BLE001 - the pipeline must always finish
        log.exception("fraud_node crashed; routing claim to manual review")
        return {
            "fraud_result": {
                "status": "scoring_error",
                "fraud_signals": ["Cannot auto-score: unexpected scoring error. Routed to manual review."],
                "requires_investigation": True,
            },
            "warnings": ["Fraud scoring failed unexpectedly; claim routed to manual review."],
        }


def retrieval_node_step(state: GraphState) -> dict:
    """Retrieve similar claims. retrieval_node already degrades internally;
    the outer guard is a belt-and-braces net for anything it didn't foresee."""
    try:
        updated = _retrieval_node_fn(state)
    except Exception:  # noqa: BLE001
        log.exception("retrieval_node crashed; continuing without evidence")
        return {
            "similar_claims": [],
            "similar_claims_summary": "Similar-claims evidence unavailable (retrieval error).",
            "warnings": ["Similar-claims retrieval failed; recommendation uses model score only."],
        }
    return {
        "similar_claims": updated.get("similar_claims", []),
        "similar_claims_summary": updated.get("similar_claims_summary", ""),
        "warnings": updated.get("warnings", []),
    }


def recommendation_node_step(state: GraphState) -> dict:
    """Produce the recommendation and surface when it was NOT written by Groq."""
    try:
        recommendation = _recommendation_node_fn(dict(state)).get("recommendation", {})
    except Exception:  # noqa: BLE001 - recommendation_node never raises; this is a last resort
        log.exception("recommendation_node crashed; using rules fallback")
        recommendation = rules_recommendation(dict(state), "unexpected_error")

    warnings: list[str] = []
    if recommendation.get("fallback_reason") == "demo_ai_limit":
        warnings.append(
            "The demo's shared daily AI allowance is used up, so a rules-based recommendation was used."
        )
    elif recommendation.get("source") == "rules_fallback":
        warnings.append(
            "AI recommendation unavailable "
            f"({recommendation.get('fallback_reason', 'unknown')}); a rules-based recommendation was used."
        )
    return {"recommendation": recommendation, "warnings": warnings}


# ---------------------------------------------------------------------------
# Routing: only bother retrieving similar claims if fraud_node could
# actually score this one. If the claim had an unrecognized category or a
# missing field, it's already routed to manual review — retrieval adds
# nothing there.
# ---------------------------------------------------------------------------
def route_after_fraud(state: GraphState) -> str:
    if state.get("fraud_result", {}).get("status") == "ok":
        return "retrieve"
    return "skip_retrieval"


# ---------------------------------------------------------------------------
# Build the graph
# ---------------------------------------------------------------------------
def build_graph():
    graph = StateGraph(GraphState)

    graph.add_node("fraud_node", fraud_node_step)
    graph.add_node("retrieval_node", retrieval_node_step)
    graph.add_node("recommendation_node", recommendation_node_step)

    graph.set_entry_point("fraud_node")

    graph.add_conditional_edges(
        "fraud_node",
        route_after_fraud,
        {
            "retrieve": "retrieval_node",
            "skip_retrieval": "recommendation_node",
        },
    )
    graph.add_edge("retrieval_node", "recommendation_node")
    graph.add_edge("recommendation_node", END)

    return graph.compile()


if __name__ == "__main__":
    import json as _json

    app = build_graph()

    test_claim = {
        "months_as_customer": 328,
        "age": 48,
        "policy_state": "OH",
        "policy_csl": "250/500",
        "policy_deductable": 1000,
        "policy_annual_premium": 1406.91,
        "umbrella_limit": 0,
        "insured_sex": "MALE",
        "insured_education_level": "MD",
        "insured_occupation": "craft-repair",
        "insured_relationship": "husband",
        "capital-gains": 53300,
        "capital-loss": 0,
        "incident_type": "Single Vehicle Collision",
        "collision_type": "Side Collision",
        "incident_severity": "Major Damage",
        "authorities_contacted": "Police",
        "incident_state": "SC",
        "incident_city": "Columbus",
        "incident_hour_of_the_day": 5,
        "number_of_vehicles_involved": 1,
        "property_damage": "YES",
        "bodily_injuries": 1,
        "witnesses": 2,
        "police_report_available": "YES",
        "total_claim_amount": 71610,
        "injury_claim": 6510,
        "property_claim": 13020,
        "vehicle_claim": 52080,
        "auto_make": "Saab",
        "auto_model": "92x",
        "auto_year": 2004,
    }

    final_state = app.invoke({"claim": test_claim})

    print("fraud_result:", final_state.get("fraud_result"))
    print("similar_claims_summary:", final_state.get("similar_claims_summary"))
    for c in final_state.get("similar_claims", []):
        print(f"  score={c['score']}  fraud={c['fraud_reported']}  {c['summary'][:100]}...")
    print("warnings:", final_state.get("warnings"))
    print("\nrecommendation:")
    print(_json.dumps(final_state.get("recommendation", {}), indent=2))
