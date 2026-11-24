// Headline numbers above the claims list (same tile style as the Analytics page).
export default function StatCards({ claims }) {
  const scored = claims.filter((c) => c.fraud_probability != null);
  const pending = claims.filter((c) => c.status === "pending").length;
  const highPending = claims.filter((c) => c.status === "pending" && (c.fraud_probability ?? 0) >= 0.5).length;
  const avg = scored.length ? scored.reduce((s, c) => s + c.fraud_probability, 0) / scored.length : null;
  const reviewed = claims.length - pending;

  const tiles = [
    { label: "Total claims", value: claims.length, hint: `${scored.length} auto-scored` },
    { label: "Waiting for review", value: pending, hint: "pending decisions", tone: pending ? "warn" : "" },
    { label: "High risk, not yet reviewed", value: highPending, hint: "start with these", tone: highPending ? "bad" : "" },
    { label: "Average fraud score", value: avg != null ? `${(avg * 100).toFixed(0)}%` : "—", hint: `${reviewed} reviewed so far` },
  ];

  return (
    <div className="kpi-row kpi-4">
      {tiles.map((t) => (
        <div className={`kpi ${t.tone ? `kpi-${t.tone}` : ""}`} key={t.label}>
          <div className="kpi-label">{t.label}</div>
          <div className="kpi-value">{t.value}</div>
          <div className="kpi-hint">{t.hint}</div>
        </div>
      ))}
    </div>
  );
}
