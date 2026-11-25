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
    POST /auth/login           credentials -> short-lived bearer token + role
    GET  /auth/me              the signed-in account
    POST /auth/password        change your own password (required after a reset)
    POST /demo/start           one-click sandbox session as an investigator or customer (if enabled)
    POST /demo/switch          the same visitor, other role
    GET  /demo/status          claims used against the demo cap

  Organization (admin, investigator):
    POST /claims               submit a new claim -> runs the full pipeline
    GET  /claims               list claims (paginated summary, for the dashboard)
    GET  /claims/{claim_id}    full detail for one claim
    POST /claims/{claim_id}/feedback   investigator confirms/overrides the outcome
    GET  /users                accounts this user may manage
    POST /users                create an investigator (admin) or customer account
    POST /users/{id}/active    activate / deactivate an account
    POST /users/{id}/password  reset an account's password (user must change it)
    GET  /audit                activity log (admin only)

  Customer:
    GET  /my/policy            the policy details the organization recorded
    POST /my/claims            file a claim (incident details only)
    GET  /my/claims            own claims with a plain status (no fraud score)

Roles are enforced on every route (see auth.py).

Cost/abuse controls: POST /claims is rate-limited per client (each submission
may trigger one Groq call), and the Groq client itself enforces per-minute and
per-day call budgets (see config.py / agents/groq_client.py).
"""

import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

import auth
import config
import demo
from agents import fraud_node as fraud_module
from agents import groq_client
from agents.feedback_node import DuplicateFeedbackError, feedback_node
from db import claims_store, security_store, users_store
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
    if config.IS_PRODUCTION and not config.DATABASE_URL and not os.getenv("DATA_DIR"):
        # Without either, data goes to SQLite files on the host's (usually
        # ephemeral) filesystem and is silently lost on the next restart.
        raise RuntimeError(
            "Set DATABASE_URL (e.g. a free Neon Postgres) in production, or DATA_DIR pointing at a "
            "persistent disk. Refusing to store data on an ephemeral filesystem."
        )


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


def _seed_demo_data() -> None:
    """Create the sample claims once, in the background (it runs the real pipeline)."""
    try:
        log.info("Demo data: %s", demo.ensure_seed())
    except Exception:  # noqa: BLE001 - a missing sample must never take the API down
        log.exception("Seeding the demo data failed; it will be retried on the next start")


def _background_startup() -> None:
    _warm_retrieval()
    if config.DEMO_ENABLED:
        _seed_demo_data()


@asynccontextmanager
async def lifespan(_: FastAPI):
    _validate_runtime_config()
    claims_store.init_db()
    threading.Thread(target=_background_startup, name="startup-warmup", daemon=True).start()
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


class DemoRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["investigator", "customer", "admin"]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Annotated[str, Field(min_length=1, max_length=100)]
    password: Annotated[str, Field(min_length=1, max_length=200)]


# A customer only describes the incident; the insurer-side facts (policy, insured
# person, insured vehicle) are recorded by the organization on the customer's
# account. Both schemas are derived from ClaimSubmission so the bounds live in
# exactly one place.
PROFILE_FIELDS = (
    "months_as_customer", "age", "policy_state", "policy_csl", "policy_deductable",
    "policy_annual_premium", "umbrella_limit", "insured_sex", "insured_education_level",
    "insured_occupation", "insured_relationship", "capital_gains", "capital_loss",
    "auto_make", "auto_model", "auto_year",
)
INCIDENT_FIELDS = (
    "incident_type", "collision_type", "incident_severity", "authorities_contacted",
    "incident_state", "incident_city", "incident_hour_of_the_day", "number_of_vehicles_involved",
    "property_damage", "bodily_injuries", "witnesses", "police_report_available",
    "injury_claim", "property_claim", "vehicle_claim",
)
_STRICT = ConfigDict(populate_by_name=True, extra="forbid")


def _subset_of_claim(name: str, names: tuple[str, ...]) -> type[BaseModel]:
    fields = {n: (ClaimSubmission.model_fields[n].annotation, ClaimSubmission.model_fields[n]) for n in names}
    return create_model(name, __config__=_STRICT, **fields)  # type: ignore[call-overload]


PolicyProfile = _subset_of_claim("PolicyProfile", PROFILE_FIELDS)
IncidentSubmission = _subset_of_claim("IncidentSubmission", INCIDENT_FIELDS)

Username = Annotated[str, Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9][A-Za-z0-9._@-]*$")]
NewPassword = Annotated[str, Field(min_length=10, max_length=200)]


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Username
    full_name: Annotated[str, Field(min_length=1, max_length=100)]
    password: NewPassword
    role: Literal["investigator", "customer"]
    profile: PolicyProfile | None = None  # type: ignore[valid-type]


class SetActiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool


class SetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: NewPassword


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: Annotated[str, Field(min_length=1, max_length=200)]
    new_password: NewPassword


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
        # Configuration only: /health never queries the database, so frequent
        # health checks do not keep a scale-to-zero database (Neon) awake.
        "database": "postgres" if config.DATABASE_URL else "sqlite",
        "demo_enabled": config.DEMO_ENABLED,
        "retrieval": similarity.status(),
        "llm": groq_client.status(),
    }


@app.get("/auth/config")
def auth_config():
    """Tells the frontend whether it must show a login screen (and whether the demo is on)."""
    cfg: dict = {"auth_required": auth.auth_required()}
    if config.DEMO_ENABLED and auth.auth_required():
        cfg["demo"] = {"enabled": True, "max_claims": config.DEMO_MAX_CLAIMS, "contact_url": config.DEMO_CONTACT_URL}
    return cfg


@app.post("/auth/login")
def login(body: LoginRequest, request: Request):
    """Exchange the operator credentials for a short-lived bearer token."""
    if not auth.auth_required():
        raise HTTPException(status_code=400, detail="Authentication is disabled")
    retry_after = _login_limiter.check(_client_ip(request))
    if retry_after is not None:
        raise _too_many(retry_after, "Too many login attempts; try again shortly.")
    ip = _client_ip(request)
    key = users_store.username_key(body.username)
    locked_for = security_store.seconds_locked(key)
    if locked_for is not None:
        # Same answer whether or not the account exists (unknown names are counted too).
        raise _too_many(locked_for, "Too many failed sign-ins for this account. Try again later.")

    user = auth.authenticate(body.username, body.password)
    if user is None:
        locked_now = security_store.record_failure(key)
        # Only real usernames go in the audit log: a typed "username" that is not
        # an account is often a password typed into the wrong box.
        if auth.resolve_user(body.username) is not None:
            security_store.audit("login_locked" if locked_now else "login_failed", actor=body.username, ip=ip)
        log.warning("Failed login attempt", extra={"client": ip})
        raise HTTPException(status_code=401, detail="Invalid credentials")

    security_store.clear_failures(key)
    security_store.audit("login", actor=user.username, ip=ip)
    return {
        "access_token": auth.create_token(user.username, user.role),
        "token_type": "bearer",  # nosec B105 - the OAuth token-type label, not a password
        "expires_in": config.TOKEN_TTL_MINUTES * 60,
        "user": _user_view(user),
    }


def _user_view(user: auth.User) -> dict:
    view = {
        "username": user.username,
        "role": user.role,
        "full_name": user.full_name,
        "must_change_password": user.must_change_password,
    }
    if user.is_demo:
        view["demo"] = True
    return view


def _demo_session_response(session_id: str, role: str) -> dict:
    user = auth.resolve_demo({"sub": demo.SUBJECT_PREFIX + session_id, "demo_role": role})
    if user is None:  # demo was switched off between the check and here
        raise HTTPException(status_code=404, detail="The demo is not available")
    return {
        "access_token": auth.create_demo_token(session_id, role),
        "token_type": "bearer",  # nosec B105 - the OAuth token-type label, not a password
        "expires_in": config.DEMO_TOKEN_TTL_MINUTES * 60,
        "user": _user_view(user),
        "demo": demo.usage(session_id),
    }


def _demo_user(user: auth.User) -> auth.User:
    if not user.is_demo:
        raise HTTPException(status_code=403, detail="Demo sessions only")
    return user


def _demo_admin(user: auth.User) -> None:
    """The demo admin may look at the (fictional) People page and activity log; nobody else
    in the demo may touch the account routes."""
    if not (user.is_demo and user.role == "admin"):
        raise HTTPException(status_code=403, detail="Not available in the demo")


def _refuse_demo_admin_write(user: auth.User) -> None:
    if user.is_demo and user.role == "admin":
        raise HTTPException(
            status_code=403,
            detail="The admin demo is read-only, so nothing can be added here. Switch to investigator or customer to file and review claims.",
        )


@app.post("/demo/start")
def demo_start(body: DemoRoleRequest, request: Request):
    """One click, no password: a private sandbox as an investigator or a customer."""
    if not config.DEMO_ENABLED or not auth.auth_required():
        raise HTTPException(status_code=404, detail="The demo is not available")
    retry_after = demo.check_start(_client_ip(request))
    if retry_after is not None:
        raise HTTPException(
            status_code=429, detail=demo.limit_detail("starts"), headers={"Retry-After": str(int(retry_after) + 1)}
        )
    return _demo_session_response(demo.begin_session(), body.role)


@app.post("/demo/switch")
def demo_switch(body: DemoRoleRequest, user: Annotated[auth.User, Depends(auth.require_account)]):
    """The same visitor and the same sandbox, seen as the other role."""
    return _demo_session_response(str(_demo_user(user).demo_session), body.role)


@app.get("/demo/status")
def demo_status(user: Annotated[auth.User, Depends(auth.require_account)]):
    return demo.usage(str(_demo_user(user).demo_session))


@app.get("/auth/me")
def me(user: Annotated[auth.User, Depends(auth.require_account)]):
    """The signed-in account (the frontend uses this to choose the portal)."""
    return _user_view(user)


@app.post("/auth/password", status_code=204)
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    user: Annotated[auth.User, Depends(auth.require_account)],
):
    """Change your own password. Works with a temporary password, so a user
    whose password was set by the organization can replace it."""
    if user.is_demo:
        raise HTTPException(status_code=403, detail="Not available in the demo")
    if user.role == "admin":
        raise HTTPException(
            status_code=400,
            detail="The administrator password is set on the server (ADMIN_PASSWORD_HASH), not here.",
        )
    ip = _client_ip(request)
    retry_after = _login_limiter.check(ip)
    if retry_after is not None:
        raise _too_many(retry_after, "Too many attempts; try again shortly.")
    if auth.authenticate(user.username, body.current_password) is None:
        security_store.audit("password_change_failed", actor=user.username, ip=ip)
        raise HTTPException(status_code=400, detail="Your current password is not correct")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=422, detail="Choose a password different from the current one")
    account = users_store.get_active_user(user.username)
    if account is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    users_store.set_password(account["id"], auth.hash_password(body.new_password), must_change=False)
    security_store.audit("password_changed", actor=user.username, ip=ip)


# ---------------------------------------------------------------------------
# Claims pipeline (shared by the organization and customer routes)
# ---------------------------------------------------------------------------
def _check_claim_rate(key: str) -> None:
    retry_after = _claims_limiter.check(key)
    if retry_after is not None:
        raise _too_many(retry_after, "Too many claim submissions; slow down.")


def _enforce_demo_cap(user: auth.User) -> None:
    """A demo visitor may file only DEMO_MAX_CLAIMS claims; the 429 carries a contact link."""
    if claims_store.count_session_claims(str(user.demo_session)) >= config.DEMO_MAX_CLAIMS:
        raise HTTPException(status_code=429, detail=demo.limit_detail("claims"))


def _run_pipeline(claim: dict, submitted_by: str, demo_session: str | None = None) -> dict:
    """Score, retrieve, recommend and persist one claim. A degraded step never
    raises: every node falls back instead (see graph.py). A demo claim is saved in
    its visitor's sandbox and only reaches the LLM while the shared demo allowance lasts."""
    state_in: dict = {"claim": claim}
    if demo_session is not None:
        state_in["use_llm"] = demo.allow_ai()
    final_state = _graph_app.invoke(state_in)

    fraud_result = final_state.get("fraud_result", {})
    similar_claims = final_state.get("similar_claims", [])
    recommendation = final_state.get("recommendation", {})
    warnings = final_state.get("warnings", [])
    similar_summary = final_state.get("similar_claims_summary")

    claim_id = claims_store.insert_claim(
        claim, fraud_result, similar_claims, recommendation, warnings, similar_summary, submitted_by,
        demo_session=demo_session,
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


@app.post("/claims")
def submit_claim(
    submission: ClaimSubmission,
    request: Request,
    user: Annotated[auth.User, Depends(auth.require_org)],
):
    """Organization submits a full claim (all 32 fields) and sees the full analysis."""
    _refuse_demo_admin_write(user)
    _check_claim_rate(_client_ip(request))
    if user.is_demo:
        _enforce_demo_cap(user)
        return _run_pipeline(submission.model_dump(by_alias=True), user.username, demo_session=user.demo_session)
    result = _run_pipeline(submission.model_dump(by_alias=True), submitted_by=user.username)
    security_store.audit("claim_submitted", actor=user.username, target=f"claim:{result['id']}")
    return result


@app.get("/claims")
def get_claims(
    user: Annotated[auth.User, Depends(auth.require_org)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Summary list for the dashboard — trims claim/similar_claims detail
    since the list view only needs status + top-line numbers."""
    claims = claims_store.list_claims(limit=limit, offset=offset, session=user.demo_session)
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
            "submitted_by": c.get("submitted_by"),
            "outcome": c.get("outcome"),
        }
        for c in claims
    ]


@app.get("/claims/{claim_id}")
def get_claim_detail(claim_id: int, user: Annotated[auth.User, Depends(auth.require_org)]):
    claim = claims_store.get_claim(claim_id)
    # 404 (not 403) for another visitor's sandbox claim: don't confirm it exists.
    if claim is None or not claims_store.is_visible(claim, user.demo_session):
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")
    return claim


@app.post("/claims/{claim_id}/feedback")
def submit_feedback(
    claim_id: int,
    feedback: FeedbackSubmission,
    user: Annotated[auth.User, Depends(auth.require_org)],
):
    claim_record = claims_store.get_claim(claim_id)
    if claim_record is None or not claims_store.is_visible(claim_record, user.demo_session):
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")
    if claim_record["status"] == "reviewed":
        raise HTTPException(status_code=409, detail=f"Claim {claim_id} was already reviewed")

    _refuse_demo_admin_write(user)
    if user.is_demo:
        # A sandbox decision is never saved as feedback: it must not change the
        # similar-claims evidence or the data the model is retrained on.
        if claim_record.get("demo_session") != user.demo_session:
            raise HTTPException(status_code=403, detail="Sample claims are read-only. Review one of your own claims.")
        claims_store.mark_reviewed(claim_id, _outcome(feedback))
        return {"status": "ok", "feedback_id": None, "indexed": False, "demo": True}

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
        claims_store.mark_reviewed(claim_id, _outcome(feedback))
        raise HTTPException(status_code=409, detail=f"Claim {claim_id} was already reviewed") from None

    claims_store.mark_reviewed(claim_id, _outcome(feedback))
    security_store.audit("claim_reviewed", actor=user.username, target=f"claim:{claim_id}:{_outcome(feedback)}")
    return result


def _outcome(feedback: FeedbackSubmission) -> str:
    """What the customer is told once an investigator has confirmed the facts."""
    return "approved" if feedback.confirmed_fraud_reported == "N" else "not_approved"


# ---------------------------------------------------------------------------
# Customer portal: own claims only, plain status, never the fraud analysis
# ---------------------------------------------------------------------------
def _customer_view(c: dict) -> dict:
    """Everything a customer may see about their own claim. Deliberately omits the
    fraud score, signals, similar claims and the AI recommendation: showing those
    would teach a fraudster exactly what gets flagged."""
    status = "under_review" if c["status"] == "pending" else (c.get("outcome") or "closed")
    claim = c["claim"] or {}
    return {
        "id": c["id"],
        "created_at": c["created_at"],
        "reviewed_at": c.get("reviewed_at"),
        "status": status,
        "incident_type": claim.get("incident_type"),
        "incident_severity": claim.get("incident_severity"),
        "total_claim_amount": claim.get("total_claim_amount"),
    }


def _customer_profile(user: auth.User) -> dict:
    if user.is_demo:
        return demo.DEMO_PROFILE
    account = users_store.get_active_user(user.username)
    profile = (account or {}).get("profile")
    if not profile:
        raise HTTPException(
            status_code=409,
            detail="Your policy details have not been set up yet. Please contact your insurer.",
        )
    return profile


@app.get("/my/policy")
def my_policy(user: Annotated[auth.User, Depends(auth.require_customer)]):
    """The policy facts on file, so the customer can check them before filing."""
    profile = _customer_profile(user)
    keys = ("policy_state", "policy_csl", "policy_deductable", "policy_annual_premium",
            "auto_make", "auto_model", "auto_year", "months_as_customer")
    return {k: profile.get(k) for k in keys}


@app.post("/my/claims")
def file_claim(
    incident: IncidentSubmission,  # type: ignore[valid-type]
    user: Annotated[auth.User, Depends(auth.require_customer)],
):
    """A customer files a claim: their incident details + the policy on file.
    The total is computed here, never trusted from the client."""
    _check_claim_rate(f"demo:{user.demo_session}" if user.is_demo else f"customer:{user.username}")
    if user.is_demo:
        _enforce_demo_cap(user)
    details = incident.model_dump(by_alias=True)
    details["total_claim_amount"] = details["injury_claim"] + details["property_claim"] + details["vehicle_claim"]
    try:
        claim = ClaimSubmission(**{**_customer_profile(user), **details}).model_dump(by_alias=True)
    except ValidationError:
        log.error("Stored customer profile failed validation")
        raise HTTPException(
            status_code=409, detail="Your policy details need updating. Please contact your insurer."
        ) from None
    result = _run_pipeline(claim, submitted_by=user.username, demo_session=user.demo_session)
    if not user.is_demo:
        security_store.audit("claim_filed", actor=user.username, target=f"claim:{result['id']}")
    stored = claims_store.get_claim(result["id"])
    if stored is None:  # just inserted; only a concurrently deleted database could do this
        raise HTTPException(status_code=500, detail="Claim could not be saved")
    return _customer_view(stored)


@app.get("/my/claims")
def my_claims(
    user: Annotated[auth.User, Depends(auth.require_customer)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    rows = claims_store.list_claims_for_user(user.username, limit, offset, session=user.demo_session)
    return [_customer_view(c) for c in rows]


# ---------------------------------------------------------------------------
# Account management (organization only)
# ---------------------------------------------------------------------------
def _manageable_roles(actor: auth.User) -> tuple[str, ...]:
    """Admin manages investigators and customers; investigators manage customers."""
    return ("investigator", "customer") if actor.role == "admin" else ("customer",)


def _managed_user_or_404(user_id: int, actor: auth.User) -> dict:
    target = users_store.get_user(user_id)
    # 404 (not 403) for accounts the actor may not manage: don't confirm they exist.
    if target is None or target["role"] not in _manageable_roles(actor):
        raise HTTPException(status_code=404, detail="User not found")
    return target


@app.get("/users")
def list_users(actor: Annotated[auth.User, Depends(auth.require_org)]):
    if actor.is_demo:
        _demo_admin(actor)
        return demo.sample_users()  # fictional: the real accounts are never read
    return users_store.list_users(_manageable_roles(actor))


@app.post("/users", status_code=201)
def create_user(body: CreateUserRequest, actor: Annotated[auth.User, Depends(auth.require_org)]):
    if actor.is_demo:
        _demo_admin(actor)
    if body.role not in _manageable_roles(actor):
        raise HTTPException(status_code=403, detail="Only the administrator can create investigator accounts")
    if body.role == "customer" and body.profile is None:
        raise HTTPException(status_code=422, detail="A customer account needs its policy details (profile)")
    if body.role != "customer" and body.profile is not None:
        raise HTTPException(status_code=422, detail="Only customer accounts have policy details")
    if actor.is_demo:
        # Same validation as a real account, but nothing is stored (and the real admin's
        # username and the real accounts are never consulted).
        if body.username.casefold() in demo.sample_usernames():
            raise HTTPException(status_code=409, detail="That username is already taken")
        return demo.fake_created_user(
            body.username, body.full_name.strip(), body.role,
            body.profile.model_dump(by_alias=True) if body.profile else None, actor.username,
        )
    if auth.is_reserved_username(body.username):
        raise HTTPException(status_code=409, detail="That username is already taken")
    try:
        user = users_store.create_user(
            username=body.username,
            full_name=body.full_name.strip(),
            role=body.role,
            password_hash=auth.hash_password(body.password),
            created_by=actor.username,
            profile=body.profile.model_dump(by_alias=True) if body.profile else None,
        )
    except users_store.DuplicateUserError:
        raise HTTPException(status_code=409, detail="That username is already taken") from None
    log.info("Account created", extra={"role": body.role, "by_role": actor.role})
    security_store.audit("account_created", actor=actor.username, target=f"{body.role}:{user['username']}")
    return user


@app.post("/users/{user_id}/active")
def set_user_active(
    user_id: int, body: SetActiveRequest, actor: Annotated[auth.User, Depends(auth.require_real_org)]
):
    target = _managed_user_or_404(user_id, actor)
    users_store.set_active(user_id, body.active)
    action = "account_activated" if body.active else "account_deactivated"
    security_store.audit(action, actor=actor.username, target=f"{target['role']}:{target['username']}")
    return users_store.get_user(user_id)


@app.post("/users/{user_id}/password", status_code=204)
def reset_user_password(
    user_id: int, body: SetPasswordRequest, actor: Annotated[auth.User, Depends(auth.require_real_org)]
):
    target = _managed_user_or_404(user_id, actor)
    # The organization chose this password, so the user must replace it at next sign-in.
    users_store.set_password(user_id, auth.hash_password(body.password), must_change=True)
    security_store.clear_failures(users_store.username_key(target["username"]))  # also unlocks
    security_store.audit("password_reset", actor=actor.username, target=f"{target['role']}:{target['username']}")


@app.get("/audit")
def audit_log(
    admin: Annotated[auth.User, Depends(auth.require_admin)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    """Most recent activity first: sign-ins, account changes, claims filed and reviewed."""
    if admin.is_demo:
        return demo.sample_audit(limit)  # fictional: the real log is never read
    return security_store.list_audit(limit)
