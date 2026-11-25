"""
demo.py

The public demo: one click, no password, a private sandbox per visitor.

Why it exists. A visitor to the live link should be able to try every part of
the product without being handed a login, but a shared login on a public site
gets abused quickly. So a visitor instead gets a short-lived token (see
auth.py) for a throwaway identity, and everything they do is fenced in:

  * Their claims are tagged with their session id and are invisible to everyone
    else (db/claims_store.py). Real users never see sandbox rows either.
  * Their decisions never reach the feedback table, so strangers cannot change
    the similar-claims evidence or the data the model is retrained on.
  * They cannot create accounts, reset passwords or read the activity log
    (main.py refuses those routes for demo users).
  * They may file at most DEMO_MAX_CLAIMS claims, make at most
    DEMO_REQUESTS_PER_MINUTE requests a minute, and share one daily AI budget
    (DEMO_AI_CALLS_PER_DAY). Past that, a friendly message points them to the
    author's portfolio instead of failing mysteriously.
  * Sandbox data is purged after DEMO_DATA_TTL_HOURS (lazily, whenever someone
    starts a demo: the free host has no scheduler).

The same module seeds the sample claims that make the dashboards look alive,
and a few hidden "template" claims that are copied into each new sandbox so the
investigator has something to review immediately.

Nothing here imports FastAPI, so auth.py can use it without a cycle.
"""

from __future__ import annotations

import logging
import random
import re
import secrets
import time
from datetime import UTC, datetime, timedelta

import config
from db import claims_store
from resilience import KeyedRateLimiter, SlidingWindowBudget

log = logging.getLogger(__name__)

ROLES = ("investigator", "customer")
SUBJECT_PREFIX = "demo:"
_SESSION_ID = re.compile(r"^[0-9a-f]{24}$")

# The policy a demo customer "has on file": the 16 insurer-side fields a real
# customer's account would carry (see main.PROFILE_FIELDS).
DEMO_PROFILE: dict = {
    "months_as_customer": 214, "age": 41, "policy_state": "OH", "policy_csl": "250/500",
    "policy_deductable": 1000, "policy_annual_premium": 1280.5, "umbrella_limit": 0,
    "insured_sex": "FEMALE", "insured_education_level": "College", "insured_occupation": "prof-specialty",
    "insured_relationship": "wife", "capital-gains": 0, "capital-loss": 0,
    "auto_make": "Honda", "auto_model": "Accord", "auto_year": 2012,
}


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
def _new_ai_budget() -> SlidingWindowBudget:
    return SlidingWindowBudget(config.GROQ_MAX_CALLS_PER_MINUTE, config.DEMO_AI_CALLS_PER_DAY)


_ai_budget = _new_ai_budget()
request_limiter = KeyedRateLimiter(config.DEMO_REQUESTS_PER_MINUTE)
start_limiter = KeyedRateLimiter(config.DEMO_STARTS_PER_HOUR_PER_IP, window_s=3600.0)
total_start_limiter = KeyedRateLimiter(config.DEMO_STARTS_PER_HOUR_TOTAL, window_s=3600.0)


def check_start(client_ip: str) -> float | None:
    """Count one demo start. None = allowed, else seconds to wait (per address, then overall)."""
    retry = start_limiter.check(client_ip)
    return retry if retry is not None else total_start_limiter.check("all")


def reset_limits() -> None:
    """Recreate every limiter from the current config (used by tests)."""
    global _ai_budget, request_limiter, start_limiter, total_start_limiter
    _ai_budget = _new_ai_budget()
    request_limiter = KeyedRateLimiter(config.DEMO_REQUESTS_PER_MINUTE)
    start_limiter = KeyedRateLimiter(config.DEMO_STARTS_PER_HOUR_PER_IP, window_s=3600.0)
    total_start_limiter = KeyedRateLimiter(config.DEMO_STARTS_PER_HOUR_TOTAL, window_s=3600.0)


def allow_ai() -> bool:
    """Claim one slot of the shared daily demo AI budget. False means: use the rules engine."""
    return _ai_budget.try_acquire()


def limit_detail(kind: str) -> dict:
    """The body of a 429 caused by a demo limit. ``contact_url`` lets the UI offer a next step."""
    messages = {
        "claims": (
            f"You've reached the demo limit of {config.DEMO_MAX_CLAIMS} claims. This is a free sandbox with "
            "capped AI calls. For higher limits, a deployment of your own, or a custom build, get in touch "
            "through Mukesh's portfolio."
        ),
        "rate": "You're going faster than this free demo allows. Wait a few seconds and try again.",
        "starts": "Too many demo sessions were started from your network. Please try again in a while.",
    }
    return {"code": "demo_limit", "kind": kind, "message": messages[kind], "contact_url": config.DEMO_CONTACT_URL}


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def new_session_id() -> str:
    return secrets.token_hex(12)


def is_valid_session_id(session_id: str) -> bool:
    return bool(_SESSION_ID.fullmatch(session_id))


def begin_session() -> str:
    """Start a sandbox: tidy up old ones, copy in the starter claims, return the session id."""
    session_id = new_session_id()
    try:
        cutoff = (datetime.now(UTC) - timedelta(hours=config.DEMO_DATA_TTL_HOURS)).isoformat()
        removed = claims_store.purge_demo_sandboxes(cutoff)
        if removed:
            log.info("Purged expired demo claims", extra={"count": removed})
    except Exception:  # noqa: BLE001 - cleanup must never stop a visitor from starting the demo
        log.exception("Could not purge old demo claims")
    try:
        claims_store.clone_templates(session_id)
    except Exception:  # noqa: BLE001 - the demo still works without starter claims
        log.exception("Could not copy the starter claims into a new demo sandbox")
    return session_id


def usage(session_id: str) -> dict:
    """What the demo banner shows: claims used against the cap, and where to get more."""
    return {
        "claims_used": claims_store.count_session_claims(session_id),
        "claims_max": config.DEMO_MAX_CLAIMS,
        "contact_url": config.DEMO_CONTACT_URL,
    }


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------
# The 32 fields of a claim, in the dataset's own column names.
CLAIM_FIELDS = (
    "months_as_customer", "age", "policy_state", "policy_csl", "policy_deductable", "policy_annual_premium",
    "umbrella_limit", "insured_sex", "insured_education_level", "insured_occupation", "insured_relationship",
    "capital-gains", "capital-loss", "incident_type", "collision_type", "incident_severity",
    "authorities_contacted", "incident_state", "incident_city", "incident_hour_of_the_day",
    "number_of_vehicles_involved", "property_damage", "bodily_injuries", "witnesses",
    "police_report_available", "total_claim_amount", "injury_claim", "property_claim", "vehicle_claim",
    "auto_make", "auto_model", "auto_year",
)

# Fictional policyholders for the sample claims' "Submitted by" column.
SAMPLE_CUSTOMERS = (
    "ava.martinez", "liam.chen", "noah.patel", "mia.johnson", "ethan.okafor", "sofia.rossi",
    "lucas.kim", "isla.nguyen", "owen.schmidt", "zoe.hernandez", "kai.tanaka", "ruby.walker",
)

_PACE_S = 6.5  # keeps seeding under Groq's per-minute budget (10 calls/minute)


def _py(value):
    """numpy scalar -> plain Python, so it serialises to JSON."""
    return value.item() if hasattr(value, "item") else value


def _claim_from_row(row) -> dict:
    return {field: _py(row[field]) for field in CLAIM_FIELDS}


def _row(claim, state, *, tag, status, created, submitted_by, reviewed=None, outcome=None) -> dict:
    import json

    return {
        "claim_json": json.dumps(claim),
        "fraud_result_json": json.dumps(state.get("fraud_result", {})),
        "similar_claims_json": json.dumps(state.get("similar_claims", [])),
        "recommendation_json": json.dumps(state.get("recommendation", {})),
        "status": status,
        "created_at": created.isoformat(),
        "reviewed_at": reviewed.isoformat() if reviewed else None,
        "warnings_json": json.dumps(state.get("warnings", [])),
        "similar_summary": state.get("similar_claims_summary"),
        "submitted_by": submitted_by,
        "outcome": outcome,
        "demo_session": tag,
    }


def build_sample_rows(pace_s: float | None = None) -> list[dict]:
    """Run real dataset claims through the real pipeline and return them as rows.

    Nothing is written here. A mix of fraud and genuine claims (40/60) is spread over
    the last three weeks and marked reviewed, which fills the analytics charts, plus
    three PENDING templates (one high, one low and one medium risk) that each new
    sandbox gets a copy of. The pipeline is the live one, so with a Groq key set the
    recommendations are real AI output; the pace keeps it inside the per-minute budget.
    """
    from agents.fraud_node import fraud_node
    from claims_data import load_cleaned_claims
    from graph import build_graph
    from retrieval import similarity

    try:
        similarity.warm()
    except Exception:  # noqa: BLE001 - the pipeline degrades to "no evidence" with a warning
        log.exception("Similar-claims index unavailable while seeding; samples will have no evidence")

    df = load_cleaned_claims(config.CSV_PATH)
    rng = random.Random(2026)  # noqa: S311  # nosec B311 - a fixed-seed sample picker, not security
    n = max(1, config.DEMO_SEED_CLAIMS)
    n_fraud = round(n * 0.4)
    chosen = rng.sample(list(df.index[df["fraud_reported"] == "Y"]), n_fraud) + rng.sample(
        list(df.index[df["fraud_reported"] == "N"]), n - n_fraud
    )
    rng.shuffle(chosen)

    # Templates: score a pool cheaply (no AI) and take the riskiest, safest and most borderline.
    taken = set(chosen)
    pool = rng.sample([i for i in df.index if i not in taken], 80)
    scored = []
    for i in pool:
        result = fraud_node(_claim_from_row(df.loc[i]))
        if result.get("status") == "ok":
            scored.append((result["fraud_probability"], i))
    scored.sort()
    templates = [scored[-1][1], scored[0][1], min(scored, key=lambda t: abs(t[0] - 0.5))[1]] if scored else []
    templates = list(dict.fromkeys(templates))  # distinct, order kept

    pace = pace_s if pace_s is not None else (_PACE_S if config.get_groq_api_key() else 0.0)
    graph = build_graph()

    def run(idx):
        claim = _claim_from_row(df.loc[idx])
        state = graph.invoke({"claim": claim})
        if pace:
            time.sleep(pace)
        return claim, state

    now = datetime.now(UTC)
    ages = sorted((rng.uniform(0.2, 20.0) for _ in chosen), reverse=True)  # oldest first
    rows: list[dict] = []
    for k, (idx, age_days) in enumerate(zip(chosen, ages, strict=True)):
        claim, state = run(idx)
        created = now - timedelta(days=age_days)
        reviewed = min(created + timedelta(hours=rng.uniform(1.0, 30.0)), now - timedelta(minutes=10))
        outcome = "not_approved" if df.loc[idx, "fraud_reported"] == "Y" else "approved"
        rows.append(_row(claim, state, tag=claims_store.SEED, status="reviewed", created=created,
                         submitted_by=SAMPLE_CUSTOMERS[k % len(SAMPLE_CUSTOMERS)], reviewed=reviewed, outcome=outcome))
    for idx in templates:
        claim, state = run(idx)
        rows.append(_row(claim, state, tag=claims_store.TEMPLATE, status="pending", created=now,
                         submitted_by=claims_store.SAMPLE_SUBMITTER))
    return rows


def ensure_seed(pace_s: float | None = None) -> str:
    """Create the sample data once. Returns "exists" or "seeded".

    All rows are built in memory and inserted in one transaction, so a restart in the
    middle (free hosts sleep) writes nothing and the next start simply tries again.
    """
    if claims_store.count_tagged(claims_store.SEED) > 0:
        return "exists"
    rows = build_sample_rows(pace_s)
    claims_store.insert_many(rows)
    log.info("Seeded demo data", extra={"claims": len(rows)})
    return "seeded"
