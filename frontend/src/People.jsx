import { useEffect, useState } from "react";
import { createUser, getAuditLog, listUsers, resetUserPassword, setUserActive } from "./api";
import { DEFAULT_CLAIM, PROFILE_FIELDS, pick } from "./claimFields";
import FieldGrid from "./FieldGrid";

const EMPTY = { username: "", full_name: "", password: "", role: "customer" };
const date = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" }) : "—");

function NewAccountForm({ canCreateStaff, onCreated, onCancel }) {
  const [form, setForm] = useState(EMPTY);
  const [profile, setProfile] = useState(() => pick(DEFAULT_CLAIM, PROFILE_FIELDS));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const isCustomer = form.role === "customer";
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const submit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const body = { ...form, ...(isCustomer ? { profile } : {}) };
      onCreated(await createUser(body), form.password);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className="panel" onSubmit={submit}>
      <div className="panel-header">
        <h2>New account</h2>
        <button type="button" onClick={onCancel}>Cancel</button>
      </div>

      <div className="form-grid">
        <label className="form-field">
          Account type
          <select value={form.role} onChange={set("role")} disabled={!canCreateStaff}>
            <option value="customer">Customer (files claims)</option>
            {canCreateStaff && <option value="investigator">Investigator (staff)</option>}
          </select>
        </label>
        <label className="form-field">
          Full name
          <input value={form.full_name} onChange={set("full_name")} maxLength={100} required />
        </label>
        <label className="form-field">
          Username
          <input
            value={form.username}
            onChange={set("username")}
            minLength={3}
            maxLength={50}
            pattern="[A-Za-z0-9][A-Za-z0-9._@\-]*"
            title="3-50 characters: letters, numbers, dot, underscore, @ or hyphen"
            autoComplete="off"
            required
          />
        </label>
        <label className="form-field">
          Temporary password (10+ characters)
          <input
            type="text"
            value={form.password}
            onChange={set("password")}
            minLength={10}
            maxLength={200}
            autoComplete="new-password"
            required
          />
        </label>
      </div>

      {isCustomer && (
        <fieldset className="form-group">
          <legend>Policy details on file</legend>
          <p className="topbar-sub">
            Recorded once by the organization. The customer only describes the incident when filing; these
            details are added automatically.
          </p>
          <FieldGrid
            fields={PROFILE_FIELDS}
            values={profile}
            onChange={(f, v) => setProfile((p) => ({ ...p, [f]: v }))}
          />
        </fieldset>
      )}

      {error && <p className="error" role="alert">{error}</p>}
      <button type="submit" className="primary-btn" disabled={submitting} style={{ marginTop: 18 }}>
        {submitting ? "Creating…" : "Create account"}
      </button>
    </form>
  );
}

function ResetPassword({ user, onDone }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const submit = async (e) => {
    e.preventDefault();
    try {
      await resetUserPassword(user.id, password);
      onDone(`Password for ${user.username} was reset (and the account unlocked). Share it securely; they must change it at their next sign-in.`);
    } catch (err) {
      setError(err.message);
    }
  };
  return (
    <form className="inline-form" onSubmit={submit}>
      <input
        type="text"
        placeholder="New password (10+ characters)"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        minLength={10}
        required
      />
      <button type="submit" className="primary-btn">Save</button>
      <button type="button" onClick={() => onDone(null)}>Cancel</button>
      {error && <span className="error">{error}</span>}
    </form>
  );
}

const ACTIONS = {
  login: "Signed in",
  login_failed: "Failed sign-in",
  login_locked: "Account locked (too many failed sign-ins)",
  password_changed: "Changed own password",
  password_change_failed: "Failed password change",
  password_reset: "Reset a password",
  account_created: "Created an account",
  account_deactivated: "Deactivated an account",
  account_activated: "Activated an account",
  claim_filed: "Filed a claim",
  claim_submitted: "Submitted a claim",
  claim_reviewed: "Reviewed a claim",
};
const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—");

// Admin only: who did what. The API never records passwords, tokens or claim contents.
function ActivityLog() {
  const [events, setEvents] = useState(null);
  const [error, setError] = useState(null);
  const load = () => getAuditLog(200).then(setEvents).catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);
  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Activity log</h2>
        <button type="button" onClick={load}>Refresh</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {!events && !error && <p className="topbar-sub">Loading…</p>}
      {events && events.length === 0 && <p className="empty">No activity yet.</p>}
      {events && events.length > 0 && (
        <table>
          <thead>
            <tr><th>When</th><th>Who</th><th>What</th><th>Target</th><th>IP</th></tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <tr key={e.id}>
                <td>{when(e.at)}</td>
                <td>{e.actor || "—"}</td>
                <td className={e.action.startsWith("login_") || e.action.endsWith("_failed") ? "stat-value-danger" : ""}>
                  {ACTIONS[e.action] || e.action}
                </td>
                <td>{e.target || "—"}</td>
                <td className="matched">{e.ip || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function People({ currentUser }) {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [creating, setCreating] = useState(false);
  const [resetting, setResetting] = useState(null);
  const [notice, setNotice] = useState(null);
  const isAdmin = currentUser?.role === "admin";

  const load = () =>
    listUsers()
      .then((u) => {
        setUsers(u);
        setError(null);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));

  useEffect(() => {
    load();
  }, []);

  const handleCreated = (user, password) => {
    setCreating(false);
    setUsers((prev) => [user, ...prev]);
    setNotice(
      `Account created. Send ${user.full_name} their sign-in details securely: username "${user.username}", ` +
        `temporary password "${password}". They will choose their own password when they first sign in.`,
    );
  };

  const toggleActive = async (user) => {
    try {
      const updated = await setUserActive(user.id, !user.active);
      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
    } catch (e) {
      setError(e.message);
    }
  };

  const customers = users.filter((u) => u.role === "customer").length;
  const staff = users.length - customers;

  return (
    <>
      <div className="stat-row people-stats">
        <div className="stat-card">
          <div className="stat-value">{customers}</div>
          <div className="stat-label">Customer accounts</div>
        </div>
        {isAdmin && (
          <div className="stat-card">
            <div className="stat-value">{staff}</div>
            <div className="stat-label">Investigators</div>
          </div>
        )}
        <div className="stat-card">
          <div className="stat-value">{users.filter((u) => u.active).length}</div>
          <div className="stat-label">Active accounts</div>
        </div>
      </div>

      {notice && (
        <div className="notice notice-good" role="status">
          {notice} <button type="button" className="link-btn" onClick={() => setNotice(null)}>Dismiss</button>
        </div>
      )}

      {creating ? (
        <NewAccountForm canCreateStaff={isAdmin} onCreated={handleCreated} onCancel={() => setCreating(false)} />
      ) : (
        <div className="panel">
          <div className="panel-header">
            <h2>{isAdmin ? "Investigators and customers" : "Customers"}</h2>
            <button type="button" className="primary-btn" onClick={() => { setCreating(true); setNotice(null); }}>
              + New account
            </button>
          </div>
          {loading && <p className="topbar-sub">Loading…</p>}
          {error && <p className="error" role="alert">{error}</p>}
          {!loading && users.length === 0 && <p className="empty">No accounts yet. Create the first one.</p>}
          {users.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Username</th>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id}>
                    <td>{u.full_name}</td>
                    <td>{u.username}</td>
                    <td><span className={`badge role-${u.role}`}>{u.role}</span></td>
                    <td>
                      <span className={`badge ${u.active ? "badge-reviewed" : "badge-pending"}`}>
                        {u.active ? "active" : "deactivated"}
                      </span>
                    </td>
                    <td>{date(u.created_at)}</td>
                    <td>
                      <div className="row-actions">
                      {resetting === u.id ? (
                        <ResetPassword user={u} onDone={(msg) => { setResetting(null); if (msg) setNotice(msg); }} />
                      ) : (
                        <>
                          <button type="button" onClick={() => setResetting(u.id)}>Reset password</button>
                          <button type="button" onClick={() => toggleActive(u)}>
                            {u.active ? "Deactivate" : "Activate"}
                          </button>
                        </>
                      )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {isAdmin && !creating && <ActivityLog />}
    </>
  );
}
