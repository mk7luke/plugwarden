// Hash router: #/view/param?query
import { useEffect, useState } from "./lib.js";

export function parse(hash = location.hash) {
  const raw = hash.replace(/^#\/?/, "");
  const [path, qs = ""] = raw.split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  return { name: parts[0] || "dashboard", parts, query: Object.fromEntries(new URLSearchParams(qs)) };
}

export function navigate(to, { replace = false } = {}) {
  const h = to.startsWith("#") ? to : "#" + to;
  if (replace) history.replaceState(null, "", h), window.dispatchEvent(new HashChangeEvent("hashchange"));
  else location.hash = h;
}

export function useRoute() {
  const [r, set] = useState(parse());
  useEffect(() => {
    const on = () => set(parse());
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  return r;
}

// Navigate, then open a dialog on the new page. Route changes close open dialogs, so the dialog must open after that.
let afterNav = null;
export function openAfterNav(to, open) {
  const h = to.startsWith("#") ? to : "#" + to;
  if (location.hash.split("?")[0] === h.split("?")[0]) { open(); return; }
  afterNav = open;
  navigate(h);
}
export function runAfterNav() { const f = afterNav; afterNav = null; f?.(); }
