import { useEffect, useState } from "react";
import { getDemoStatus } from "./api";

// Shown on every screen while someone is in the public demo: what this is, which role they
// are viewing as, how much of the free allowance is used, and how to leave or get more.
export default function DemoBar({ role, onSwitch, onExit, refreshKey }) {
  const [status, setStatus] = useState(null);
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    getDemoStatus()
      .then((s) => !cancelled && setStatus(s))
      .catch(() => {}); // the bar still works without the counter
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  const other = role === "investigator" ? "customer" : "investigator";

  const handleSwitch = async () => {
    setSwitching(true);
    setError(null);
    try {
      await onSwitch(other);
    } catch (e) {
      setError(e.message);
      setSwitching(false);
    }
  };

  const used = status?.claims_used ?? 0;
  const max = status?.claims_max ?? null;
  const atLimit = max !== null && used >= max;

  return (
    <div className="demo-bar" role="region" aria-label="Demo mode">
      <div className="demo-bar-main">
        <span className="demo-tag">DEMO</span>
        <span>
          You are exploring as a <strong>{role}</strong>. Your data is private to you and cleared after 24 hours.
        </span>
      </div>
      <div className="demo-bar-actions">
        {max !== null && (
          <span className={`demo-usage ${atLimit ? "demo-usage-full" : ""}`} title="Claims you have filed in this demo">
            Claims {used}/{max}
          </span>
        )}
        <button type="button" onClick={handleSwitch} disabled={switching}>
          {switching ? "Switching…" : `Switch to ${other}`}
        </button>
        {status?.contact_url && (
          <a href={status.contact_url} target="_blank" rel="noopener noreferrer">Contact Mukesh ↗</a>
        )}
        <button type="button" onClick={onExit}>Exit demo</button>
      </div>
      {error && <div className="demo-bar-error" role="alert">{error}</div>}
    </div>
  );
}
