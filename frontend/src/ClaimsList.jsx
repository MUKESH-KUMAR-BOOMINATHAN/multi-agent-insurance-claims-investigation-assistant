import { useState } from "react";
import { loadPrefs } from "./prefs";

const FILTERS = [
  ["all", "All", () => true],
  ["pending", "Pending review", (c) => c.status === "pending"],
  ["high", "High risk", (c) => (c.fraud_probability ?? 0) >= 0.5],
  ["manual", "Manual review", (c) => c.fraud_probability == null],
  ["reviewed", "Reviewed", (c) => c.status === "reviewed"],
];
const riskTone = (p) => (p >= 0.5 ? "bad" : p >= 0.3 ? "warn" : "good");
const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—");

export default function ClaimsList({ claims, loading, error, onSelect, onRefresh }) {
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const perPage = loadPrefs().rowsPerPage;

  const q = query.trim().toLowerCase();
  const matches = (c) =>
    !q ||
    `#${c.id}`.includes(q) ||
    (c.incident_type || "").toLowerCase().includes(q) ||
    (c.submitted_by || "").toLowerCase().includes(q) ||
    (c.recommendation || "").toLowerCase().includes(q);
  const test = FILTERS.find(([k]) => k === filter)[2];
  const visible = claims.filter((c) => test(c) && matches(c));
  const shown = visible.slice(0, perPage);

  return (
    <div className="panel">
      <div className="panel-header list-header">
        <div className="chips" role="tablist" aria-label="Filter claims">
          {FILTERS.map(([key, label, fn]) => (
            <button key={key} type="button" role="tab" aria-selected={filter === key}
              className={`chip ${filter === key ? "chip-on" : ""}`} onClick={() => setFilter(key)}>
              {label} <span className="chip-n">{claims.filter(fn).length}</span>
            </button>
          ))}
        </div>
        <div className="list-tools">
          <input className="search" type="search" placeholder="Search id, type, customer…"
            value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search claims" />
          <button type="button" onClick={onRefresh}>Refresh</button>
        </div>
      </div>

      {loading && <p className="topbar-sub">Loading claims…</p>}
      {error && <p className="error">{error}</p>}

      {!loading && !error && claims.length === 0 && <p className="empty">No claims submitted yet.</p>}
      {!loading && !error && claims.length > 0 && visible.length === 0 && (
        <p className="empty">No claims match this filter.</p>
      )}

      {!loading && !error && shown.length > 0 && (
        <table className="claims-table">
          <thead>
            <tr>
              <th>Claim</th>
              <th>Incident</th>
              <th>Fraud risk</th>
              <th>Recommendation</th>
              <th>Submitted by</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((c) => (
              <tr key={c.id} onClick={() => onSelect(c.id)} className="clickable-row"
                tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onSelect(c.id)}>
                <td>
                  <div className="cell-strong">#{c.id}</div>
                  <div className="matched">{when(c.created_at)}</div>
                </td>
                <td>
                  <div>{c.incident_type || "—"}</div>
                  <div className="matched">
                    {c.incident_severity || ""}
                    {c.total_claim_amount != null && ` · $${Number(c.total_claim_amount).toLocaleString("en-US")}`}
                  </div>
                </td>
                <td>
                  {c.fraud_probability != null ? (
                    <div className="risk-cell">
                      <span className={`risk-num t-${riskTone(c.fraud_probability)}`}>
                        {(c.fraud_probability * 100).toFixed(0)}%
                      </span>
                      <span className="bar-track">
                        <span className={`bar-fill ${riskTone(c.fraud_probability)}`}
                          style={{ width: `${c.fraud_probability * 100}%` }} />
                      </span>
                    </div>
                  ) : (
                    <span className="badge badge-pending">manual review</span>
                  )}
                </td>
                <td>
                  <span className={`badge badge-rec-${c.recommendation || "unknown"}`}>
                    {(c.recommendation || "—").replace(/_/g, " ")}
                  </span>
                </td>
                <td>{c.submitted_by || "—"}</td>
                <td>
                  <span className={`badge badge-${c.status}`}>{c.status}</span>
                  {c.outcome && <div className="matched">{c.outcome === "approved" ? "approved" : "not approved"}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {visible.length > perPage && (
        <p className="topbar-sub bar-note">
          Showing {perPage} of {visible.length} claims (change the page size in Settings).
        </p>
      )}
    </div>
  );
}
