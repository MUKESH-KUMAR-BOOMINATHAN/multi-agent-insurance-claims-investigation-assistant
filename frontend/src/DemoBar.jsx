import { useEffect, useState } from "react";
import { getDemoStatus } from "./api";

// Shown on every screen while someone is in the public demo: what this is, which role they
// are viewing as, how much of the free allowance is used, and how to leave or get more.
const ROLES = ["investigator", "customer", "admin"];
const INTRO = {
  investigator: "an investigator",
  customer: "a customer",
  admin: "the admin",
};

export default function DemoBar({ role, onSwitch, onExit, refreshKey }) {
  const [status, setStatus] = useState(null);
  const [switching, setSwitching] = useState(null); // the role being switched to
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

  const handleSwitch = async (target) => {
    setSwitching(target);
    setError(null);
    try {
      await onSwitch(target);
    } catch (e) {
      setError(e.message);
    } finally {
      setSwitching(null);
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
          You are exploring as <strong>{INTRO[role]}</strong>.{" "}
          {role === "admin"
            ? "This view is read-only: nothing you add is saved."
            : "Your data is private to you and cleared after 24 hours."}
        </span>
      </div>
      <div className="demo-bar-actions">
        {max !== null && role !== "admin" && (
          <span className={`demo-usage ${atLimit ? "demo-usage-full" : ""}`} title="Claims you have filed in this demo">
            Claims {used}/{max}
          </span>
        )}
        {ROLES.filter((r) => r !== role).map((r) => (
          <button type="button" key={r} onClick={() => handleSwitch(r)} disabled={switching !== null}>
            {switching === r ? "Switching…" : `Switch to ${r}`}
          </button>
        ))}
        {status?.contact_url && (
          <a href={status.contact_url} target="_blank" rel="noopener noreferrer">Contact Mukesh ↗</a>
        )}
        <button type="button" onClick={onExit}>Exit demo</button>
      </div>
      {error && <div className="demo-bar-error" role="alert">{error}</div>}
    </div>
  );
}
