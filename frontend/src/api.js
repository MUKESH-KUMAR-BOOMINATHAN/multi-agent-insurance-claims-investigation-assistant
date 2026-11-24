// Override with VITE_API_URL at build time (e.g. your Render URL on Vercel).
// Falls back to the local backend for development.
const API_BASE = (import.meta.env.VITE_API_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");

// The bearer token lives ONLY in this module's memory: never localStorage or
// sessionStorage, so an XSS bug cannot read it from storage. The cost is that a
// page refresh signs you out, which is the intended trade-off for an internal tool.
let accessToken = null;
let onUnauthorized = () => {};

export function setUnauthorizedHandler(handler) {
  onUnauthorized = handler;
}

// Display-only: reads the username and expiry from the token payload. The server
// still verifies the signature on every request, so this is never trusted for access.
export function getSession() {
  if (!accessToken) return null;
  try {
    const b64 = accessToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(atob(b64));
    return { username: payload.sub, expiresAt: payload.exp * 1000 };
  } catch {
    return null;
  }
}

export function hasToken() {
  return accessToken !== null;
}

// Turn FastAPI's error payloads into a readable sentence. A 422 detail is a
// list of {loc, msg}; everything else is a plain string.
function describeError(status, detail) {
  if (Array.isArray(detail)) {
    return detail
      .map((d) => `${(d.loc || []).filter((p) => p !== "body").join(".")}: ${d.msg}`)
      .join("; ");
  }
  if (typeof detail === "string") return detail;
  if (status === 429) return "Too many requests — please wait a moment and try again.";
  return `Request failed (${status})`;
}

async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json" };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;

  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  } catch {
    throw new Error("Cannot reach the API. If the server was idle it may be starting up — try again in a minute.");
  }

  if (res.status === 401 && accessToken) {
    // Expired or revoked token: drop it and send the user back to the login screen.
    accessToken = null;
    onUnauthorized();
    throw new Error("Your session expired. Please sign in again.");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(describeError(res.status, body.detail));
  }
  if (res.status === 204) return null;
  return res.json();
}

export function getHealth() {
  return request("/health");
}

export function getAuthConfig() {
  return request("/auth/config");
}

// Returns the signed-in account: { username, role, full_name }.
export async function login(username, password) {
  const body = await request("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  accessToken = body.access_token;
  return body.user;
}

export function getMe() {
  return request("/auth/me");
}

export function logout() {
  accessToken = null;
}

export function listClaims() {
  return request("/claims?limit=500");
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

// ---- Customer portal -------------------------------------------------------
export function getMyPolicy() {
  return request("/my/policy");
}

export function listMyClaims() {
  return request("/my/claims");
}

export function fileClaim(incident) {
  return request("/my/claims", { method: "POST", body: JSON.stringify(incident) });
}

// ---- Account management (organization) -------------------------------------
export function listUsers() {
  return request("/users");
}

export function createUser(user) {
  return request("/users", { method: "POST", body: JSON.stringify(user) });
}

export function setUserActive(id, active) {
  return request(`/users/${id}/active`, { method: "POST", body: JSON.stringify({ active }) });
}

export function resetUserPassword(id, password) {
  return request(`/users/${id}/password`, { method: "POST", body: JSON.stringify({ password }) });
}

// ---- Own account -----------------------------------------------------------
export function changePassword(currentPassword, newPassword) {
  return request("/auth/password", {
    method: "POST",
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
}

// ---- Admin -------------------------------------------------------------------
export function getAuditLog(limit = 200) {
  return request(`/audit?limit=${limit}`);
}
