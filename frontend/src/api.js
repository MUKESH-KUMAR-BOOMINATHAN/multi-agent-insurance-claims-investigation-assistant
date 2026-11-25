// Override with VITE_API_URL at build time (e.g. your Render URL on Vercel).
// Falls back to the local backend for development.
const API_BASE = (import.meta.env.VITE_API_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");

// The bearer token lives ONLY in this module's memory: never localStorage or
// sessionStorage, so an XSS bug cannot read it from storage. The cost is that a
// page refresh signs you out, which is the intended trade-off for an internal tool.
let accessToken = null;
let onUnauthorized = () => {};
let onDemoLimit = () => {};

export function setUnauthorizedHandler(handler) {
  onUnauthorized = handler;
}

// Called when the public demo's claim cap is reached, so the app can offer a next step.
export function setDemoLimitHandler(handler) {
  onDemoLimit = handler;
}

export class ApiError extends Error {
  constructor(message, extra = {}) {
    super(message);
    this.name = "ApiError";
    Object.assign(this, extra);
  }
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
  if (detail && typeof detail === "object" && typeof detail.message === "string") return detail.message;
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
    const detail = body.detail;
    if (detail && typeof detail === "object" && detail.code === "demo_limit") {
      const err = new ApiError(describeError(res.status, detail), {
        status: res.status,
        code: "demo_limit",
        kind: detail.kind,
        contactUrl: detail.contact_url,
      });
      // Only the claim cap is worth a dialog; "going too fast" is an inline message.
      if (detail.kind === "claims") onDemoLimit(err);
      throw err;
    }
    throw new ApiError(describeError(res.status, detail), { status: res.status });
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

// ---- Public demo -------------------------------------------------------------
// One click, no password: a private sandbox as an investigator or a customer.
export async function startDemo(role) {
  const body = await request("/demo/start", { method: "POST", body: JSON.stringify({ role }) });
  accessToken = body.access_token;
  return body;
}

// The same visitor and sandbox, seen as the other role.
export async function switchDemoRole(role) {
  const body = await request("/demo/switch", { method: "POST", body: JSON.stringify({ role }) });
  accessToken = body.access_token;
  return body;
}

export function getDemoStatus() {
  return request("/demo/status");
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
