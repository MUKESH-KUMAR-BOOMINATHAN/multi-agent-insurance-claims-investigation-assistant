"""
main.py

FastAPI layer for the multi-agent insurance claims investigation assistant.
Wraps graph.py's LangGraph pipeline and claims_store.py's persistence layer
to power a claims dashboard (list + detail + feedback).

Run with:
    uv run uvicorn main:app --reload

Endpoints:
    POST /claims              submit a new claim -> runs the full pipeline
    GET  /claims               list all claims (summary, for the dashboard list view)
    GET  /claims/{claim_id}    full detail for one claim
    POST /claims/{claim_id}/feedback   investigator confirms/overrides the outcome
"""

from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agents.feedback_node import feedback_node
from db import claims_store
from graph import build_graph

app = FastAPI(title="Insurance Claims Investigation Assistant API")

# Allow the React dev server (Vite default port) to call this API directly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_graph_app = build_graph()  # compiled once at startup, same pattern as graph.py's __main__


class ClaimSubmission(BaseModel):
    months_as_customer: int
    age: int
    policy_state: str
    policy_csl: str
    policy_deductable: int
    policy_annual_premium: float
    umbrella_limit: int
    insured_sex: str
    insured_education_level: str
    insured_occupation: str
    insured_relationship: str
    capital_gains: int = Field(alias="capital-gains")
    capital_loss: int = Field(alias="capital-loss")
    incident_type: str
    collision_type: str
    incident_severity: str
    authorities_contacted: str
    incident_state: str
    incident_city: str
    incident_hour_of_the_day: int
    number_of_vehicles_involved: int
    property_damage: str
    bodily_injuries: int
    witnesses: int
    police_report_available: str
    total_claim_amount: int
    injury_claim: int
    property_claim: int
    vehicle_claim: int
    auto_make: str
    auto_model: str
    auto_year: int

    class Config:
        populate_by_name = True


class FeedbackSubmission(BaseModel):
    investigator_decision: str  # e.g. "agreed", "overridden_to_approve"
    confirmed_fraud_reported: str  # "Y" or "N"


@app.get("/")
def home():
    return {"message": "Insurance Claims Investigation Assistant API is running"}


@app.post("/claims")
def submit_claim(submission: ClaimSubmission):
    claim = submission.model_dump(by_alias=True)

    final_state = _graph_app.invoke({"claim": claim})

    fraud_result = final_state.get("fraud_result", {})
    similar_claims = final_state.get("similar_claims", [])
    recommendation = final_state.get("recommendation", {})

    claim_id = claims_store.insert_claim(claim, fraud_result, similar_claims, recommendation)

    return {
        "id": claim_id,
        "claim": claim,
        "fraud_result": fraud_result,
        "similar_claims": similar_claims,
        "recommendation": recommendation,
    }


@app.get("/claims")
def get_claims():
    """Summary list for the dashboard — trims claim/similar_claims detail
    since the list view only needs status + top-line numbers."""
    claims = claims_store.list_claims()
    return [
        {
            "id": c["id"],
            "status": c["status"],
            "created_at": c["created_at"],
            "fraud_probability": (c["fraud_result"] or {}).get("fraud_probability"),
            "recommendation": (c["recommendation"] or {}).get("recommendation"),
            "incident_type": (c["claim"] or {}).get("incident_type"),
        }
        for c in claims
    ]


@app.get("/claims/{claim_id}")
def get_claim_detail(claim_id: int):
    claim = claims_store.get_claim(claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")
    return claim


@app.post("/claims/{claim_id}/feedback")
def submit_feedback(claim_id: int, feedback: FeedbackSubmission):
    claim_record = claims_store.get_claim(claim_id)
    if claim_record is None:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    result = feedback_node(
        claim=claim_record["claim"],
        fraud_result=claim_record["fraud_result"] or {},
        recommendation=claim_record["recommendation"] or {},
        investigator_decision=feedback.investigator_decision,
        confirmed_fraud_reported=feedback.confirmed_fraud_reported,
        claim_id=claim_id,
    )

    claims_store.mark_reviewed(claim_id)

    return result