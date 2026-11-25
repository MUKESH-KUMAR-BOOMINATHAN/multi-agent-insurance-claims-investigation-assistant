import { PORTFOLIO_URL } from "./claimFields";

const ICONS = {
  claims: (
    <>
      <rect x="3" y="3" width="7" height="9" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" />
    </>
  ),
  analytics: <path d="M3 12l2-2 4 4 8-8 4 4" />,
  people: (
    <>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2.5 20c.6-3.6 3.3-6 6.5-6s5.9 2.4 6.5 6" />
      <circle cx="17.5" cy="9" r="2.5" />
      <path d="M17 14c2.6.2 4.2 2.1 4.5 5" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1" />
    </>
  ),
};

const NAV = [
  ["claims", "Claims"],
  ["analytics", "Analytics"],
  ["people", "People"],
  ["settings", "Settings"],
];

export default function Sidebar({ onSignOut, section, onNavigate, currentUser }) {
  // In the public demo only the (read-only) admin tour has a People page.
  const nav = NAV.filter(([key]) => !(key === "people" && currentUser?.demo && currentUser.role !== "admin"));
  const initials = currentUser ? (currentUser.full_name || currentUser.username).slice(0, 2).toUpperCase() : "";
  return (
    <>
    {/* Phones and small tablets: the sidebar is hidden, so a slim top bar + bottom tabs replace it. */}
    <header className="mobile-top">
      <div className="sidebar-brand">
        <span className="brand-mark">CG</span>
        <div className="brand-name">ClaimGuard</div>
      </div>
      <div className="mobile-top-right">
        {currentUser && (
          <span className="avatar avatar-sm" title={currentUser.full_name || currentUser.username}>{initials}</span>
        )}
        {onSignOut && (
          <button type="button" className="mobile-signout" onClick={onSignOut}>Sign out</button>
        )}
      </div>
    </header>
    <nav className="mobile-tabs" aria-label="Main">
      {nav.map(([key, label]) => (
        <button
          type="button"
          key={key}
          className={`mobile-tab ${section === key ? "active" : ""}`}
          aria-current={section === key ? "page" : undefined}
          onClick={() => onNavigate(key)}
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            {ICONS[key]}
          </svg>
          <span>{label}</span>
        </button>
      ))}
    </nav>
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="brand-mark">CG</span>
        <div>
          <div className="brand-name">ClaimGuard</div>
          <div className="brand-sub">Investigation Assistant</div>
        </div>
      </div>

      <nav className="sidebar-nav">
        {nav.map(([key, label]) => (
          <button
            type="button"
            key={key}
            className={`nav-item ${section === key ? "active" : ""}`}
            aria-current={section === key ? "page" : undefined}
            onClick={() => onNavigate(key)}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              {ICONS[key]}
            </svg>
            {label}
          </button>
        ))}
      </nav>

      <a className="sidebar-credit" href={PORTFOLIO_URL} target="_blank" rel="noopener noreferrer">
        <span>Built by Mukesh Kumar</span>
        <strong>View portfolio ↗</strong>
      </a>

      {currentUser && (
        <div className="sidebar-user">
          <span className="avatar avatar-sm">{(currentUser.full_name || currentUser.username).slice(0, 2).toUpperCase()}</span>
          <div>
            <div className="sidebar-user-name">{currentUser.full_name || currentUser.username}</div>
            <div className="brand-sub">{currentUser.demo ? `${currentUser.role} (demo)` : currentUser.role}</div>
          </div>
        </div>
      )}

      {onSignOut && (
        <button type="button" className="signout-btn" onClick={onSignOut}>
          Sign out
        </button>
      )}
    </aside>
    </>
  );
}
