"""
recommendation_node.py

LangGraph node: takes the fraud_node score/signals and the retrieval_node's
similar-claims evidence, and asks a local Ollama model (llama3.1) to produce
a structured, explainable investigation recommendation.

Expects graph state to already contain "claim", "fraud_result", and
"similar_claims" / "similar_claims_summary" (i.e. runs after fraud_node and
retrieval_node). Writes "recommendation" (dict) back into state.

Requires Ollama running locally with llama3.1 pulled:
    ollama pull llama3.1
"""

import json
import re

import ollama

MODEL_NAME = "llama3.1"

SYSTEM_PROMPT = """You are a claims investigation assistant. You are given a
fraud-risk score from a trained model, the signals behind that score, and a
summary of similar historical claims (including whether those were confirmed
fraud). Your job is to turn this into a clear, explainable recommendation for
a human claims investigator.

Respond with ONLY a JSON object, no other text, no markdown fences, in
exactly this shape:
{
  "recommendation": "approve" | "investigate" | "deny_pending_review",
  "confidence": <float 0.0-1.0>,
  "key_factors": ["short factor 1", "short factor 2", ...],
  "next_steps": ["concrete next step 1", "concrete next step 2", ...]
}

Rules:
- If fraud_result.status is not "ok" (model couldn't score the claim),
  recommendation must be "investigate" and next_steps must include manual
  data verification.
- Ground every key_factor in the actual data given to you — do not invent
  numbers or claim details that weren't provided.
- confidence should reflect how much the fraud score AND the similar-claims
  evidence agree with each other, not just the raw fraud probability.
- Keep key_factors and next_steps concise (under 15 words each)."""


def _build_user_prompt(state: dict) -> str:
    fraud_result = state.get("fraud_result", {})
    similar_summary = state.get("similar_claims_summary", "No similar-claims data available.")
    similar_claims = state.get("similar_claims", [])

    similar_lines = "\n".join(
        f"  - score={c.get('score')} fraud_reported={c.get('fraud_reported')} "
        f"type={c.get('incident_type')} severity={c.get('incident_severity')}"
        for c in similar_claims
    ) or "  (none)"

    return f"""Fraud model result:
{json.dumps(fraud_result, indent=2)}

Similar historical claims summary: {similar_summary}
Similar claims detail:
{similar_lines}

Produce the JSON recommendation now."""


def _parse_llm_json(raw: str) -> dict:
    """Ollama sometimes wraps JSON in markdown fences or adds stray text
    even when asked not to — strip and extract defensively."""
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def recommendation_node(state: dict) -> dict:
    """
    LangGraph node function.

    Input state keys used: "claim", "fraud_result", "similar_claims",
        "similar_claims_summary"
    Output state keys added: "recommendation" (dict)
    """
    user_prompt = _build_user_prompt(state)

    response = ollama.chat(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        options={"temperature": 0.1},  # low temp — this is a judgment task, not creative writing
    )

    raw_content = response["message"]["content"]

    try:
        recommendation = _parse_llm_json(raw_content)
    except (json.JSONDecodeError, AttributeError):
        # Fail safe rather than crash the graph — route to human review
        # whenever we can't trust the model's structured output.
        recommendation = {
            "recommendation": "investigate",
            "confidence": 0.0,
            "key_factors": ["LLM response could not be parsed as JSON"],
            "next_steps": ["Manual review required — recommendation node output was malformed"],
        }

    state["recommendation"] = recommendation
    return state


if __name__ == "__main__":
    # Smoke test using the same fraud_result / similar_claims shape produced
    # by fraud_node.py + retrieval_node.py
    test_state = {
        "claim": {"incident_type": "Single Vehicle Collision", "incident_severity": "Major Damage"},
        "fraud_result": {
            "status": "ok",
            "fraud_probability": 0.869,
            "fraud_signals": [
                "Major damage severity — historically 60%+ fraud rate",
                "Incident type is a collision (Single Vehicle Collision)",
                "High total claim amount ($71,610)",
            ],
            "requires_investigation": True,
            "out_of_range_warnings": [],
        },
        "similar_claims_summary": "3 of 5 most similar past claims were confirmed fraud.",
        "similar_claims": [
            {"score": 0.8736, "fraud_reported": "Y", "incident_type": "Single Vehicle Collision", "incident_severity": "Major Damage"},
            {"score": 0.8591, "fraud_reported": "N", "incident_type": "Single Vehicle Collision", "incident_severity": "Major Damage"},
            {"score": 0.8535, "fraud_reported": "Y", "incident_type": "Single Vehicle Collision", "incident_severity": "Major Damage"},
            {"score": 0.8532, "fraud_reported": "Y", "incident_type": "Multi-vehicle Collision", "incident_severity": "Major Damage"},
            {"score": 0.8522, "fraud_reported": "N", "incident_type": "Single Vehicle Collision", "incident_severity": "Minor Damage"},
        ],
    }

    result = recommendation_node(test_state)
    print(json.dumps(result["recommendation"], indent=2))