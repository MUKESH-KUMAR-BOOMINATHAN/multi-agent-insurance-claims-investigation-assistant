import { useEffect, useState } from "react";
import { getHealth, getSession } from "./api";
import { applyTheme, loadPrefs, savePrefs } from "./prefs";

function Row({ label, value, tone }) {
  return (
    <div className="settings-row">
      <span>{label}</span>
      <strong className={tone === "bad" ? "stat-value-danger" : ""}>{value}</strong>
    </div>
  );
}

function minutesLeft(expiresAt) {
  const mins = Math.max(0, Math.round((expiresAt - Date.now()) / 60000));
  return mins >= 60 ? `${Math.floor(mins / 60)} h ${mins % 60} min` : `${mins} min`;
}

function UserSpace({ authRequired, onSignOut }) {
  const [prefs, setPrefs] = useState(loadPrefs);
  const [saved, setSaved] = useState(false);
  const session = getSession();

  const update = (patch) => {
    setPrefs((p) => ({ ...p, ...patch }));
    setSaved(false);
  };

  const save = () => {
    savePrefs(prefs);
    applyTheme(prefs.theme);
    setSaved(true);
  };

  const shownName = prefs.displayName.trim() || session?.username || "Investigator";
  const initials = shownName.slice(0, 2).toUpperCase();

  return (
    <div className="panel">
      <div className="panel-header"><h2>Your space</h2></div>

      <div className="profile">
        <span className="avatar">{initials}</span>
        <div>
          <div className="profile-name">{shownName}</div>
          <div className="topbar-sub">
            {authRequired ? `Signed in as ${session?.username ?? "operator"}` : "Local mode (sign-in disabled)"}
          </div>
        </div>
      </div>

      <div className="form-grid">
        <label className="form-field">
          <span>Display name</span>
          <input
            type="text"
            maxLength={40}
            value={prefs.displayName}
            placeholder={session?.username ?? "Your name"}
            onChange={(e) => update({ displayName: e.target.value })}
          />
        </label>

        <label className="form-field">
          <span>Theme</span>
          <select value={prefs.theme} onChange={(e) => update({ theme: e.target.value })}>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
        </label>

        <label className="form-field">
          <span>Claims per page</span>
          <select value={prefs.rowsPerPage} onChange={(e) => update({ rowsPerPage: Number(e.target.value) })}>
            {[10, 25, 50].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
      </div>

      <div className="settings-actions">
        <button type="button" className="primary-btn" onClick={save}>Save preferences</button>
        {saved && <span className="saved-note" role="status">Saved</span>}
      </div>
      <p className="topbar-sub bar-note">Preferences are stored in this browser only.</p>

      {authRequired && session && (
        <>
          <div className="panel-header settings-sub"><h2>Session</h2></div>
          <Row label="User" value={session.username} />
          <Row label="Session expires in" value={minutesLeft(session.expiresAt)} />
          <div className="settings-actions">
            <button type="button" onClick={onSignOut}>Sign out</button>
          </div>
        </>
      )}
    </div>
  );
}

// Read-only system status from the public /health endpoint (no secrets are exposed there).
export default function Settings({ authRequired, onSignOut }) {
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getHealth().then(setHealth).catch((e) => setError(e.message));
  }, []);

  const llm = health?.llm || {};
  const budget = llm.budget || {};
  const retrieval = health?.retrieval || {};
  const yesNo = (v) => (v ? "Yes" : "No");

  return (
    <>
      <UserSpace authRequired={authRequired} onSignOut={onSignOut} />

      {error && <div className="panel"><p className="error" role="alert">{error}</p></div>}
      {!error && !health && <div className="panel"><p className="topbar-sub">Loading system status…</p></div>}

      {health && (
        <div className="panel-grid">
          <div className="panel">
            <div className="panel-header"><h2>Services</h2></div>
            <Row label="API" value={health.status === "ok" ? "Online" : health.status} />
            <Row
              label="Fraud model"
              value={health.fraud_model_loaded ? "Loaded" : "Unavailable"}
              tone={health.fraud_model_loaded ? "" : "bad"}
            />
            <Row
              label="Similar-claims index"
              value={retrieval.ready ? `Ready (${retrieval.documents} claims)` : "Warming up"}
              tone={retrieval.ready ? "" : "bad"}
            />
            <Row label="Sign-in required" value={yesNo(authRequired)} />
          </div>

          <div className="panel">
            <div className="panel-header"><h2>AI (Groq)</h2></div>
            <Row label="API key configured" value={yesNo(llm.configured)} tone={llm.configured ? "" : "bad"} />
            <Row label="Model" value={llm.model ?? "—"} />
            <Row label="Circuit breaker" value={llm.circuit ?? "—"} tone={llm.circuit === "open" ? "bad" : ""} />
            <Row label="Calls this minute" value={`${budget.used_last_minute ?? 0} / ${budget.limit_per_minute ?? "—"}`} />
            <Row label="Calls today" value={`${budget.used_last_day ?? 0} / ${budget.limit_per_day ?? "—"}`} />
          </div>
        </div>
      )}
    </>
  );
}
