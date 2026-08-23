import { useState } from "react";
import { submitClaim } from "./api";

// Mirrors agents/fraud_node.py's ENCODINGS dict exactly — these are the
// ONLY values the model was trained on. Typing anything else (as in the
// raw Swagger form) throws an UnrecognizedCategoryError server-side.
const OPTIONS = {
  policy_state: ["IL", "IN", "OH"],
  policy_csl: ["100/300", "250/500", "500/1000"],
  insured_sex: ["FEMALE", "MALE"],
  insured_education_level: ["Associate", "College", "High School", "JD", "MD", "Masters", "PhD"],
  insured_occupation: [
    "adm-clerical", "armed-forces", "craft-repair", "exec-managerial", "farming-fishing",
    "handlers-cleaners", "machine-op-inspct", "other-service", "priv-house-serv",
    "prof-specialty", "protective-serv", "sales", "tech-support", "transport-moving",
  ],
  insured_relationship: ["husband", "not-in-family", "other-relative", "own-child", "unmarried", "wife"],
  incident_type: ["Multi-vehicle Collision", "Parked Car", "Single Vehicle Collision", "Vehicle Theft"],
  collision_type: ["Front Collision", "Not Applicable", "Rear Collision", "Side Collision"],
  incident_severity: ["Major Damage", "Minor Damage", "Total Loss", "Trivial Damage"],
  authorities_contacted: ["Ambulance", "Fire", "None", "Other", "Police"],
  incident_state: ["NC", "NY", "OH", "PA", "SC", "VA", "WV"],
  incident_city: ["Arlington", "Columbus", "Hillsdale", "Northbend", "Northbrook", "Riverwood", "Springfield"],
  property_damage: ["NO", "Unknown", "YES"],
  police_report_available: ["NO", "Unknown", "YES"],
  auto_make: [
    "Accura", "Audi", "BMW", "Chevrolet", "Dodge", "Ford", "Honda", "Jeep",
    "Mercedes", "Nissan", "Saab", "Suburu", "Toyota", "Volkswagen",
  ],
  auto_model: [
    "3 Series", "92x", "93", "95", "A3", "A5", "Accord", "C300", "CRV", "Camry", "Civic",
    "Corolla", "E400", "Escape", "F150", "Forrestor", "Fusion", "Grand Cherokee", "Highlander",
    "Impreza", "Jetta", "Legacy", "M5", "MDX", "ML350", "Malibu", "Maxima", "Neon", "Passat",
    "Pathfinder", "RAM", "RSX", "Silverado", "TL", "Tahoe", "Ultima", "Wrangler", "X5", "X6",
  ],
};

const DEFAULT_CLAIM = {
  months_as_customer: 200,
  age: 40,
  policy_state: "OH",
  policy_csl: "250/500",
  policy_deductable: 1000,
  policy_annual_premium: 1250,
  umbrella_limit: 0,
  insured_sex: "MALE",
  insured_education_level: "College",
  insured_occupation: "craft-repair",
  insured_relationship: "husband",
  "capital-gains": 0,
  "capital-loss": 0,
  incident_type: "Single Vehicle Collision",
  collision_type: "Side Collision",
  incident_severity: "Minor Damage",
  authorities_contacted: "Police",
  incident_state: "OH",
  incident_city: "Columbus",
  incident_hour_of_the_day: 12,
  number_of_vehicles_involved: 1,
  property_damage: "NO",
  bodily_injuries: 0,
  witnesses: 1,
  police_report_available: "YES",
  total_claim_amount: 30000,
  injury_claim: 3000,
  property_claim: 6000,
  vehicle_claim: 21000,
  auto_make: "Honda",
  auto_model: "Civic",
  auto_year: 2015,
};

const NUMERIC_FIELDS = new Set([
  "months_as_customer", "age", "policy_deductable", "policy_annual_premium", "umbrella_limit",
  "capital-gains", "capital-loss", "incident_hour_of_the_day", "number_of_vehicles_involved",
  "bodily_injuries", "witnesses", "total_claim_amount", "injury_claim", "property_claim",
  "vehicle_claim", "auto_year",
]);

const FIELD_LABELS = {
  "capital-gains": "Capital Gains",
  "capital-loss": "Capital Loss",
};

function labelFor(field) {
  return FIELD_LABELS[field] || field.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function ClaimForm({ onSubmitted, onCancel }) {
  const [form, setForm] = useState(DEFAULT_CLAIM);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const update = (field, rawValue) => {
    const value = NUMERIC_FIELDS.has(field) ? Number(rawValue) : rawValue;
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const result = await submitClaim(form);
      onSubmitted(result.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  const allFields = Object.keys(DEFAULT_CLAIM);

  return (
    <div className="panel">
      <div className="panel-header">
        <button type="button" onClick={onCancel}>&larr; Cancel</button>
        <h2>Submit New Claim</h2>
      </div>

      <form onSubmit={handleSubmit}>
        <div className="form-grid">
          {allFields.map((field) => (
            <label key={field} className="form-field">
              {labelFor(field)}
              {OPTIONS[field] ? (
                <select value={form[field]} onChange={(e) => update(field, e.target.value)}>
                  {OPTIONS[field].map((opt) => (
                    <option key={opt} value={opt}>{opt}</option>
                  ))}
                </select>
              ) : (
                <input
                  type="number"
                  step="any"
                  value={form[field]}
                  onChange={(e) => update(field, e.target.value)}
                />
              )}
            </label>
          ))}
        </div>

        {error && <p className="error" style={{ marginTop: 16 }}>{error}</p>}

        <button type="submit" className="primary-btn" disabled={submitting} style={{ marginTop: 20 }}>
          {submitting ? "Running investigation pipeline..." : "Submit for Investigation"}
        </button>
      </form>
    </div>
  );
}
