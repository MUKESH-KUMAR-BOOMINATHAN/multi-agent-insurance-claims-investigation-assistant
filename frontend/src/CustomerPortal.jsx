import { useEffect, useState } from "react";
import { fileClaim, getMyPolicy, listMyClaims } from "./api";
import { DEFAULT_CLAIM, PORTFOLIO_URL, pick } from "./claimFields";
import FieldGrid from "./FieldGrid";
import ChangePassword from "./ChangePassword";

// What a customer is told about each status. The portal never shows the fraud
// score or the AI's reasoning (the API does not even send them).
const STATUS = {
  under_review: { label: "Under review", tone: "warn", text: "An investigator is reviewing your claim." },
  approved: { label: "Approved", tone: "good", text: "Your claim has been approved." },
  not_approved: { label: "Not approved", tone: "bad", text: "An investigator will contact you about next steps." },
  closed: { label: "Closed", tone: "muted", text: "This claim is closed." },
};

const GROUPS = [
  ["What happened", ["incident_type", "collision_type", "incident_severity", "incident_hour_of_the_day",
    "incident_state", "incident_city"]],
  ["Who and what was involved", ["number_of_vehicles_involved", "bodily_injuries", "witnesses",
    "authorities_contacted", "police_report_available", "property_damage"]],
  ["What you are claiming", ["injury_claim", "property_claim", "vehicle_claim"]],
];
const ALL_INCIDENT = GROUPS.flatMap(([, f]) => f);

const money = (n) => (n == null ? "—" : `$${Number(n).toLocaleString("en-US")}`);
const date = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" }) : "—");

// Filed -> Under review -> Decision, so the customer sees where their claim is.
function Tracker({ status }) {
  const decided = status !== "under_review";
  const steps = [
    ["Filed", "done"],
    ["Under review", decided ? "done" : "current"],
    [decided ? (STATUS[status] || STATUS.closed).label : "Decision", decided ? `done ${status}` : "todo"],
  ];
  return (
    <ol className="tracker" aria-label="Claim progress">
      {steps.map(([label, state]) => (
        <li key={label} className={`step ${state}`}>
          <span className="step-dot" aria-hidden="true" />
          <span className="step-label">{label}</span>
        </li>
      ))}
    </ol>
  );
}

function StatusBadge({ status }) {
  const s = STATUS[status] || STATUS.closed;
  return <span className={`status-pill pill-${s.tone}`}>{s.label}</span>;
}

function PolicyCard({ policy }) {
  if (!policy) return null;
  const items = [
    ["Policy state", policy.policy_state],
    ["Coverage limit", policy.policy_csl],
    ["Deductible", money(policy.policy_deductable)],
    ["Annual premium", money(policy.policy_annual_premium)],
    ["Insured vehicle", `${policy.auto_year} ${policy.auto_make} ${policy.auto_model}`],
    ["Customer for", `${policy.months_as_customer} months`],
  ];
  return (
    <div className="panel">
      <div className="panel-header"><h2>Your policy</h2></div>
      <div className="policy-grid">
        {items.map(([k, v]) => (
          <div key={k}>
            <div className="kpi-label">{k}</div>
            <div className="policy-value">{v}</div>
          </div>
        ))}
      </div>
      <p className="topbar-sub bar-note">Something wrong here? Contact your insurer before filing a claim.</p>
    </div>
  );
}

function IncidentForm({ onFiled, onCancel }) {
  const [form, setForm] = useState(() => pick(DEFAULT_CLAIM, ALL_INCIDENT));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const total = (form.injury_claim || 0) + (form.property_claim || 0) + (form.vehicle_claim || 0);

  const submit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      onFiled(await fileClaim(form));
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className="panel" onSubmit={submit}>
      <div className="panel-header">
        <h2>File a new claim</h2>
        <button type="button" onClick={onCancel}>Cancel</button>
      </div>
      {GROUPS.map(([title, fields]) => (
        <fieldset key={title} className="form-group">
          <legend>{title}</legend>
          <FieldGrid fields={fields} values={form} onChange={(f, v) => setForm((p) => ({ ...p, [f]: v }))} />
        </fieldset>
      ))}
      <div className="claim-total">
        <span>Total claim amount</span>
        <strong>{money(total)}</strong>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <button type="submit" className="primary-btn" disabled={submitting || total <= 0}>
        {submitting ? "Submitting…" : "Submit claim"}
      </button>
    </form>
  );
}

export default function CustomerPortal({ user, onSignOut }) {
  const [claims, setClaims] = useState([]);
  const [policy, setPolicy] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [filing, setFiling] = useState(false);
  const [justFiled, setJustFiled] = useState(null);
  const [changingPassword, setChangingPassword] = useState(false);

  const load = () =>
    Promise.all([listMyClaims(), getMyPolicy()])
      .then(([c, p]) => {
        setClaims(c);
        setPolicy(p);
        setError(null);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));

  useEffect(() => {
    load();
  }, []);

  const handleFiled = (claim) => {
    setFiling(false);
    setJustFiled(claim);
    setClaims((prev) => [claim, ...prev]);
  };

  const firstName = (user?.full_name || user?.username || "").split(" ")[0];

  return (
    <div className="portal">
      <header className="portal-bar">
        <div className="sidebar-brand portal-brand">
          <span className="brand-mark">CG</span>
          <div>
            <div className="brand-name">ClaimGuard</div>
            <div className="brand-sub">Customer portal</div>
          </div>
        </div>
        <div className="portal-user">
          <span>{user?.full_name || user?.username}</span>
          <button type="button" onClick={() => setChangingPassword((v) => !v)}>Change password</button>
          <button type="button" onClick={onSignOut}>Sign out</button>
        </div>
      </header>

      <main className="portal-main">
        <div className="topbar">
          <div>
            <h1>Hello, {firstName}</h1>
            <p className="topbar-sub">File a claim and follow its progress here.</p>
          </div>
          {!filing && (
            <button className="primary-btn" onClick={() => { setFiling(true); setJustFiled(null); }}>
              + File a new claim
            </button>
          )}
        </div>

        {changingPassword && (
          <div className="panel">
            <ChangePassword onCancel={() => setChangingPassword(false)} />
          </div>
        )}

        {error && <div className="panel"><p className="error" role="alert">{error}</p></div>}

        {justFiled && (
          <div className="notice notice-good" role="status">
            <strong>Claim #{justFiled.id} received.</strong> It is now under review; you will see updates on this page.
          </div>
        )}

        {filing ? (
          <IncidentForm onFiled={handleFiled} onCancel={() => setFiling(false)} />
        ) : (
          <>
            <div className="panel">
              <div className="panel-header"><h2>My claims</h2></div>
              {loading && <p className="topbar-sub">Loading…</p>}
              {!loading && claims.length === 0 && (
                <p className="empty">You have not filed any claims yet.</p>
              )}
              {claims.length > 0 && (
                <div className="my-claims">
                  {claims.map((c) => (
                    <div className="my-claim" key={c.id}>
                      <div>
                        <div className="my-claim-title">Claim #{c.id} · {c.incident_type || "Claim"}</div>
                        <div className="topbar-sub">
                          Filed {date(c.created_at)} · {money(c.total_claim_amount)}
                          {c.reviewed_at && ` · Decided ${date(c.reviewed_at)}`}
                        </div>
                        <div className="my-claim-text">{(STATUS[c.status] || STATUS.closed).text}</div>
                        <Tracker status={c.status} />
                      </div>
                      <StatusBadge status={c.status} />
                    </div>
                  ))}
                </div>
              )}
            </div>
            <PolicyCard policy={policy} />
          </>
        )}
      </main>
      <footer className="portal-foot">
        ClaimGuard · Built by Mukesh Kumar ·{" "}
        <a href={PORTFOLIO_URL} target="_blank" rel="noopener noreferrer">Portfolio ↗</a>
      </footer>
    </div>
  );
}
