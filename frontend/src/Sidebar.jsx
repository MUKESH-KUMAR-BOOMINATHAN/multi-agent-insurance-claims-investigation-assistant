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
  ["settings", "Settings"],
];

export default function Sidebar({ onSignOut, section, onNavigate }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="brand-mark">CG</span>
        <div>
          <div className="brand-name">ClaimGuard</div>
          <div className="brand-sub">Investigation Assistant</div>
        </div>
      </div>

      <nav className="sidebar-nav">
        {NAV.map(([key, label]) => (
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

      {onSignOut && (
        <button type="button" className="signout-btn" onClick={onSignOut}>
          Sign out
        </button>
      )}
    </aside>
  );
}
