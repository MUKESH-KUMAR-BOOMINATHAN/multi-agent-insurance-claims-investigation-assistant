const API_BASE = "http://127.0.0.1:8000";

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ? JSON.stringify(body.detail) : `Request failed: ${res.status}`);
  }
  return res.json();
}

export function listClaims() {
  return request("/claims");
}

export function getClaim(id) {
  return request(`/claims/${id}`);
}

export function submitClaim(claim) {
  return request("/claims", {
    method: "POST",
    body: JSON.stringify(claim),
  });
}

export function submitFeedback(id, investigatorDecision, confirmedFraudReported) {
  return request(`/claims/${id}/feedback`, {
    method: "POST",
    body: JSON.stringify({
      investigator_decision: investigatorDecision,
      confirmed_fraud_reported: confirmedFraudReported,
    }),
  });
}
