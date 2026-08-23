import { useEffect, useState } from "react";
import { getClaim, submitFeedback } from "./api";

export default function ClaimDetail({ claimId, onBack, onFeedbackSubmitted }) {
  const [claim, setClaim] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [decision, setDecision] = useState("agreed");
  const [confirmedOutcome, setConfirmedOutcome] = useState("N");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);

  const load = () => {
    setLoading(true);
    setError(null);
    getClaim(claimId)
      .then(setClaim)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, [claimId]);

  const handleSubmitFeedback = async () => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      await submitFeedback(claimId, decision, confirmedOutcome);
      load(); // refresh to show updated status
      onFeedbackSubmitted?.();
    } catch (e) {
      setSubmitError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) return <div className="panel"><p>Loading claim #{claimId}...</p></div>;
  if (error) return <div className="panel"><p className="error">{error}</p></div>;
  if (!claim) return null;

  const { fraud_result, similar_claims, recommendation, status } = claim;

  return (
    <div className="panel">
      <div className="panel-header">
        <button onClick={onBack}>&larr; Back to list</button>
        <span className={`badge badge-${status}`}>{status}</span>
      </div>

      <section>
        <h3>Fraud Model Result</h3>
        {fraud_result?.status === "ok" ? (
          <>
            <p>
              <strong>Fraud probability:</strong>{" "}
              {(fraud_result.fraud_probability * 100).toFixed(1)}%
              {fraud_result.requires_investigation && (
                <span className="badge badge-investigate"> Requires investigation</span>
              )}
            </p>
            <ul>
              {fraud_result.fraud_signals?.map((s, i) => <li key={i}>{s}</li>)}
            </ul>
            {fraud_result.out_of_range_warnings?.length > 0 && (
              <div className="warning">
                <strong>Warnings:</strong>
                <ul>
                  {fraud_result.out_of_range_warnings.map((w, i) => <li key={i}>{w}</li>)}
                </ul>
              </div>
            )}
          </>
        ) : (
          <p className="warning">
            Could not be auto-scored ({fraud_result?.status}). {fraud_result?.fraud_signals?.[0]}
          </p>
        )}
      </section>

      {similar_claims?.length > 0 && (
        <section>
          <h3>Similar Historical Claims</h3>
          <table>
            <thead>
              <tr>
                <th>Similarity</th>
                <th>Confirmed Fraud</th>
                <th>Incident Type</th>
                <th>Severity</th>
              </tr>
            </thead>
            <tbody>
              {similar_claims.map((c, i) => (
                <tr key={i}>
                  <td>{(c.score * 100).toFixed(1)}%</td>
                  <td>
                    <span className={c.fraud_reported === "Y" ? "badge badge-investigate" : "badge badge-ok"}>
                      {c.fraud_reported}
                    </span>
                  </td>
                  <td>{c.incident_type}</td>
                  <td>{c.incident_severity}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {recommendation && (
        <section>
          <h3>AI Recommendation</h3>
          <p>
            <span className={`badge badge-rec-${recommendation.recommendation}`}>
              {recommendation.recommendation}
            </span>{" "}
            (confidence: {(recommendation.confidence * 100).toFixed(0)}%)
          </p>
          <strong>Key factors:</strong>
          <ul>{recommendation.key_factors?.map((f, i) => <li key={i}>{f}</li>)}</ul>
          <strong>Next steps:</strong>
          <ul>{recommendation.next_steps?.map((s, i) => <li key={i}>{s}</li>)}</ul>
        </section>
      )}

      <section>
        <h3>Investigator Feedback</h3>
        {status === "reviewed" ? (
          <p>This claim has already been reviewed.</p>
        ) : (
          <div className="feedback-form">
            <label>
              Decision:
              <select value={decision} onChange={(e) => setDecision(e.target.value)}>
                <option value="agreed">Agreed with AI</option>
                <option value="overridden_to_approve">Overrode &rarr; Approve</option>
                <option value="overridden_to_investigate">Overrode &rarr; Investigate</option>
              </select>
            </label>
            <label>
              Confirmed outcome:
              <select value={confirmedOutcome} onChange={(e) => setConfirmedOutcome(e.target.value)}>
                <option value="N">Not fraud</option>
                <option value="Y">Confirmed fraud</option>
              </select>
            </label>
            <button onClick={handleSubmitFeedback} disabled={submitting}>
              {submitting ? "Submitting..." : "Submit Feedback"}
            </button>
            {submitError && <p className="error">{submitError}</p>}
          </div>
        )}
      </section>
    </div>
  );
}
