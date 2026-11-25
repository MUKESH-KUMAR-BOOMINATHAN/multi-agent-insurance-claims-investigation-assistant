import { useState } from "react";
import { login, startDemo } from "./api";
import AuthLayout from "./AuthLayout";

const DEMO_ROLES = [
  ["investigator", "Investigator", "Review claims with fraud scores, similar past claims and AI advice, then decide."],
  ["customer", "Customer", "File a claim and follow it from \"under review\" to a decision."],
];

export default function Login({ onSignedIn, demo }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [starting, setStarting] = useState(null); // the demo role being started
  const [error, setError] = useState(null);
  const demoOn = Boolean(demo?.enabled);
  const busy = submitting || starting !== null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      onSignedIn(await login(username, password));
    } catch (err) {
      // The password stays in the field on failure so a typo is easy to fix;
      // it is cleared only on success (the component unmounts).
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  const beginDemo = async (role) => {
    setStarting(role);
    setError(null);
    try {
      const body = await startDemo(role);
      onSignedIn(body.user);
    } catch (err) {
      setError(err.message);
      setStarting(null);
    }
  };

  return (
    <AuthLayout>
      <div className="auth-card">
        {demoOn && (
          <div className="demo-box" role="region" aria-label="Try the live demo">
            <h1 className="login-title">Try it live</h1>
            <p className="topbar-sub login-hint">
              No sign-up and no password. Pick a role and explore a private sandbox with sample claims.
            </p>
            <div className="demo-roles">
              {DEMO_ROLES.map(([role, title, text]) => (
                <button type="button" key={role} className="demo-role" disabled={busy} onClick={() => beginDemo(role)}>
                  <strong>{starting === role ? "Starting your sandbox…" : title}</strong>
                  <span>{text}</span>
                </button>
              ))}
            </div>
            <p className="auth-note">
              {starting
                ? "Setting up your sandbox. The database can take a few seconds to wake up."
                : `Free demo: up to ${demo.max_claims} claims each, data cleared after 24 hours.`}
            </p>
            <div className="demo-divider"><span>or sign in with an account</span></div>
          </div>
        )}

        <form className="login-form" onSubmit={handleSubmit}>
          {!demoOn && (
            <>
              <h1 className="login-title">Welcome back</h1>
              <p className="topbar-sub login-hint">
                Sign in to continue. Customers and staff use the same page; accounts are created by your insurer.
              </p>
            </>
          )}

          <label className="form-field">
            Username
            <input
              type="text"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              autoFocus={!demoOn}
            />
          </label>

          <div className="form-field">
            <label htmlFor="login-password">Password</label>
            <span className="password-wrap">
              <input
                id="login-password"
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
              <button type="button" className="password-toggle" onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? "Hide password" : "Show password"}>
                {showPassword ? "Hide" : "Show"}
              </button>
            </span>
          </div>

          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}

          <button type="submit" className="primary-btn auth-submit" disabled={busy || !username || !password}>
            {submitting ? "Signing in…" : "Sign in"}
          </button>
          <p className="auth-note">
            Forgot your password? Ask your insurer to reset it; you will choose a new one when you sign in.
          </p>
        </form>
      </div>
    </AuthLayout>
  );
}
