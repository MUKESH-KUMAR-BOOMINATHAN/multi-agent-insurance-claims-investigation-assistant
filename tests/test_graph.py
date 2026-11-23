"""Graph routing and the 'a claim always gets an answer' guarantee."""

import graph as graph_module
from graph import build_graph, route_after_fraud


def test_routing_depends_on_fraud_status():
    assert route_after_fraud({"fraud_result": {"status": "ok"}}) == "retrieve"
    assert route_after_fraud({"fraud_result": {"status": "missing_field"}}) == "skip_retrieval"
    assert route_after_fraud({}) == "skip_retrieval"


def test_full_pipeline_degrades_gracefully_with_no_index_and_no_key(valid_claim):
    state = build_graph().invoke({"claim": valid_claim})
    assert state["fraud_result"]["status"] == "ok"
    assert state["similar_claims"] == []  # index stubbed as not built
    assert state["recommendation"]["source"] == "rules_fallback"
    assert state["recommendation"]["fallback_reason"] == "no_api_key"
    # Both degradations are surfaced to the user, not silent.
    joined = " ".join(state["warnings"])
    assert "could not be loaded" in joined and "rules-based recommendation" in joined


def test_unscorable_claim_skips_retrieval_entirely(valid_claim, monkeypatch):
    def must_not_run(_state):
        raise AssertionError("retrieval must be skipped for unscorable claims")

    monkeypatch.setattr(graph_module, "_retrieval_node_fn", must_not_run)
    valid_claim["incident_city"] = "Atlantis"
    state = build_graph().invoke({"claim": valid_claim})
    assert state["fraud_result"]["status"] == "unrecognized_category"
    assert state["recommendation"]["recommendation"] == "investigate"


def test_crashing_fraud_node_still_yields_a_recommendation(valid_claim, monkeypatch):
    def boom(_claim):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(graph_module, "fraud_node", boom)
    state = build_graph().invoke({"claim": valid_claim})
    assert state["fraud_result"]["status"] == "scoring_error"
    assert state["recommendation"]["recommendation"] == "investigate"
    assert any("Fraud scoring failed" in w for w in state["warnings"])


def test_crashing_retrieval_node_is_contained(valid_claim, monkeypatch):
    def boom(_state):
        raise RuntimeError("index exploded")

    monkeypatch.setattr(graph_module, "_retrieval_node_fn", boom)
    state = build_graph().invoke({"claim": valid_claim})
    assert state["similar_claims"] == []
    assert state["recommendation"]["recommendation"] in {"investigate", "approve"}


def test_crashing_recommendation_node_is_contained(valid_claim, monkeypatch):
    def boom(_state):
        raise RuntimeError("llm node exploded")

    monkeypatch.setattr(graph_module, "_recommendation_node_fn", boom)
    state = build_graph().invoke({"claim": valid_claim})
    assert state["recommendation"]["fallback_reason"] == "unexpected_error"
