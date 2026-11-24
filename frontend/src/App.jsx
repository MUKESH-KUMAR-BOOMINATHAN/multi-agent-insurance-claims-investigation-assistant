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
import People from "./People";
import CustomerPortal from "./CustomerPortal";
import ChangePassword from "./ChangePassword";
import AuthLayout from "./AuthLayout";
import "./App.css";

function Dashboard({ onSignOut, authRequired, currentUser }) {
  const [claims, setClaims] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // view: "list" | "detail" | "new"
  // Deep links: #new opens the form, #claim/12 opens that claim (handy for sharing/screenshots).
  const hash = window.location.hash.slice(1);
  const hashClaim = /^claim\/(\d+)$/.exec(hash);
  const [view, setView] = useState(hashClaim ? "detail" : hash === "new" ? "new" : "list");
  const [selectedClaimId, setSelectedClaimId] = useState(hashClaim ? Number(hashClaim[1]) : null);

  // State is only set in the promise callbacks; `loading` starts true, and a manual
  // refresh keeps the current list on screen while it reloads.
  const fetchClaims = () =>
    listClaims()
      .then((rows) => {
        setClaims(rows);
        setError(null);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  const refresh = () => {
    fetchClaims();
  };

  useEffect(() => {
    fetchClaims();
  }, []);

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
    return ["analytics", "settings", "people"].includes(fromHash) ? fromHash : "claims";
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
    people: ["People", "Create and manage customer and investigator accounts"],
    list: ["Claims", "Review submitted claims and AI-generated fraud recommendations"],
    new: ["Submit New Claim", "Fill in the claim details to run the investigation pipeline"],
    detail: [`Claim #${selectedClaimId}`, "Fraud analysis, retrieved evidence, and investigator sign-off"],
  };
  const [title, subtitle] = titles[section === "claims" ? view : section];

  return (
    <div className="shell">
      <Sidebar onSignOut={onSignOut} section={section} onNavigate={navigate} currentUser={currentUser} />

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
          {section === "settings" && <Settings authRequired={authRequired} onSignOut={onSignOut} currentUser={currentUser} />}
          {section === "people" && <People currentUser={currentUser} />}

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

// With AUTH_DISABLED (local development only) the API treats every request as this admin.
const DEV_USER = { username: "dev", role: "admin", full_name: "Developer" };

// Auth gate + role routing. The server says whether a login is required; the
// token itself lives only in api.js memory. Customers get the customer portal,
// admins and investigators get the organization dashboard. (The API enforces
// the same split; this only chooses which screens to show.)
export default function App() {
  const [session, setSession] = useState({ status: "loading", required: false, user: null, error: null });

  // State changes happen only in the promise callbacks (the initial state is already "loading").
  const fetchConfig = () =>
    getAuthConfig()
      .then((cfg) =>
        setSession({
          status: "ready",
          required: cfg.auth_required,
          user: cfg.auth_required ? null : DEV_USER,
          error: null,
        }),
      )
      .catch((e) => setSession({ status: "error", required: false, user: null, error: e.message }));

  const retry = () => {
    setSession({ status: "loading", required: false, user: null, error: null });
    fetchConfig();
  };

  useEffect(() => {
    // A 401 anywhere (expired token, deactivated account) sends the user back to the login screen.
    setUnauthorizedHandler(() => setSession((s) => ({ ...s, user: null })));
    fetchConfig();
  }, []);

  const signOut = session.required
    ? () => {
        logout();
        window.location.hash = "";
        setSession((s) => ({ ...s, user: null }));
      }
    : null;

  if (session.status === "loading") {
    return (
      <AuthLayout>
        <div className="auth-card">
          <div className="spinner" aria-hidden="true" />
          <h1 className="login-title">Connecting…</h1>
          <p className="topbar-sub">A free server that has been idle can take up to a minute to wake up.</p>
        </div>
      </AuthLayout>
    );
  }
  if (session.status === "error") {
    return (
      <AuthLayout>
        <div className="auth-card">
          <h1 className="login-title">Can't reach the server</h1>
          <p className="error" role="alert">{session.error}</p>
          <button className="primary-btn auth-submit" onClick={retry}>Try again</button>
        </div>
      </AuthLayout>
    );
  }
  if (!session.user) {
    return <Login onSignedIn={(user) => setSession((s) => ({ ...s, user }))} />;
  }
  if (session.user.must_change_password) {
    // The organization chose this password; nothing else is reachable until it is replaced.
    return (
      <AuthLayout>
        <ChangePassword
          forced
          onDone={() => setSession((s) => ({ ...s, user: { ...s.user, must_change_password: false } }))}
          onCancel={signOut}
        />
      </AuthLayout>
    );
  }
  if (session.user.role === "customer") {
    return <CustomerPortal user={session.user} onSignOut={signOut} />;
  }
  return <Dashboard authRequired={session.required} onSignOut={signOut} currentUser={session.user} />;
}
