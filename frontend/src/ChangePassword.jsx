import { useState } from "react";
import { changePassword } from "./api";

// Used two ways: `forced` right after signing in with a temporary password set
// by the organization (nothing else is reachable until it is changed), and
// voluntarily from Settings or the customer portal.
export default function ChangePassword({ forced = false, onDone, onCancel }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(false);

  const mismatch = confirm.length > 0 && next !== confirm;

  const submit = async (e) => {
    e.preventDefault();
    if (next !== confirm) return;
    setSubmitting(true);
    setError(null);
    try {
      await changePassword(current, next);
      setDone(true);
      setCurrent("");
      setNext("");
      setConfirm("");
      onDone?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className={forced ? "auth-card" : "change-password"} onSubmit={submit}>
      {forced ? (
        <>
          <h1 className="login-title">Choose your password</h1>
          <p className="topbar-sub login-hint">
            Your account was set up with a temporary password. Pick your own to continue.
          </p>
        </>
      ) : (
        <div className="panel-header settings-sub"><h2>Change password</h2></div>
      )}

      <div className={forced ? "auth-fields" : "form-grid"}>
        <label className="form-field">
          {forced ? "Temporary password" : "Current password"}
          <input type="password" autoComplete="current-password" value={current}
            onChange={(e) => setCurrent(e.target.value)} required />
        </label>
        <label className="form-field">
          New password (10+ characters)
          <input type="password" autoComplete="new-password" value={next} minLength={10} maxLength={200}
            onChange={(e) => setNext(e.target.value)} required />
        </label>
        <label className="form-field">
          Repeat new password
          <input type="password" autoComplete="new-password" value={confirm}
            onChange={(e) => setConfirm(e.target.value)} required />
        </label>
      </div>

      {mismatch && <p className="error">The new passwords do not match.</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {done && !forced && <p className="saved-note" role="status">Password changed.</p>}

      <div className="settings-actions">
        <button type="submit" className={`primary-btn ${forced ? "auth-submit" : ""}`}
          disabled={submitting || mismatch || next.length < 10}>
          {submitting ? "Saving…" : forced ? "Save and continue" : "Change password"}
        </button>
        {onCancel && <button type="button" onClick={onCancel}>{forced ? "Sign out" : "Cancel"}</button>}
      </div>
    </form>
  );
}
