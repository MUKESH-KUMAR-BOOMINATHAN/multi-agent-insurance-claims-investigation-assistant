export default function StatCards({ claims }) {
  const total = claims.length;
  const pending = claims.filter((c) => c.status === "pending").length;
  const flagged = claims.filter((c) => c.fraud_probability != null && c.fraud_probability >= 0.5).length;
  const scored = claims.filter((c) => c.fraud_probability != null);
  const avgProb = scored.length
    ? scored.reduce((sum, c) => sum + c.fraud_probability, 0) / scored.length
    : null;

  const stats = [
    { label: "Total Claims", value: total },
    { label: "Pending Review", value: pending },
    { label: "Flagged High-Risk", value: flagged, tone: "danger" },
    { label: "Avg Fraud Score", value: avgProb != null ? `${(avgProb * 100).toFixed(0)}%` : "—" },
  ];

  return (
    <div className="stat-row">
      {stats.map((s) => (
        <div className="stat-card" key={s.label}>
          <div className={`stat-value ${s.tone === "danger" ? "stat-value-danger" : ""}`}>{s.value}</div>
          <div className="stat-label">{s.label}</div>
        </div>
      ))}
    </div>
  );
}
