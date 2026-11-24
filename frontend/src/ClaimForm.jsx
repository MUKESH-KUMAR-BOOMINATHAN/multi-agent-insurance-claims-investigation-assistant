import { useState } from "react";
import { submitClaim } from "./api";
import { DEFAULT_CLAIM } from "./claimFields";
import FieldGrid from "./FieldGrid";

// Organization form: all 32 model fields, grouped the way an adjuster thinks
// about a claim. The total is the sum of its three parts, as in the training data.
const GROUPS = [
  ["Policy", ["policy_state", "policy_csl", "policy_deductable", "policy_annual_premium", "umbrella_limit",
    "months_as_customer"]],
  ["Insured person", ["age", "insured_sex", "insured_education_level", "insured_occupation",
    "insured_relationship", "capital-gains", "capital-loss"]],
  ["Insured vehicle", ["auto_make", "auto_model", "auto_year"]],
  ["Incident", ["incident_type", "collision_type", "incident_severity", "incident_hour_of_the_day",
    "incident_state", "incident_city", "number_of_vehicles_involved", "bodily_injuries", "witnesses",
    "authorities_contacted", "police_report_available", "property_damage"]],
  ["Amounts claimed", ["injury_claim", "property_claim", "vehicle_claim"]],
];

export default function ClaimForm({ onSubmitted, onCancel }) {
  const [form, setForm] = useState(DEFAULT_CLAIM);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const update = (field, value) => setForm((prev) => ({ ...prev, [field]: value }));
  const total = (form.injury_claim || 0) + (form.property_claim || 0) + (form.vehicle_claim || 0);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const result = await submitClaim({ ...form, total_claim_amount: total });
      onSubmitted(result.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className="panel" onSubmit={handleSubmit}>
      <div className="panel-header">
        <h2>Claim details</h2>
        <button type="button" onClick={onCancel}>Cancel</button>
      </div>
      <p className="topbar-sub">
        Dropdowns list only the values the fraud model was trained on. Numbers outside the training range are
        accepted and routed to manual review.
      </p>

      {GROUPS.map(([title, fields]) => (
        <fieldset key={title} className="form-group">
          <legend>{title}</legend>
          <FieldGrid fields={fields} values={form} onChange={update} />
        </fieldset>
      ))}

      <div className="claim-total">
        <span>Total claim amount</span>
        <strong>${total.toLocaleString("en-US")}</strong>
      </div>

      {error && <p className="error" role="alert">{error}</p>}
      <button type="submit" className="primary-btn" disabled={submitting || total <= 0}>
        {submitting ? "Running the investigation pipeline…" : "Submit for investigation"}
      </button>
    </form>
  );
}
