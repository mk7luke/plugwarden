// Plugin startup health, read from the servers' Minecraft logs by the backend:
// - the canary check the scheduler runs before rolling an update out further
// - an on-demand startup report for a server (GET /servers/{id}/health?since=)
// - known (pre-existing) issues that are warnings, not failures of the latest change.
import { html, useState, useEffect } from "../lib.js";
import { get } from "../api.js";
import { useQuery } from "../store.js";
import { Icon, Btn, Tag, Skel } from "./ui.js";
import { relTime, plural } from "../fmt.js";

const HEALTH = {
  healthy: ["ok", "circle-check", "enabled cleanly"],
  failed: ["danger", "circle-x", "failed to enable"],
  unknown: ["", "circle-dashed", "not in the log yet"],
  pending: ["", "clock", "waiting for restart"],
};
const statusOf = (h) => (typeof h === "string" ? h : h?.status) || "pending";
const lines = (x) => Array.isArray(x) ? x.join("\n") : x || "";

export function HealthTag({ h }) {
  const [k, i, l] = HEALTH[statusOf(h)] || HEALTH.pending;
  return html`<${Tag} kind=${k} icon=${i} title=${h?.reason || undefined}>${l}<//>`;
}

// Redacted log lines, collapsed by default; excerpt[hl] (the backend's match_index) is the matching line.
export const Excerpt = ({ text, label = "Log excerpt", open, hl, log, line }) => {
  if (!lines(text)) return null;
  const ls = Array.isArray(text) ? text : String(text).split("\n");
  return html`<details class="excerpt" open=${open}>
    <summary>${label}${log && html`<span class="muted mono"> · ${log}${line ? `:${line}` : ""}</span>`}</summary>
    <pre class="log">${ls.map((l, i) => html`<span class=${i === hl ? "l-hit" : ""}>${l + "\n"}</span>`)}</pre></details>`;
};
// The props an excerpt-bearing item passes to <Excerpt>.
const ex = (e) => ({ text: e?.excerpt, hl: e?.match_index, log: e?.log, line: e?.line });

const pname = (r) => r.name || (r.key || "").split(":").pop();

// Canary rows + held versions from overview.auto_update.
export function CanaryStatus({ au, compact }) {
  const rows = au?.canary || [];
  const held = au?.held || [];
  if (!rows.length && !held.length) return null;
  if (compact) {
    if (!held.length) return null;
    return html`<p class="hl-canary-fail"><${Icon} n="circle-x" cls="i-sm" /><b>Canary check failed:</b>
      ${held.map(h => `${pname(h)} ${h.version} on ${h.server}`).join(", ")} — held back from the other servers
      <a class="link" href="#/updates">View log</a></p>`;
  }
  return html`<div class="canary">
    ${rows.length > 0 && html`<div class="field-label">Canary</div>
    <ul>${rows.map(r => { const st = statusOf(r.canary_health); return html`<li>
      <span class="grow"><b>${pname(r)} ${r.version}</b> <span class="muted">on ${r.server}</span></span>
      <${HealthTag} h=${r.canary_health} />
      ${st === "healthy" && r.soak_hours_left > 0 && html`<span class="small muted">${Math.ceil(r.soak_hours_left)} h soak left</span>`}
      ${st === "failed" && html`<div class="canary-ex"><${Excerpt} ...${ex(r.canary_health)} label=${r.canary_health?.reason || "Why it was held"} /></div>`}
    </li>`; })}</ul>`}
    ${held.length > 0 && html`<div class="field-label" style="margin-top:8px">Held — never auto-applied</div>
    <ul>${held.map(h => html`<li><span class="grow"><b>${pname(h)} ${h.version}</b> <span class="muted">failed on ${h.server} ${relTime(h.at)}</span></span>
      <${Tag} kind="danger" icon="circle-x">held<//>
      <div class="canary-ex"><${Excerpt} ...${ex(h)} label=${h.reason || "Log excerpt"} /></div></li>`)}</ul>`}
  </div>`;
}

// Known issues from a health report: errors that were already in earlier runs (per plugin), plus
// errors that can't be judged yet because there's no earlier run to compare with.
export function knownIssues(report) {
  if (!report) return [];
  const first = (e) => (Array.isArray(e.excerpt) ? (e.match_index != null ? e.excerpt[e.match_index] : e.excerpt.find(l => /ERROR|SEVERE|WARN|Exception/i.test(l)) || e.excerpt[0]) : e.excerpt) || "";
  // Strip "[time level]: [Plugin] " prefixes so the message itself reads first.
  const brief = (l) => l.replace(/^(\[[^\]]*\]:?\s*)+/, "").slice(0, 160);
  // Top-level list when the backend sends it; its `reason` is generic, so show the log line itself.
  const known = Array.isArray(report.preexisting_errors)
    ? report.preexisting_errors.map(e => ({ name: e.name || e.plugin, reason: brief(first(e)) || e.reason, excerpt: e.excerpt, match_index: e.match_index, log: e.log, line: e.line, seen_in_runs: e.seen_in_runs, level: e.level }))
    : (report.plugins || []).flatMap(p => (p.preexisting_errors || []).map(e => ({ name: pname(p), reason: brief(first(e)) || "error seen in earlier starts too", excerpt: e.excerpt, match_index: e.match_index, log: e.log, line: e.line, seen_in_runs: e.seen_in_runs, level: e.level })));
  // Errors that can't be judged yet (no earlier start to compare with) are only listed per plugin.
  const warn = (report.plugins || []).flatMap(p => (p.warnings || []).map(e => ({ name: pname(p), reason: `${brief(first(e))} (no earlier start to compare)`, excerpt: e.excerpt, match_index: e.match_index, log: e.log, line: e.line })));
  return [...known, ...warn];
}

// Warnings that were already there before the latest change (e.g. a UDP port already in use).
// "2 errors, 1 warning" — by the backend's level, else by the matched log line.
function byLevel(issues) {
  const lv = (i) => i.level || (/\b(ERROR|SEVERE)\b/.test(i.reason || "") || /\b(ERROR|SEVERE)\b/.test(lines(i.excerpt)) ? "error" : "warning");
  const e = issues.filter(i => lv(i) === "error").length, w = issues.length - e;
  return [e && plural(e, "error"), w && plural(w, "warning")].filter(Boolean).join(", ");
}

export function KnownIssues({ issues, title = "Known issues on this server", sub = "that also appeared in earlier starts — not caused by recent changes" }) {
  if (!issues?.length) return null;
  // The same message logged by several code paths reads as one issue with a count.
  const grouped = [...issues.reduce((m, i) => { const k = `${i.name}|${i.reason}`; const g = m.get(k); g ? g.n++ : m.set(k, { ...i, n: 1 }); return m; }, new Map()).values()];
  issues = grouped;
  return html`<section class="known" aria-label=${title}>
    <div class="row" style="gap:8px"><${Icon} n="info" cls="i-sm" /><b class="small">${title}</b>
      <span class="small muted">${byLevel(issues)} ${sub}</span></div>
    <ul>${issues.map(i => html`<li><span class="small"><b>${i.name || pname(i) || "Server"}</b> — ${i.reason}${i.n > 1 ? html` <span class="muted">×${i.n}</span>` : ""}</span>
      ${i.seen_in_runs > 1 && html`<span class="small muted"> · in the last ${i.seen_in_runs} starts</span>`}
      <${Excerpt} ...${ex(i)} /></li>`)}</ul>
  </section>`;
}

// Server page: known issues from the current run's log (no `since`).
export function ServerKnownIssues({ server }) {
  const q = useQuery(`/servers/${encodeURIComponent(server)}/health`);
  const failing = (q.data?.plugins || []).filter(p => p.status === "failed");
  return html`${failing.length > 0 && html`<section class="known is-failing" aria-label="Plugins failing at startup">
      <div class="row" style="gap:8px"><${Icon} n="circle-x" cls="i-sm" /><b class="small">${plural(failing.length, "plugin")} ${failing.some(p => p.running === false) ? "not running" : "failed to start"} after the last start</b>
        <span class="small muted">${q.data.restarted_at ? `started ${relTime(q.data.restarted_at)}` : ""}</span></div>
      <ul>${failing.map(p => html`<li><span class="small"><b>${pname(p)}</b> — ${p.reason}${p.preexisting ? " (on every start)" : ""}</span>
        ${p.running === false && html` <${Tag} kind="danger">Not running<//>`}<${Excerpt} ...${ex(p)} /></li>`)}</ul>
    </section>`}
    <${KnownIssues} issues=${knownIssues(q.data)} />`;
}

// On-demand startup report for one server since a point in time.
export function StartupCheck({ server, since, auto }) {
  const [st, setSt] = useState(null); // null | "loading" | report | Error
  const run = async () => {
    setSt("loading");
    try { setSt(await get(`/servers/${encodeURIComponent(server)}/health?since=${encodeURIComponent(since || "")}`)); }
    catch (e) { setSt(e); }
  };
  useEffect(() => { if (auto && !st) run(); }, [auto]);
  if (!st) return html`<${Btn} size="sm" icon="scroll-text" onClick=${run}>Check plugin startup<//>`;
  if (st === "loading") return html`<div class="startup"><${Skel} w="60%" /><${Skel} w="40%" /></div>`;
  if (st instanceof Error) return html`<div class="startup small" style="color:var(--danger)">Couldn't read ${server}'s log: ${st.message} <button type="button" class="linkbtn" onClick=${run}>Retry</button></div>`;
  if (st.restarted === false) return html`<div class="startup small muted"><${Icon} n="clock" cls="i-xs" />${server} hasn't restarted since the change — restart it, then check again.
    <button type="button" class="linkbtn" onClick=${run}>Check again</button></div>`;
  const plugins = st.plugins || [];
  const c = st.counts || {};
  const failed = c.failed ?? plugins.filter(p => p.status === "failed").length;
  const unknown = c.unknown ?? plugins.filter(p => p.status === "unknown").length;
  const summary = failed ? `${plural(failed, "plugin")} failed to enable`
    : unknown && !c.healthy ? `No startup found in the log yet for ${plural(unknown, "plugin")}`
    : unknown ? `${plural(c.healthy || 0, "plugin")} enabled cleanly · ${unknown} not in the log`
    : `All ${plural(plugins.length, "plugin")} enabled cleanly`;
  return html`<div class=${"startup" + (failed ? " is-bad" : unknown ? "" : " is-good")}>
    <div class="row wrap" style="gap:8px"><${Icon} n=${failed ? "circle-x" : unknown ? "circle-dashed" : "circle-check"} cls="i-sm" />
      <b class="small">${summary}</b>
      <span class="small muted">${st.restarted_at ? `restarted ${relTime(st.restarted_at)}` : "no restart found in the logs"}${st.startup_complete ? " · finished starting" : st.restarted_at ? " · still starting" : ""}</span>
      <button type="button" class="linkbtn" onClick=${run}>Refresh</button></div>
    ${plugins.length > 0 && html`<ul class="startup-list">${plugins.map(p => html`<li><span class="grow">${pname(p)} ${p.version || ""}</span>
      <${HealthTag} h=${p} />${p.status === "failed" && p.preexisting && html`<${Tag} title="This failure also happened on earlier starts — not caused by this change">on every start<//>`}
      ${p.status === "healthy" && (p.preexisting_errors || []).length > 0 && html`<span class="small muted">known issues</span>`}
      ${lines(p.excerpt) && p.status !== "healthy" && html`<div class="canary-ex"><${Excerpt} ...${ex(p)} label=${p.reason || "Log excerpt"} /></div>`}</li>`)}</ul>`}
    <${KnownIssues} issues=${knownIssues(st)} title="Known issues (already there before)" />
  </div>`;
}
