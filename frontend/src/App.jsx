import { useEffect, useRef, useState } from "react";
import { getAuthConfig, listClaims, logout, setDemoLimitHandler, setUnauthorizedHandler, switchDemoRole } from "./api";
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
import DemoBar from "./DemoBar";
import DemoLimitModal from "./DemoLimitModal";
import WakingUp from "./WakingUp";
import useHashRoute, { resetRoute } from "./useHashRoute";
import "./App.css";

// Which screen a URL hash means. Deep links: #new opens the form, #claim/12 opens that claim
// (handy for sharing/screenshots). Anything unknown is the claims list.
function parseRoute(hash, canSeePeople) {
  const claim = /^claim\/(\d+)$/.exec(hash);
  if (claim) return { section: "claims", view: "detail", claimId: Number(claim[1]) };
  if (hash === "new") return { section: "claims", view: "new", claimId: null };
  if (hash === "analytics" || hash === "settings") return { section: hash, view: "list", claimId: null };
  if (hash === "people" && canSeePeople) return { section: "people", view: "list", claimId: null };
  return { section: "claims", view: "list", claimId: null };
}

function Dashboard({ onSignOut, authRequired, currentUser, onSwitchRole }) {
  const [claims, setClaims] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const isDemo = Boolean(currentUser?.demo);

  // The URL hash is the single source of truth for the screen, and every change goes through
  // useHashRoute so the browser's Back button steps through the screens you visited.
  const [hash, go, back] = useHashRoute();
  const { section, view, claimId } = parseRoute(hash, !isDemo);

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

  const openDetail = (id) => go(`claim/${id}`);

  // Replace (not push) the form's entry, so Back from the new claim returns to the list
  // instead of to an empty form.
  const handleSubmitted = (newClaimId) => {
    refresh();
    go(`claim/${newClaimId}`, { replace: true });
  };

  const navigate = (next) => {
    go(next);
    if (next === "analytics") refresh();
  };

  const titles = {
    analytics: ["Analytics", "Fraud-risk and recommendation trends across all claims"],
    settings: ["Settings", "Your profile, preferences and system status"],
    people: ["People", "Create and manage customer and investigator accounts"],
    list: ["Claims", "Review submitted claims and AI-generated fraud recommendations"],
    new: ["Submit New Claim", "Fill in the claim details to run the investigation pipeline"],
    detail: [`Claim #${claimId}`, "Fraud analysis, retrieved evidence, and investigator sign-off"],
  };
  const [title, subtitle] = titles[section === "claims" ? view : section];

  return (
    <>
      {isDemo && <DemoBar role={currentUser.role} onSwitch={onSwitchRole} onExit={onSignOut} refreshKey={claims.length} />}
      <div className="shell">
        <Sidebar onSignOut={onSignOut} section={section} onNavigate={navigate} currentUser={currentUser} />

        <div className="main">
          <header className="topbar">
            <div>
              <h1>{title}</h1>
              <p className="topbar-sub">{subtitle}</p>
            </div>
            {section === "claims" && view === "list" && (
              <button className="primary-btn" onClick={() => go("new")}>
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
              <ClaimForm onSubmitted={handleSubmitted} onCancel={() => back("claims")} />
            )}

            {section === "claims" && view === "detail" && (
              <ClaimDetail
                claimId={claimId}
                onBack={() => back("claims")}
                onFeedbackSubmitted={refresh}
              />
            )}
          </main>
        </div>
      </div>
    </>
  );
}

// With AUTH_DISABLED (local development only) the API treats every request as this admin.
const DEV_USER = { username: "dev", role: "admin", full_name: "Developer" };

const RETRY_EVERY_MS = 4000;
const MAX_ATTEMPTS = 30; // about two minutes: longer than a free server needs to wake

// Auth gate + role routing. The server says whether a login is required; the
// token itself lives only in api.js memory. Customers get the customer portal,
// admins and investigators get the organization dashboard. (The API enforces
// the same split; this only chooses which screens to show.)
export default function App() {
  const [session, setSession] = useState({ status: "loading", required: false, user: null, error: null, demo: null, attempt: 1 });
  const [demoLimit, setDemoLimit] = useState(null);
  const running = useRef(0); // identifies the current connect loop so an older one can stop

  // Connect to the API, retrying on its own while a sleeping free server wakes up.
  // State is only set inside promise callbacks, never synchronously.
  const connect = () => {
    const id = ++running.current;
    const attempt = (n) =>
      getAuthConfig()
        .then((cfg) => {
          if (running.current !== id) return;
          setSession({
            status: "ready",
            required: cfg.auth_required,
            user: cfg.auth_required ? null : DEV_USER,
            error: null,
            demo: cfg.demo || null,
            attempt: n,
          });
        })
        .catch((e) => {
          if (running.current !== id) return;
          if (n >= MAX_ATTEMPTS) {
            setSession({ status: "error", required: false, user: null, error: e.message, demo: null, attempt: n });
            return;
          }
          setSession((s) => ({ ...s, attempt: n + 1 }));
          setTimeout(() => running.current === id && attempt(n + 1), RETRY_EVERY_MS);
        });
    attempt(1);
  };

  const retry = () => {
    setSession({ status: "loading", required: false, user: null, error: null, demo: null, attempt: 1 });
    connect();
  };

  useEffect(() => {
    // A 401 anywhere (expired token, deactivated account) sends the user back to the login screen.
    setUnauthorizedHandler(() => setSession((s) => ({ ...s, user: null })));
    setDemoLimitHandler(setDemoLimit);
    connect();
  }, []);

const signOut = session.required
    ? () => {
        logout();
        resetRoute();
        setDemoLimit(null);
        setSession((s) => ({ ...s, user: null }));
      }
    : null;

  // Demo only: view the same sandbox as the other role.
  const switchRole = async (role) => {
    const body = await switchDemoRole(role);
    resetRoute(); // a claim URL from one role means nothing in the other
    setSession((s) => ({ ...s, user: body.user }));
  };

  if (session.status === "loading") {
    return (
      <AuthLayout>
        <WakingUp attempt={session.attempt} />
      </AuthLayout>
    );
  }
  if (session.status === "error") {
    return (
      <AuthLayout>
        <div className="auth-card">
          <h1 className="login-title">Can't reach the server</h1>
          <p className="error" role="alert">{session.error}</p>
          <p className="topbar-sub">
            The free server may still be waking up, or be temporarily down. Trying again usually works.
          </p>
          <button className="primary-btn auth-submit" onClick={retry}>Try again</button>
        </div>
      </AuthLayout>
    );
  }
  if (!session.user) {
    return <Login demo={session.demo} onSignedIn={(user) => setSession((s) => ({ ...s, user }))} />;
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

  const screen =
    session.user.role === "customer" ? (
      <CustomerPortal user={session.user} onSignOut={signOut} onSwitchRole={switchRole} />
    ) : (
      <Dashboard authRequired={session.required} onSignOut={signOut} currentUser={session.user} onSwitchRole={switchRole} />
    );
  return (
    <>
      {screen}
      <DemoLimitModal limit={demoLimit} onClose={() => setDemoLimit(null)} />
    </>
  );
}
