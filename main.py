"""
main.py

FastAPI layer for the multi-agent insurance claims investigation assistant.
Wraps graph.py's LangGraph pipeline and claims_store.py's persistence layer
to power a claims dashboard (list + detail + feedback).

Run with:
    uv run uvicorn main:app --reload

Endpoints:
    GET  /health               public liveness + non-secret Groq/index status
    GET  /auth/config          tells the frontend whether to show a login screen
    POST /auth/login           operator credentials -> short-lived bearer token
    POST /claims               submit a new claim -> runs the full pipeline
    GET  /claims               list claims (paginated summary, for the dashboard)
    GET  /claims/{claim_id}    full detail for one claim
    POST /claims/{claim_id}/feedback   investigator confirms/overrides the outcome

All /claims routes require a bearer token (see auth.py).

Cost/abuse controls: POST /claims is rate-limited per client (each submission
may trigger one Groq call), and the Groq client itself enforces per-minute and
per-day call budgets (see config.py / agents/groq_client.py).
"""

import logging
import threading
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

import auth
import config
from agents import fraud_node as fraud_module
from agents import groq_client
from agents.feedback_node import DuplicateFeedbackError, feedback_node
from db import claims_store
from graph import build_graph
from logging_setup import request_id_var
from middleware import BodyLimitMiddleware, install_observability
from resilience import KeyedRateLimiter
from retrieval import similarity

config.configure_logging()
log = logging.getLogger(__name__)


def _validate_runtime_config() -> None:
    """Refuse to start with an unsafe configuration (raises RuntimeError)."""
    auth.validate_auth_config()
    if config.IS_PRODUCTION and "*" in config.CORS_ORIGINS:
        raise RuntimeError("CORS_ORIGINS must list explicit origins in production, not '*'.")


def _warm_retrieval() -> None:
    """Build the similarity index in the background so the first claim is fast.

    Failure is logged, not fatal: the pipeline degrades to 'no similar-claims
    evidence' with a visible warning, and the next request retries the load.
    """
    try:
        similarity.warm()
        log.info("Retrieval index warmed", extra={"documents": similarity.status()["documents"]})
    except Exception:  # noqa: BLE001 - never block or crash startup on the optional index
        log.exception("Retrieval warm-up failed; similar-claims evidence will be unavailable until fixed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    _validate_runtime_config()
    claims_store.init_db()
    threading.Thread(target=_warm_retrieval, name="retrieval-warmup", daemon=True).start()
    log.info("API started", extra={"environment": config.ENVIRONMENT, "auth_required": auth.auth_required()})
    yield


# Interactive docs and the OpenAPI schema are an attack-surface map: off in production.
_docs = {} if not config.IS_PRODUCTION else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title="Insurance Claims Investigation Assistant API", lifespan=lifespan, **_docs)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
    allow_credentials=False,  # bearer tokens in a header; no cookies involved
)
app.add_middleware(BodyLimitMiddleware, max_bytes=config.MAX_BODY_BYTES)
install_observability(app)


@app.exception_handler(Exception)
async def unhandled_exception(_: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler: log the traceback server-side, never leak it to the client."""
    log.exception("Unhandled error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": request_id_var.get()},
    )


_graph_app = build_graph()  # compiled once at startup
_claims_limiter = KeyedRateLimiter(config.CLAIMS_PER_MINUTE_PER_CLIENT)
_login_limiter = KeyedRateLimiter(config.LOGIN_ATTEMPTS_PER_MINUTE)

# ---------------------------------------------------------------------------
# Request schemas. Numeric bounds are deliberately wider than the training
# range: a value outside the training range is still accepted and routed to
# manual review by fraud_node; these only reject nonsense / abuse.
# ---------------------------------------------------------------------------
Category = Annotated[str, Field(min_length=1, max_length=60)]
SmallInt = Annotated[int, Field(ge=0, le=1_000)]
Money = Annotated[int, Field(ge=0, le=10_000_000)]
SignedMoney = Annotated[int, Field(ge=-10_000_000, le=10_000_000)]


class ClaimSubmission(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    months_as_customer: Annotated[int, Field(ge=0, le=1_200)]
    age: Annotated[int, Field(ge=0, le=120)]
    policy_state: Category
    policy_csl: Category
    policy_deductable: Money
    policy_annual_premium: Annotated[float, Field(ge=0, le=1_000_000)]
    umbrella_limit: SignedMoney
    insured_sex: Category
    insured_education_level: Category
    insured_occupation: Category
    insured_relationship: Category
    capital_gains: SignedMoney = Field(alias="capital-gains")
    capital_loss: SignedMoney = Field(alias="capital-loss")
    incident_type: Category
    collision_type: Category
    incident_severity: Category
    authorities_contacted: Category
    incident_state: Category
    incident_city: Category
    incident_hour_of_the_day: Annotated[int, Field(ge=0, le=23)]
    number_of_vehicles_involved: SmallInt
    property_damage: Category
    bodily_injuries: SmallInt
    witnesses: SmallInt
    police_report_available: Category
    total_claim_amount: Money
    injury_claim: Money
    property_claim: Money
    vehicle_claim: Money
    auto_make: Category
    auto_model: Category
    auto_year: Annotated[int, Field(ge=1900, le=2100)]


class FeedbackSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investigator_decision: Literal["agreed", "overridden_to_approve", "overridden_to_investigate"]
    confirmed_fraud_reported: Literal["Y", "N"]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Annotated[str, Field(min_length=1, max_length=100)]
    password: Annotated[str, Field(min_length=1, max_length=200)]


def _client_ip(request: Request) -> str:
    """Caller address. Behind Render's proxy this is the real client only if uvicorn
    runs with --proxy-headers (see render.yaml)."""
    return request.client.host if request.client else "unknown"


def _too_many(retry_after: float, detail: str) -> HTTPException:
    return HTTPException(status_code=429, detail=detail, headers={"Retry-After": str(int(retry_after) + 1)})


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/")
def home():
    return {"message": "Insurance Claims Investigation Assistant API is running"}


@app.get("/health")
def health():
    """Public liveness probe plus non-secret status of the optional dependencies."""
    return {
        "status": "ok",
        "fraud_model_loaded": fraud_module.model is not None,
        "retrieval": similarity.status(),
        "llm": groq_client.status(),
    }


@app.get("/auth/config")
def auth_config():
    """Tells the frontend whether it must show a login screen."""
    return {"auth_required": auth.auth_required()}


@app.post("/auth/login")
def login(body: LoginRequest, request: Request):
    """Exchange the operator credentials for a short-lived bearer token."""
    if not auth.auth_required():
        raise HTTPException(status_code=400, detail="Authentication is disabled")
    retry_after = _login_limiter.check(_client_ip(request))
    if retry_after is not None:
        raise _too_many(retry_after, "Too many login attempts; try again shortly.")
    if not auth.authenticate(body.username, body.password):
        # Deliberately generic, and the attempted username is never logged (it is
        # often a mistyped password).
        log.warning("Failed login attempt", extra={"client": _client_ip(request)})
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {
        "access_token": auth.create_token(body.username),
        "token_type": "bearer",  # nosec B105 - the OAuth token-type label, not a password
        "expires_in": config.TOKEN_TTL_MINUTES * 60,
    }


@app.post("/claims", dependencies=[Depends(auth.require_user)])
def submit_claim(submission: ClaimSubmission, request: Request):
    client = _client_ip(request)
    retry_after = _claims_limiter.check(client)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many claim submissions; slow down.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    claim = submission.model_dump(by_alias=True)

    # Every step inside the graph degrades instead of raising, so the claim
    # always comes back with a fraud_result and a recommendation.
    final_state = _graph_app.invoke({"claim": claim})

    fraud_result = final_state.get("fraud_result", {})
    similar_claims = final_state.get("similar_claims", [])
    recommendation = final_state.get("recommendation", {})
    warnings = final_state.get("warnings", [])
    similar_summary = final_state.get("similar_claims_summary")

    claim_id = claims_store.insert_claim(
        claim, fraud_result, similar_claims, recommendation, warnings, similar_summary
    )

    return {
        "id": claim_id,
        "claim": claim,
        "fraud_result": fraud_result,
        "similar_claims": similar_claims,
        "similar_claims_summary": similar_summary,
        "recommendation": recommendation,
        "warnings": warnings,
    }


@app.get("/claims", dependencies=[Depends(auth.require_user)])
def get_claims(limit: Annotated[int, Query(ge=1, le=500)] = 100, offset: Annotated[int, Query(ge=0)] = 0):
    """Summary list for the dashboard — trims claim/similar_claims detail
    since the list view only needs status + top-line numbers."""
    claims = claims_store.list_claims(limit=limit, offset=offset)
    return [
        {
            "id": c["id"],
            "status": c["status"],
            "created_at": c["created_at"],
            "fraud_probability": (c["fraud_result"] or {}).get("fraud_probability"),
            "recommendation": (c["recommendation"] or {}).get("recommendation"),
            "recommendation_source": (c["recommendation"] or {}).get("source"),
            "incident_type": (c["claim"] or {}).get("incident_type"),
            "incident_severity": (c["claim"] or {}).get("incident_severity"),
            "total_claim_amount": (c["claim"] or {}).get("total_claim_amount"),
        }
        for c in claims
    ]


@app.get("/claims/{claim_id}", dependencies=[Depends(auth.require_user)])
def get_claim_detail(claim_id: int):
    claim = claims_store.get_claim(claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")
    return claim


@app.post("/claims/{claim_id}/feedback", dependencies=[Depends(auth.require_user)])
def submit_feedback(claim_id: int, feedback: FeedbackSubmission):
    claim_record = claims_store.get_claim(claim_id)
    if claim_record is None:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")
    if claim_record["status"] == "reviewed":
        raise HTTPException(status_code=409, detail=f"Claim {claim_id} was already reviewed")

    try:
        result = feedback_node(
            claim=claim_record["claim"],
            fraud_result=claim_record["fraud_result"] or {},
            recommendation=claim_record["recommendation"] or {},
            investigator_decision=feedback.investigator_decision,
            confirmed_fraud_reported=feedback.confirmed_fraud_reported,
            claim_id=claim_id,
        )
    except DuplicateFeedbackError:
        # Feedback exists but the claim was still 'pending' (e.g. a crash between
        # the two writes): heal the status, then report the conflict.
        claims_store.mark_reviewed(claim_id)
        raise HTTPException(status_code=409, detail=f"Claim {claim_id} was already reviewed") from None

    claims_store.mark_reviewed(claim_id)
    return result
