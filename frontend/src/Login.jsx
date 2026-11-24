import { useState } from "react";
import { login } from "./api";
import AuthLayout from "./AuthLayout";

export default function Login({ onSignedIn }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

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

  return (
    <AuthLayout>
      <form className="auth-card" onSubmit={handleSubmit}>
        <h1 className="login-title">Welcome back</h1>
        <p className="topbar-sub login-hint">
          Sign in to continue. Customers and staff use the same page; accounts are created by your insurer.
        </p>

        <label className="form-field">
          Username
          <input
            type="text"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            autoFocus
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

        <button type="submit" className="primary-btn auth-submit" disabled={submitting || !username || !password}>
          {submitting ? "Signing in…" : "Sign in"}
        </button>
        <p className="auth-note">
          Forgot your password? Ask your insurer to reset it; you will choose a new one when you sign in.
        </p>
      </form>
    </AuthLayout>
  );
}
