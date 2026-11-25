import { useCallback, useEffect, useState } from "react";

// The app has no router library: screens live in the URL hash (#claim/12, #new, #people ...).
// Every screen change must create a browser history entry, or the Back button leaves the
// app instead of going to the previous screen. This hook is the one place that does it.
//
//   const [hash, go, back] = useHashRoute();
//   go("claim/12")                        push an entry (Back returns to where you were)
//   go("claim/12", { replace: true })     replace the current entry (e.g. after submitting a
//                                         form, so Back skips the finished form)
//   back("claims")                        go back one screen; if the app was opened straight
//                                         on this screen, go to "claims" instead of leaving
//
// Entries we push carry a depth counter in history.state, so `back` knows whether there is
// an app screen behind the current one.

const read = () => window.location.hash.replace(/^#/, "");
const depth = () => window.history.state?.depth ?? 0;
const urlFor = (hash) => (hash ? `#${hash}` : window.location.pathname + window.location.search);

export default function useHashRoute() {
  const [hash, setHash] = useState(read);

  useEffect(() => {
    // popstate covers Back/Forward and address-bar edits of the fragment.
    const sync = () => setHash(read());
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);

  const go = useCallback((next, { replace = false } = {}) => {
    if (next === read()) return;
    if (replace) {
      window.history.replaceState({ depth: depth() }, "", urlFor(next));
    } else {
      window.history.pushState({ depth: depth() + 1 }, "", urlFor(next));
    }
    setHash(next);
  }, []);

  const back = useCallback(
    (fallback = "claims") => {
      if (depth() > 0) window.history.back();
      else go(fallback, { replace: true });
    },
    [go],
  );

  return [hash, go, back];
}

// Forget the current screen without adding a history entry (sign-out, switching role).
export function resetRoute() {
  window.history.replaceState({ depth: 0 }, "", window.location.pathname + window.location.search);
}
