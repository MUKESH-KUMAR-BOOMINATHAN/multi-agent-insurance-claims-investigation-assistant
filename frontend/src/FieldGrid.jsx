import { NUMERIC_FIELDS, OPTIONS, labelFor } from "./claimFields";

// A grid of inputs for `fields`: a dropdown when the field has OPTIONS, else a number.
export default function FieldGrid({ fields, values, onChange }) {
  return (
    <div className="form-grid">
      {fields.map((field) => (
        <label key={field} className="form-field">
          {labelFor(field)}
          {OPTIONS[field] ? (
            <select value={values[field]} onChange={(e) => onChange(field, e.target.value)}>
              {OPTIONS[field].map((opt) => (
                <option key={opt} value={opt}>{opt}</option>
              ))}
            </select>
          ) : (
            <input
              type="number"
              step="any"
              value={values[field]}
              onChange={(e) => onChange(field, NUMERIC_FIELDS.has(field) ? Number(e.target.value) : e.target.value)}
            />
          )}
        </label>
      ))}
    </div>
  );
}
