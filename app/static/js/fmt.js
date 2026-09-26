// Formatting helpers.

export function relTime(iso) {
  if (!iso) return "never";
  const t = typeof iso === "number" ? iso * (iso < 1e12 ? 1000 : 1) : Date.parse(iso);
  if (isNaN(t)) return String(iso);
  const s = Math.round((Date.now() - t) / 1000);
  const fut = s < 0, a = Math.abs(s);
  let v;
  if (a < 45) return fut ? "in a moment" : "just now";
  if (a < 3600) v = `${Math.round(a / 60)}m`;
  else if (a < 86400) v = `${Math.round(a / 3600)}h`;
  else if (a < 86400 * 30) v = `${Math.round(a / 86400)}d`;
  else return new Date(t).toLocaleDateString();
  return fut ? `in ${v}` : `${v} ago`;
}

export function absTime(iso) {
  if (!iso) return "";
  const t = typeof iso === "number" ? iso * (iso < 1e12 ? 1000 : 1) : Date.parse(iso);
  if (isNaN(t)) return String(iso);
  return new Date(t).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function duration(a, b) {
  if (!a || !b) return "";
  const s = Math.max(0, Math.round((Date.parse(b) - Date.parse(a)) / 1000));
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

export function bytes(n) {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;

export function initials(email) {
  if (!email) return "—";
  const name = email.split("@")[0];
  const parts = name.split(/[._-]/).filter(Boolean);
  return ((parts[0] || "?")[0] + (parts[1] ? parts[1][0] : "")).toUpperCase();
}

// Short, comparable version for tight spaces; callers put the full string in a tooltip.
// 2.14.3-SNAPSHOT-1231+8090431 → 2.14.3·1231, 6.0.1+1.21.8 → 6.0.1, 2.0.40-SNAPSHOT (build 3990) → 2.0.40·3990
export function compactVer(v) {
  if (!v) return v;
  return String(v)
    .replace(/\s*\(git[^)]*\)/i, "")
    .replace(/\+[0-9a-f]{6,}$/i, "")
    .replace(/\+(mc)?1\.\d+(\.\d+)?$/i, "")
    .replace(/-SNAPSHOT-?(\d+)/i, "·$1").replace(/-SNAPSHOT/i, "-S")
    .replace(/\s*\(build (\d+)\)/i, "·$1").replace(/-b(?:uild-?)?(\d+)$/i, "·$1")
    .replace(/-S·/, "·")
    .trim();
}

// Fit a string into n characters by eliding the middle: 2.0.40·3990 → 2.0.4…3990.
export function midTrunc(s, n) {
  if (!s || s.length <= n) return s;
  const tail = Math.ceil((n - 1) / 2), head = n - 1 - tail;
  return s.slice(0, head) + "…" + s.slice(-tail);
}

// Files that hold live server data rather than configuration (warn before pushing them).
export const isDataFile = (path) => /(^|\/)(spawn|warps?|saves|homes?|kits?|jails?|worth|usermap|npcs?)\.(ya?ml|json)$|[-_]data\.(ya?ml|json)$|(^|\/)data\.(ya?ml|json)$|(^|\/)(userdata|playerdata|data|warps|players)\//i.test(path || "");

// Third-party links (changelogs, source pages) are only rendered when they're https.
export const safeUrl = (u) => typeof u === "string" && /^https:\/\//i.test(u) ? u : null;
