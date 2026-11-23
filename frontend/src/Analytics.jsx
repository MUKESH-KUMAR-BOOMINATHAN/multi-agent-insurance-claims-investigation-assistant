// Portfolio analytics dashboard, computed client-side from the claims list.
// Charts are plain SVG (no chart library): small, themeable, and print-sharp.

const pct = (n, total) => (total ? Math.round((n / total) * 100) : 0);
const money = (n) =>
  n >= 1_000_000 ? `$${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `$${Math.round(n / 1000)}K` : `$${Math.round(n)}`;

function countBy(items, keyFn) {
  const m = new Map();
  for (const it of items) {
    const k = keyFn(it) ?? "Unknown";
    m.set(k, (m.get(k) || 0) + 1);
  }
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

/* ---------- KPI tile ---------- */
function Kpi({ label, value, hint, tone }) {
  return (
    <div className={`kpi ${tone ? `kpi-${tone}` : ""}`}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {hint && <div className="kpi-hint">{hint}</div>}
    </div>
  );
}

/* ---------- Donut with centre label + legend ---------- */
function Donut({ slices, centerValue, centerLabel }) {
  const total = slices.reduce((s, x) => s + x.value, 0);
  const R = 52;
  const C = 2 * Math.PI * R;
  let offset = 0;
  return (
    <div className="donut-wrap">
      <svg viewBox="0 0 140 140" className="donut" role="img" aria-label={centerLabel}>
        <circle cx="70" cy="70" r={R} className="donut-track" />
        {total > 0 &&
          slices
            .filter((s) => s.value > 0)
            .map((s) => {
              const len = (s.value / total) * C;
              const el = (
                <circle
                  key={s.label}
                  cx="70"
                  cy="70"
                  r={R}
                  className={`donut-seg c-${s.tone}`}
                  strokeDasharray={`${Math.max(len - 2, 0)} ${C - Math.max(len - 2, 0)}`}
                  strokeDashoffset={-offset}
                />
              );
              offset += len;
              return el;
            })}
        <text x="70" y="68" textAnchor="middle" className="donut-num">{centerValue}</text>
        <text x="70" y="86" textAnchor="middle" className="donut-cap">{centerLabel}</text>
      </svg>
      <ul className="legend">
        {slices.map((s) => (
          <li key={s.label}>
            <span className={`dot d-${s.tone}`} />
            <span className="legend-label">{s.label}</span>
            <strong>{s.value}</strong>
            <span className="legend-pct">{pct(s.value, total)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ---------- Fraud-score histogram (10 bins, threshold marked) ---------- */
function ScoreHistogram({ scores }) {
  const bins = Array.from({ length: 10 }, () => 0);
  scores.forEach((p) => { bins[Math.min(9, Math.floor(p * 10))] += 1; });
  const max = Math.max(1, ...bins);
  const W = 460, H = 170, padB = 24, padT = 22, gap = 6;
  const bw = (W - gap * 9) / 10;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Distribution of fraud scores">
      {bins.map((n, i) => {
        const h = (n / max) * (H - padB - padT);
        const x = i * (bw + gap);
        const tone = i >= 5 ? "bad" : i >= 3 ? "warn" : "good";
        return (
          <g key={i}>
            <rect x={x} y={H - padB - h} width={bw} height={Math.max(h, n ? 3 : 0)} rx="4" className={`bar-svg b-${tone}`} />
            {n > 0 && <text x={x + bw / 2} y={H - padB - h - 5} textAnchor="middle" className="axis-num">{n}</text>}
            <text x={x + bw / 2} y={H - 6} textAnchor="middle" className="axis">{i * 10}%</text>
          </g>
        );
      })}
      <line x1={5 * (bw + gap) - gap / 2} x2={5 * (bw + gap) - gap / 2} y1={padT} y2={H - padB} className="threshold" />
      <text x={5 * (bw + gap) + 4} y={padT + 8} className="axis threshold-label">flag threshold 50%</text>
    </svg>
  );
}

/* ---------- Claims per day ---------- */
function Timeline({ claims }) {
  const byDay = new Map();
  claims.forEach((c) => {
    if (!c.created_at) return;
    const d = new Date(c.created_at);
    const key = d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
    const sort = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const cur = byDay.get(sort) || { key, n: 0, high: 0 };
    cur.n += 1;
    if ((c.fraud_probability ?? 0) >= 0.5) cur.high += 1;
    byDay.set(sort, cur);
  });
  const days = [...byDay.entries()].sort((a, b) => a[0] - b[0]).slice(-12).map(([, v]) => v);
  if (days.length < 2) return null;
  const max = Math.max(...days.map((d) => d.n));
  const W = 460, H = 170, padB = 24, padT = 22, gap = 8;
  const bw = Math.min(40, (W - gap * (days.length - 1)) / days.length);
  const total = days.length * bw + (days.length - 1) * gap;
  const x0 = (W - total) / 2;
  return (
    <div className="panel dash-card">
      <div className="dash-title">Claims over time <span>high-risk highlighted</span></div>
      <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Claims per day">
        {days.map((d, i) => {
          const x = x0 + i * (bw + gap);
          const h = (d.n / max) * (H - padB - padT);
          const hh = (d.high / max) * (H - padB - padT);
          return (
            <g key={d.key}>
              <rect x={x} y={H - padB - h} width={bw} height={h} rx="4" className="bar-svg b-good" />
              {d.high > 0 && <rect x={x} y={H - padB - hh} width={bw} height={hh} rx="4" className="bar-svg b-bad" />}
              <text x={x + bw / 2} y={H - padB - h - 5} textAnchor="middle" className="axis-num">{d.n}</text>
              <text x={x + bw / 2} y={H - 6} textAnchor="middle" className="axis">{d.key}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/* ---------- Horizontal bars ---------- */
function HBars({ rows, total }) {
  return (
    <div className="bars">
      {rows.map(([label, n, tone]) => (
        <div className="bar-row" key={label}>
          <span className="bar-label" title={label}>{label}</span>
          <span className="bar-track">
            <span className={`bar-fill ${tone || ""}`} style={{ width: `${pct(n, total)}%` }} />
          </span>
          <span className="bar-n">{n} <span className="bar-pct">({pct(n, total)}%)</span></span>
        </div>
      ))}
    </div>
  );
}

const SOURCE_LABELS = {
  groq: "AI (Groq)",
  rules_fallback: "Rules (AI unavailable)",
  rules: "Rules (unscorable)",
};

export default function Analytics({ claims }) {
  if (claims.length === 0) {
    return (
      <div className="panel">
        <p className="topbar-sub">Submit some claims and your analytics will appear here.</p>
      </div>
    );
  }

  const total = claims.length;
  const scored = claims.filter((c) => c.fraud_probability != null);
  const scores = scored.map((c) => c.fraud_probability);
  const avg = scores.length ? Math.round((scores.reduce((a, b) => a + b, 0) / scores.length) * 100) : null;
  const highClaims = scored.filter((c) => c.fraud_probability >= 0.5);
  const medClaims = scored.filter((c) => c.fraud_probability >= 0.3 && c.fraud_probability < 0.5);
  const lowClaims = scored.filter((c) => c.fraud_probability < 0.3);
  const reviewed = claims.filter((c) => c.status === "reviewed").length;
  const aiCount = claims.filter((c) => c.recommendation_source === "groq").length;
  const flaggedValue = highClaims.reduce((s, c) => s + (Number(c.total_claim_amount) || 0), 0);
  const totalValue = claims.reduce((s, c) => s + (Number(c.total_claim_amount) || 0), 0);

  const decisions = countBy(claims, (c) => c.recommendation).map(([k, n]) => [
    k, n, k === "approve" ? "good" : k === "investigate" ? "warn" : "bad",
  ]);
  const sources = countBy(claims, (c) => SOURCE_LABELS[c.recommendation_source] || "Not recorded");
  const types = countBy(claims, (c) => c.incident_type);

  // Average fraud score by severity: shows which kinds of incident look riskiest.
  const sevMap = new Map();
  scored.forEach((c) => {
    const k = c.incident_severity || "Unknown";
    const cur = sevMap.get(k) || { sum: 0, n: 0 };
    cur.sum += c.fraud_probability;
    cur.n += 1;
    sevMap.set(k, cur);
  });
  const severity = [...sevMap.entries()]
    .map(([k, v]) => [`${k} (${v.n})`, Math.round((v.sum / v.n) * 100)])
    .sort((a, b) => b[1] - a[1]);

  return (
    <div className="dash">
      <div className="kpi-row">
        <Kpi label="Claims analysed" value={total} hint={`${scored.length} auto-scored`} />
        <Kpi label="Average fraud score" value={avg != null ? `${avg}%` : "—"} hint="across scored claims" />
        <Kpi label="High-risk flagged" value={highClaims.length} hint={`${pct(highClaims.length, scored.length)}% of scored`} tone="bad" />
        <Kpi label="Value flagged" value={totalValue ? money(flaggedValue) : "—"} hint={totalValue ? `of ${money(totalValue)} claimed` : ""} tone="bad" />
        <Kpi label="Investigator reviewed" value={`${pct(reviewed, total)}%`} hint={`${reviewed} of ${total} claims`} tone="good" />
      </div>

      <div className="dash-grid">
        <div className="panel dash-card">
          <div className="dash-title">Fraud-risk mix <span>by model score</span></div>
          <Donut
            centerValue={scored.length}
            centerLabel="scored"
            slices={[
              { label: "Low risk (under 30%)", value: lowClaims.length, tone: "good" },
              { label: "Medium (30-50%)", value: medClaims.length, tone: "warn" },
              { label: "High (50%+)", value: highClaims.length, tone: "bad" },
            ]}
          />
        </div>

        <div className="panel dash-card">
          <div className="dash-title">Recommendations <span>what the system advised</span></div>
          <Donut
            centerValue={total}
            centerLabel="claims"
            slices={decisions.map(([label, value, tone]) => ({ label, value, tone }))}
          />
        </div>

        <div className="panel dash-card">
          <div className="dash-title">Fraud-score distribution <span>claims per 10% band</span></div>
          <ScoreHistogram scores={scores} />
        </div>

        <Timeline claims={claims} />

        <div className="panel dash-card">
          <div className="dash-title">Average risk by damage severity <span>higher = riskier</span></div>
          <HBars
            total={100}
            rows={severity.map(([l, n]) => [l, n, n >= 50 ? "bad" : n >= 30 ? "warn" : "good"])}
          />
        </div>

        <div className="panel dash-card">
          <div className="dash-title">Claims by incident type</div>
          <HBars rows={types} total={total} />
        </div>

        <div className="panel dash-card wide">
          <div className="dash-title">Human + AI workflow <span>{aiCount} AI-written</span></div>
          <HBars rows={sources} total={total} />
          <div className="progress-label">
            <span>Review progress</span>
            <strong>{pct(reviewed, total)}%</strong>
          </div>
          <div className="bar-track big"><span className="bar-fill good" style={{ width: `${pct(reviewed, total)}%` }} /></div>
        </div>
      </div>
    </div>
  );
}
