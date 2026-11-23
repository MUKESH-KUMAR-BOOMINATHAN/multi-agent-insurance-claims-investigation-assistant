// Per-browser preferences. Stored in localStorage purely as a convenience; every
// read and write is guarded because storage can be blocked (private windows etc.).
const KEY = "claimguard.prefs";
const DEFAULTS = { displayName: "", theme: "light", rowsPerPage: 25 };

export function loadPrefs() {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || "{}") };
  } catch {
    return { ...DEFAULTS };
  }
}

export function savePrefs(prefs) {
  try {
    localStorage.setItem(KEY, JSON.stringify(prefs));
  } catch {
    /* storage unavailable: preferences just last for this page view */
  }
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme === "dark" ? "dark" : "light";
}
