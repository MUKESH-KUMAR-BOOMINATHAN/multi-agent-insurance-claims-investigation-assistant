import { useEffect } from "react";

// Shown when a demo visitor reaches the claim cap. The demo is capped because it runs on
// free hosting with a limited AI allowance; this turns the limit into a next step.
export default function DemoLimitModal({ limit, onClose }) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!limit) return null;
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="demo-limit-title"
        onClick={(e) => e.stopPropagation()}>
        <h2 id="demo-limit-title">You've reached the demo limit</h2>
        <p>{limit.message}</p>
        <p className="topbar-sub">
          Everything you filed so far is still here to explore: open a claim, switch roles, or review the
          analytics.
        </p>
        <div className="modal-actions">
          {limit.contactUrl && (
            <a className="primary-btn" href={limit.contactUrl} target="_blank" rel="noopener noreferrer">
              Contact Mukesh via portfolio ↗
            </a>
          )}
          <button type="button" onClick={onClose} autoFocus>Keep exploring</button>
        </div>
      </div>
    </div>
  );
}
