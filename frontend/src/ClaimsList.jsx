export default function ClaimsList({ claims, loading, error, onSelect, onRefresh }) {
  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Claims</h2>
        <button onClick={onRefresh}>Refresh</button>
      </div>

      {loading && <p>Loading claims...</p>}
      {error && <p className="error">{error}</p>}

      {!loading && !error && claims.length === 0 && (
        <p className="empty">No claims submitted yet.</p>
      )}

      {!loading && !error && claims.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Status</th>
              <th>Incident Type</th>
              <th>Fraud Probability</th>
              <th>AI Recommendation</th>
              <th>Submitted</th>
            </tr>
          </thead>
          <tbody>
            {claims.map((c) => (
              <tr key={c.id} onClick={() => onSelect(c.id)} className="clickable-row">
                <td>#{c.id}</td>
                <td>
                  <span className={`badge badge-${c.status}`}>{c.status}</span>
                </td>
                <td>{c.incident_type || "—"}</td>
                <td>
                  {c.fraud_probability != null
                    ? `${(c.fraud_probability * 100).toFixed(1)}%`
                    : "—"}
                </td>
                <td>
                  <span className={`badge badge-rec-${c.recommendation || "unknown"}`}>
                    {c.recommendation || "—"}
                  </span>
                </td>
                <td>{c.created_at ? new Date(c.created_at).toLocaleString() : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
