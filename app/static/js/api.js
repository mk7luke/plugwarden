// API client for /api/v2. Append ?fixtures=1 to the page URL to use the local fixture layer.

export const FIXTURES = new URLSearchParams(location.search).has("fixtures");
let fx = null;
async function fixtures() { return fx || (fx = await import("./fixtures.js")); }

export class ApiError extends Error {
  constructor(status, detail, path, conflicts) { super(detail || `HTTP ${status}`); this.status = status; this.path = path; this.conflicts = conflicts || []; }
}

export async function api(path, { method = "GET", body, form } = {}) {
  if (FIXTURES) return (await fixtures()).handle(method, path, body ?? form);
  // Writes carry the CSRF guard header the server requires (403 without it).
  const opts = { method, headers: method === "GET" ? {} : { "X-Requested-With": "lgt-amp-sync" } };
  if (form) opts.body = form;
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  let res;
  try { res = await fetch("/api/v2" + path, opts); }
  catch (e) { throw new ApiError(0, "Network error. Is the server reachable?", path); }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    let d = data && data.detail !== undefined ? data.detail : data;
    if (Array.isArray(d)) d = d.map(x => x.msg || JSON.stringify(x)).join("; ");
    // 409s carry {message, conflicts:[...]}.
    if (d && typeof d === "object") {
      const err = new ApiError(res.status, d.message || (d.fields ? "Some fields are invalid" : undefined), path, d.conflicts);
      err.fields = d.fields; err.code = d.code;
      throw err;
    }
    throw new ApiError(res.status, typeof d === "string" ? d : `HTTP ${res.status}`, path);
  }
  return data;
}

export const get = (p) => api(p);
export const post = (p, body) => api(p, { method: "POST", body });
export const put = (p, body) => api(p, { method: "PUT", body });

// Stream job log lines. Returns a close() function.
export function streamJob(id, { onLine, onDone, onError }) {
  if (FIXTURES) {
    let closed = false;
    fixtures().then(f => f.stream(id, l => !closed && onLine(l), j => !closed && onDone(j)));
    return () => { closed = true; };
  }
  const es = new EventSource(`/api/v2/jobs/${encodeURIComponent(id)}/stream`);
  let finished = false;
  const finish = async (job) => {
    if (finished) return; finished = true; es.close();
    if (!job) { try { job = await get(`/jobs/${encodeURIComponent(id)}`); } catch (e) { onError?.(e); return; } }
    onDone(job);
  };
  es.onmessage = (e) => onLine(e.data);
  // The server sends `event: end` with {status, summary}; fetch the full job for results.
  es.addEventListener("end", () => finish(null));
  es.onerror = () => { if (!finished) finish(null); };
  return () => { finished = true; es.close(); };
}

// Multi-word file search: the server matches one contiguous string, so query the longest word
// and keep results whose path contains every word ("essentials config" → Essentials/config.yml).
export async function searchFiles(server, q, limit = 200) {
  const words = q.toLowerCase().split(/[\s/]+/).filter(Boolean);
  if (!words.length) return { results: [] };
  const longest = words.reduce((a, b) => b.length > a.length ? b : a);
  const r = await get(`/servers/${encodeURIComponent(server)}/search?q=${encodeURIComponent(longest)}&limit=${limit}`);
  return { ...r, results: (r.results || []).filter(x => words.every(w => x.path.toLowerCase().includes(w))) };
}
