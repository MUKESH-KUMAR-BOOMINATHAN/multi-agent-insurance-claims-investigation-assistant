import { useEffect, useRef, useState } from "react";
import { getDemoStatus } from "./api";

// Shown on every screen while someone is in the public demo: what this is, which role they
// are viewing as, how much of the free allowance is used, and how to leave or get more.
// On phones the actions collapse into a "Menu" dropdown so the bar stays one line tall.
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
  const [open, setOpen] = useState(false); // phone-only dropdown
  const ref = useRef(null);

  useEffect(() => {
    let cancelled = false;
    getDemoStatus()
      .then((s) => !cancelled && setStatus(s))
      .catch(() => {}); // the bar still works without the counter
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  // Close the dropdown on an outside tap or Escape.
  useEffect(() => {
    if (!open) return;
    const away = (e) => ref.current && !ref.current.contains(e.target) && setOpen(false);
    const esc = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  const handleSwitch = async (target) => {
    setSwitching(target);
    setError(null);
    try {
      await onSwitch(target);
      setOpen(false);
    } catch (e) {
      setError(e.message);
    } finally {
      setSwitching(null);
    }
  };

  const used = status?.claims_used ?? 0;
  const max = status?.claims_max ?? null;
  const atLimit = max !== null && used >= max;
  const showUsage = max !== null && role !== "admin";
  const usageClass = `demo-usage ${atLimit ? "demo-usage-full" : ""}`;

  return (
    <div className={`demo-bar ${open ? "demo-open" : ""}`} role="region" aria-label="Demo mode" ref={ref}>
      <div className="demo-bar-main">
        <span className="demo-tag">DEMO</span>
        <span className="demo-text-full">
          You are exploring as <strong>{INTRO[role]}</strong>.{" "}
          {role === "admin"
            ? "This view is read-only: nothing you add is saved."
            : "Your data is private to you and cleared after 24 hours."}
        </span>
        <span className="demo-text-short">
          Viewing as <strong>{role}</strong>
        </span>
      </div>
      {showUsage && (
        <span className={`${usageClass} demo-usage-mobile`} title="Claims you have filed in this demo">
          {used}/{max}
        </span>
      )}
      <button
        type="button"
        className="demo-menu-btn"
        aria-expanded={open}
        aria-controls="demo-actions"
        onClick={() => setOpen((o) => !o)}
      >
        Menu <span aria-hidden="true">{open ? "▴" : "▾"}</span>
      </button>
      <div className="demo-bar-actions" id="demo-actions">
        {showUsage && (
          <span className={`${usageClass} demo-usage-desktop`} title="Claims you have filed in this demo">
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
