import { useEffect, useState } from "react";
import { getAuthConfig, listClaims, logout, setUnauthorizedHandler } from "./api";
import Sidebar from "./Sidebar";
import StatCards from "./StatCards";
import ClaimsList from "./ClaimsList";
import ClaimDetail from "./ClaimDetail";
import ClaimForm from "./ClaimForm";
import Login from "./Login";
import Analytics from "./Analytics";
import Settings from "./Settings";
import "./App.css";

function Dashboard({ onSignOut, authRequired }) {
  const [claims, setClaims] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // view: "list" | "detail" | "new"
  // Deep links: #new opens the form, #claim/12 opens that claim (handy for sharing/screenshots).
  const hash = window.location.hash.slice(1);
  const hashClaim = /^claim\/(\d+)$/.exec(hash);
  const [view, setView] = useState(hashClaim ? "detail" : hash === "new" ? "new" : "list");
  const [selectedClaimId, setSelectedClaimId] = useState(hashClaim ? Number(hashClaim[1]) : null);

  const refresh = () => {
    setLoading(true);
    setError(null);
    listClaims()
      .then(setClaims)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(refresh, []);

  const openDetail = (id) => {
    setSelectedClaimId(id);
    setView("detail");
  };

  const handleSubmitted = (newClaimId) => {
    refresh();
    openDetail(newClaimId);
  };

  // section: which sidebar page is open. "claims" uses `view` for list/new/detail.
  const [section, setSection] = useState(() => {
    const fromHash = window.location.hash.slice(1);
    return ["analytics", "settings"].includes(fromHash) ? fromHash : "claims";
  });
  const navigate = (next) => {
    setSection(next);
    window.location.hash = next === "claims" ? "" : next;
    if (next === "claims") setView("list");
    if (next === "analytics") refresh();
  };

  const titles = {
    analytics: ["Analytics", "Fraud-risk and recommendation trends across all claims"],
    settings: ["Settings", "Your profile, preferences and system status"],
    list: ["Claims", "Review submitted claims and AI-generated fraud recommendations"],
    new: ["Submit New Claim", "Fill in the claim details to run the investigation pipeline"],
    detail: [`Claim #${selectedClaimId}`, "Fraud analysis, retrieved evidence, and investigator sign-off"],
  };
  const [title, subtitle] = titles[section === "claims" ? view : section];

  return (
    <div className="shell">
      <Sidebar onSignOut={onSignOut} section={section} onNavigate={navigate} />

      <div className="main">
        <header className="topbar">
          <div>
            <h1>{title}</h1>
            <p className="topbar-sub">{subtitle}</p>
          </div>
          {section === "claims" && view === "list" && (
            <button className="primary-btn" onClick={() => setView("new")}>
              + New Claim
            </button>
          )}
        </header>

        <main className="content">
          {section === "analytics" && <Analytics claims={claims} />}
          {section === "settings" && <Settings authRequired={authRequired} onSignOut={onSignOut} />}

          {section === "claims" && view === "list" && (
            <>
              {claims.length > 0 && <StatCards claims={claims} />}
              <ClaimsList
                claims={claims}
                loading={loading}
                error={error}
                onSelect={openDetail}
                onRefresh={refresh}
              />
            </>
          )}

          {section === "claims" && view === "new" && (
            <ClaimForm onSubmitted={handleSubmitted} onCancel={() => setView("list")} />
          )}

          {section === "claims" && view === "detail" && (
            <ClaimDetail
              claimId={selectedClaimId}
              onBack={() => setView("list")}
              onFeedbackSubmitted={refresh}
            />
          )}
        </main>
      </div>
    </div>
  );
}

// Auth gate. The server says whether a login is required (it is not in local
// development with AUTH_DISABLED); the token itself lives only in api.js memory.
export default function App() {
  const [session, setSession] = useState({ status: "loading", required: false, signedIn: false, error: null });

  // State changes happen only in the promise callbacks (the initial state is already "loading").
  const fetchConfig = () =>
    getAuthConfig()
      .then((cfg) =>
        setSession({ status: "ready", required: cfg.auth_required, signedIn: !cfg.auth_required, error: null }),
      )
      .catch((e) => setSession({ status: "error", required: false, signedIn: false, error: e.message }));

  const retry = () => {
    setSession({ status: "loading", required: false, signedIn: false, error: null });
    fetchConfig();
  };

  useEffect(() => {
    // A 401 anywhere (expired token) sends the user back to the login screen.
    setUnauthorizedHandler(() => setSession((s) => ({ ...s, signedIn: false })));
    fetchConfig();
  }, []);

  if (session.status === "loading") {
    return (
      <div className="login-shell">
        <div className="login-card">
          <p>Connecting to the server…</p>
          <p className="topbar-sub">A server that has been idle can take up to a minute to wake up.</p>
        </div>
      </div>
    );
  }
  if (session.status === "error") {
    return (
      <div className="login-shell">
        <div className="login-card">
          <p className="error" role="alert">{session.error}</p>
          <button className="primary-btn" onClick={retry}>Retry</button>
        </div>
      </div>
    );
  }
  if (session.required && !session.signedIn) {
    return <Login onSignedIn={() => setSession((s) => ({ ...s, signedIn: true }))} />;
  }
  return (
    <Dashboard
      authRequired={session.required}
      onSignOut={
        session.required
          ? () => {
              logout();
              setSession((s) => ({ ...s, signedIn: false }));
            }
          : null
      }
    />
  );
}
