"""
graph.py

LangGraph orchestration for the multi-agent insurance claims investigation
assistant. Wires together:

    fraud_node           -> scores the claim, flags out-of-range / unrecognized inputs
    retrieval_node       -> (skipped if fraud_node couldn't score the claim)
                            pulls similar historical claims as evidence
    recommendation_node  -> local llama3.1 turns fraud score + similar-claims
                            evidence into a structured investigator recommendation

Place this file in the project root (matches: agents/fraud_node.py,
agents/recommendation_node.py, retrieval/retrieval_node.py).
"""

from typing import TypedDict, Optional

from langgraph.graph import StateGraph, END

from agents.fraud_node import fraud_node
from agents.recommendation_node import recommendation_node as _recommendation_node_fn
from retrieval.retrieval_node import retrieval_node as _retrieval_node_fn


# ---------------------------------------------------------------------------
# Shared graph state
# ---------------------------------------------------------------------------
class GraphState(TypedDict, total=False):
    claim: dict
    fraud_result: dict
    similar_claims: list
    similar_claims_summary: str
    recommendation: dict


# ---------------------------------------------------------------------------
# Node wrappers — adapt each node's own function signature to (state) -> dict
# ---------------------------------------------------------------------------
def fraud_node_step(state: GraphState) -> dict:
    """fraud_node takes a bare claim dict and returns its own result dict —
    wrap it so it reads/writes through shared graph state."""
    result = fraud_node(state["claim"])
    return {"fraud_result": result}


def retrieval_node_step(state: GraphState) -> dict:
    """retrieval_node already speaks `state` natively (reads state["claim"],
    writes similar_claims / similar_claims_summary), so just delegate."""
    updated = _retrieval_node_fn(state)
    return {
        "similar_claims": updated.get("similar_claims", []),
        "similar_claims_summary": updated.get("similar_claims_summary", ""),
    }


def recommendation_node_step(state: GraphState) -> dict:
    """recommendation_node also speaks `state` natively (reads fraud_result /
    similar_claims, writes recommendation), so just delegate."""
    updated = _recommendation_node_fn(state)
    return {"recommendation": updated.get("recommendation", {})}


# ---------------------------------------------------------------------------
# Routing: only bother retrieving similar claims if fraud_node could
# actually score this one. If the claim had an unrecognized category or a
# missing field, it's already routed to manual review — retrieval adds
# nothing there.
# ---------------------------------------------------------------------------
def route_after_fraud(state: GraphState) -> str:
    if state["fraud_result"].get("status") == "ok":
        return "retrieve"
    return "skip_retrieval"


# retrieval_node writes similar_claims_summary; if that key is missing (the
# skip_retrieval path), recommendation_node still runs but with no similar-
# claims evidence — its own prompt rules handle status != "ok" by forcing
# "investigate" regardless.


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
    print("\nrecommendation:")
    import json as _json
    print(_json.dumps(final_state.get("recommendation", {}), indent=2))