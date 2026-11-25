import { useEffect, useState } from "react";

// Shown from the very first moment the app starts loading. The API runs on a free host
// that sleeps when idle, so the first request can take up to a minute: say so straight
// away, show that something is happening, and keep retrying without the user doing anything.
export default function WakingUp({ attempt = 1 }) {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, []);

  const longWait = seconds >= 20;

  return (
    <div className="auth-card wake-card" role="status" aria-live="polite">
      <div className="spinner" aria-hidden="true" />
      <h1 className="login-title">Waking up the server…</h1>
      <p className="wake-lead">
        ClaimGuard runs on <strong>free hosting</strong>, which puts the server to sleep when nobody is using it.
        The first visit after a quiet spell can take <strong>up to a minute</strong>. After that, everything is fast.
      </p>
      <div className="wake-bar" aria-hidden="true"><span /></div>
      <p className="topbar-sub wake-meta">
        {seconds}s elapsed
        {longWait
          ? " · Almost there. Free servers sometimes need a little longer."
          : " · You don't need to do anything, this page continues on its own."}
        {attempt > 3 && ` (retry ${attempt})`}
      </p>
    </div>
  );
}
