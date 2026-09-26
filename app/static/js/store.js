// Tiny global store + cached GET queries with prefix invalidation.
import { useEffect, useState, useRef } from "./lib.js";
import { get } from "./api.js";

const state = { toasts: [], jobs: [], palette: false, drawer: false, confirm: null, theme: document.documentElement.dataset.theme };
const subs = new Set();
export const getState = () => state;
export function setState(patch) {
  Object.assign(state, typeof patch === "function" ? patch(state) : patch);
  subs.forEach(f => f());
}
export function useStore(sel) {
  const [, force] = useState(0);
  const selRef = useRef(sel); selRef.current = sel;
  const last = useRef(sel(state));
  useEffect(() => {
    const f = () => { const v = selRef.current(state); if (v !== last.current) { last.current = v; force(x => x + 1); } };
    subs.add(f); f();
    return () => subs.delete(f);
  }, []);
  last.current = sel(state);
  return last.current;
}

// ----- queries -----
const cache = new Map(); // path -> {data, error, loading, promise, ts}
const qsubs = new Set();
const notifyQ = () => qsubs.forEach(f => f());

function load(path, force = false) {
  let e = cache.get(path);
  if (e && e.promise && !force) return e.promise;
  if (!e) { e = { data: undefined, error: null, loading: true }; cache.set(path, e); }
  e.loading = true;
  e.promise = get(path).then(
    d => { e.data = d; e.error = null; e.ts = Date.now(); },
    err => { e.error = err; }
  ).finally(() => { e.loading = false; e.promise = null; notifyQ(); });
  notifyQ();
  return e.promise;
}

export function useQuery(path) {
  const [, force] = useState(0);
  useEffect(() => {
    if (!path) return;
    const f = () => force(x => x + 1);
    qsubs.add(f);
    const e = cache.get(path);
    // Refetch on mount when missing, stale, failed, or older than 15s (cached data shows meanwhile).
    if (!e || (!e.loading && (e.stale || e.error || Date.now() - (e.ts || 0) > 15000))) load(path, true);
    return () => qsubs.delete(f);
  }, [path]);
  const e = path ? cache.get(path) : null;
  return {
    data: e?.data, error: e?.error, loading: !e || (e.loading && e.data === undefined),
    refreshing: !!e?.loading, reload: () => path && load(path, true),
  };
}

export function peek(path) { return cache.get(path)?.data; }

// Mark matching queries stale and refetch those currently on screen.
export function invalidate(...prefixes) {
  for (const [k, e] of cache) {
    if (!prefixes.length || prefixes.some(p => k.startsWith(p))) { e.stale = true; if (qsubs.size) load(k, true).then(() => { e.stale = false; }); }
  }
}
export const prefetch = (p) => load(p);

// ----- toasts -----
let tid = 0;
export function toast(t) {
  const id = ++tid;
  const item = { id, kind: "info", ...t };
  setState(s => ({ toasts: [...s.toasts, item].slice(-4) }));
  if (!t.sticky) setTimeout(() => dismissToast(id), t.timeout || 5200);
  return id;
}
export const dismissToast = (id) => setState(s => ({ toasts: s.toasts.filter(t => t.id !== id) }));

// ----- confirm dialog (promise) -----
export function confirmDialog(opts) {
  return new Promise(resolve => setState({ confirm: { ...opts, resolve } }));
}

// ----- theme -----
export function setTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("amp.theme", t); } catch {}
  setState({ theme: t });
}
