import { useEffect, useState } from "react";
import { getClaim, submitFeedback } from "./api";

const pct = (x, digits = 0) => `${(x * 100).toFixed(digits)}%`;
const money = (n) => (n == null ? "—" : `$${Number(n).toLocaleString("en-US")}`);
const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—");
const riskTone = (p) => (p >= 0.5 ? "bad" : p >= 0.3 ? "warn" : "good");
const riskLabel = (p) => (p >= 0.5 ? "High risk" : p >= 0.3 ? "Medium risk" : "Low risk");

const SOURCE_LABEL = {
  groq: (r) => `AI · Groq (${r.model})${r.cached ? " · cached" : ""}`,
  rules: () => "Rules (no AI needed)",
  rules_fallback: () => "Rules fallback (AI unavailable)",
};
const DECISIONS = [
  ["agreed", "Agree with the recommendation", "The advice matches what I found."],
  ["overridden_to_approve", "Override: approve", "I found no fraud; pay the claim."],
  ["overridden_to_investigate", "Override: investigate", "Something needs a closer look."],
];
const OUTCOMES = [
  ["N", "Not fraud", "The claim is genuine."],
  ["Y", "Confirmed fraud", "The claim is fraudulent."],
];
const FACTS = [
  ["Incident", (c) => c.incident_type],
  ["Severity", (c) => c.incident_severity],
  ["Collision", (c) => c.collision_type],
  ["When", (c) => (c.incident_hour_of_the_day != null ? `${String(c.incident_hour_of_the_day).padStart(2, "0")}:00` : null)],
  ["Where", (c) => [c.incident_city, c.incident_state].filter(Boolean).join(", ")],
  ["Authorities", (c) => c.authorities_contacted],
  ["Police report", (c) => c.police_report_available],
  ["Vehicles / injuries / witnesses",
    (c) => [c.number_of_vehicles_involved, c.bodily_injuries, c.witnesses].every((v) => v != null)
      ? `${c.number_of_vehicles_involved} / ${c.bodily_injuries} / ${c.witnesses}` : null],
  ["Injury / property / vehicle", (c) => `${money(c.injury_claim)} / ${money(c.property_claim)} / ${money(c.vehicle_claim)}`],
  ["Insured vehicle", (c) => [c.auto_year, c.auto_make, c.auto_model].filter(Boolean).join(" ")],
  ["Policy", (c) => [c.policy_state, c.policy_csl].filter(Boolean).join(" · ")],
  ["Customer for", (c) => (c.months_as_customer != null ? `${c.months_as_customer} months` : null)],
];

function Gauge({ value }) {
  const R = 46;
  const C = 2 * Math.PI * R;
  const tone = riskTone(value);
  return (
    <svg viewBox="0 0 120 120" className="gauge" role="img" aria-label={`Fraud probability ${pct(value, 1)}`}>
      <circle cx="60" cy="60" r={R} className="gauge-track" />
      <circle cx="60" cy="60" r={R} className={`gauge-fill c-${tone}`}
        strokeDasharray={`${value * C} ${C}`} transform="rotate(-90 60 60)" />
      <text x="60" y="58" textAnchor="middle" className="gauge-num">{pct(value)}</text>
      <text x="60" y="76" textAnchor="middle" className="gauge-cap">fraud risk</text>
    </svg>
  );
}

function ChoiceCards({ name, options, value, onChange }) {
  return (
    <div className="choice-cards" role="radiogroup" aria-label={name}>
      {options.map(([v, title, hint]) => (
        <label key={v} className={`choice ${value === v ? "chosen" : ""}`}>
          <input type="radio" name={name} value={v} checked={value === v} onChange={() => onChange(v)} />
          <span className="choice-title">{title}</span>
          <span className="choice-hint">{hint}</span>
        </label>
      ))}
    </div>
  );
}

export default function ClaimDetail({ claimId, onBack, onFeedbackSubmitted }) {
  const [claim, setClaim] = useState(null);
  const [error, setError] = useState(null);

  // No pre-selected answers: feedback becomes training data, so the investigator
  // must make an explicit choice (a default of "Not fraud" would bias the labels).
  const [decision, setDecision] = useState("");
  const [confirmedOutcome, setConfirmedOutcome] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);

  const load = () =>
    getClaim(claimId)
      .then((c) => {
        setClaim(c);
        setError(null);
      })
      .catch((e) => setError(e.message));

  useEffect(() => {
    load();
  }, [claimId]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSubmitFeedback = async () => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      await submitFeedback(claimId, decision, confirmedOutcome);
      await load();
      onFeedbackSubmitted?.();
    } catch (e) {
      setSubmitError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  if (error) return <div className="panel"><p className="error">{error}</p></div>;
  if (!claim) return <div className="panel"><p className="topbar-sub">Loading claim #{claimId}…</p></div>;

  const { fraud_result: fr, similar_claims: similar, similar_claims_summary: summary, recommendation: rec,
    status, warnings, claim: facts = {} } = claim;
  const scored = fr?.status === "ok";
  const prob = scored ? fr.fraud_probability : null;
  const fraudInSimilar = (similar || []).filter((c) => c.fraud_reported === "Y").length;
  const voteMatch = /^(\d+) of the (\d+)/.exec(summary || "");

  return (
    <div className="detail">
      <button type="button" className="back-link" onClick={onBack}>&larr; All claims</button>

      {/* ---- summary header ---- */}
      <div className="panel detail-head">
        <div>
          <div className="detail-title">
            {facts.incident_type || "Claim"} <span className="topbar-sub">· {facts.incident_severity}</span>
          </div>
          <div className="detail-meta">
            Submitted {when(claim.created_at)}{claim.submitted_by && <> by <strong>{claim.submitted_by}</strong></>}
            {claim.reviewed_at && <> · Reviewed {when(claim.reviewed_at)}</>}
          </div>
        </div>
        <div className="detail-head-right">
          <div className="detail-amount">{money(facts.total_claim_amount)}</div>
          <span className={`status-pill ${status === "reviewed" ? "pill-good" : "pill-warn"}`}>
            {status === "reviewed" ? "Reviewed" : "Pending review"}
          </span>
        </div>
      </div>

      {warnings?.length > 0 && (
        <div className="warning" role="status">
          <strong>Degraded mode:</strong>
          <ul>{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
        </div>
      )}

      <div className="detail-grid">
        {/* ---- left: evidence ---- */}
        <div className="detail-col">
          <div className="panel">
            <div className="card-title">Fraud model</div>
            {scored ? (
              <div className="score-row">
                <Gauge value={prob} />
                <div className="score-body">
                  <div className="pill-row">
                    <span className={`status-pill pill-${riskTone(prob)}`}>{riskLabel(prob)}</span>
                    {fr.requires_investigation && <span className="status-pill pill-bad">Flagged for investigation</span>}
                  </div>
                  <ul className="signal-list">
                    {fr.fraud_signals?.map((s, i) => <li key={i}>{s}</li>)}
                  </ul>
                </div>
              </div>
            ) : (
              <div className="manual-review">
                <span className="status-pill pill-warn">Manual review</span>
                <p>{fr?.fraud_signals?.[0] || "This claim could not be scored automatically."}</p>
              </div>
            )}
            {fr?.out_of_range_warnings?.length > 0 && (
              <div className="warning">
                <strong>Outside the training range:</strong>
                <ul>{fr.out_of_range_warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
              </div>
            )}
          </div>

          {similar?.length > 0 && (
            <div className="panel">
              <div className="card-title">Similar past claims</div>
              {voteMatch && (
                <div className="vote">
                  <div className="vote-text">
                    <strong>{voteMatch[1]} of {voteMatch[2]}</strong> most similar past claims were confirmed fraud
                  </div>
                  <div className="bar-track big">
                    <span className={`bar-fill ${riskTone(Number(voteMatch[1]) / Number(voteMatch[2]))}`}
                      style={{ width: `${(Number(voteMatch[1]) / Number(voteMatch[2])) * 100}%` }} />
                  </div>
                </div>
              )}
              <table className="similar-table">
                <thead>
                  <tr><th>Similarity</th><th>Outcome</th><th>Incident</th><th>Matched on</th></tr>
                </thead>
                <tbody>
                  {similar.map((c, i) => (
                    <tr key={i}>
                      <td>
                        <div className="sim">
                          <span>{c.score != null ? pct(c.score) : "—"}</span>
                          <span className="bar-track"><span className="bar-fill" style={{ width: `${(c.score || 0) * 100}%` }} /></span>
                        </div>
                      </td>
                      <td>
                        <span className={`badge ${c.fraud_reported === "Y" ? "badge-investigate" : "badge-ok"}`}>
                          {c.fraud_reported === "Y" ? "Fraud" : "Genuine"}
                        </span>
                      </td>
                      <td>{c.incident_type}<div className="matched">{c.incident_severity}</div></td>
                      <td className="matched">{c.matched_fields?.length ? c.matched_fields.join(", ") : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="topbar-sub bar-note">
                {fraudInSimilar} of the {similar.length} closest shown were fraud. Similarity is context for the
                investigator, not proof.
              </p>
            </div>
          )}

          <div className="panel">
            <div className="card-title">Claim facts</div>
            <dl className="facts">
              {FACTS.map(([label, get]) => {
                const v = get(facts);
                return v ? (
                  <div key={label}><dt>{label}</dt><dd>{v}</dd></div>
                ) : null;
              })}
            </dl>
          </div>
        </div>

        {/* ---- right: recommendation + decision ---- */}
        <div className="detail-col">
          {rec && (
            <div className="panel">
              <div className="card-title">Recommendation</div>
              <div className="rec-head">
                <span className={`rec-decision badge-rec-${rec.recommendation}`}>
                  {(rec.recommendation || "").replace(/_/g, " ")}
                </span>
                <span className={`badge badge-source-${rec.source || "unknown"}`}>
                  {(SOURCE_LABEL[rec.source] || (() => "Unknown source"))(rec)}
                </span>
              </div>
              {rec.confidence != null && (
                <div className="confidence">
                  <div className="progress-label"><span>Confidence</span><strong>{pct(rec.confidence)}</strong></div>
                  <div className="bar-track"><span className="bar-fill" style={{ width: pct(rec.confidence) }} /></div>
                </div>
              )}
              {rec.overrides?.length > 0 && (
                <p className="warning"><strong>Policy adjusted the AI output:</strong> {rec.overrides.join("; ")}</p>
              )}
              <div className="rec-section">Key factors</div>
              <ul className="signal-list">{rec.key_factors?.map((f, i) => <li key={i}>{f}</li>)}</ul>
              <div className="rec-section">Next steps</div>
              <ol className="steps">{rec.next_steps?.map((s, i) => <li key={i}>{s}</li>)}</ol>
            </div>
          )}

          <div className="panel">
            <div className="card-title">Investigator decision</div>
            {status === "reviewed" ? (
              <div className="decided">
                <span className={`status-pill ${claim.outcome === "not_approved" ? "pill-bad" : "pill-good"}`}>
                  {claim.outcome === "not_approved" ? "Confirmed fraud · not approved" : "Genuine · approved"}
                </span>
                <p className="topbar-sub">
                  Decided {when(claim.reviewed_at)}. The customer sees this outcome, and the confirmed claim now
                  informs future similar-claim evidence.
                </p>
              </div>
            ) : (
              <div className="decision-form">
                <div className="rec-section">Your assessment</div>
                <ChoiceCards name="decision" options={DECISIONS} value={decision} onChange={setDecision} />
                <div className="rec-section">Confirmed outcome</div>
                <ChoiceCards name="outcome" options={OUTCOMES} value={confirmedOutcome} onChange={setConfirmedOutcome} />
                <p className="topbar-sub">
                  Nothing is pre-selected on purpose: your answer becomes training data.
                </p>
                <button type="button" className="primary-btn" onClick={handleSubmitFeedback}
                  disabled={submitting || !decision || !confirmedOutcome}>
                  {submitting ? "Saving…" : "Record decision"}
                </button>
                {submitError && <p className="error">{submitError}</p>}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
