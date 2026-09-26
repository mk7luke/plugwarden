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
// Why a plugin isn't running, as classified by the backend, plus the next step.
const CAUSE = { port_in_use: "Port in use", missing_dependency: "Missing dependency", unsupported_version: "Unsupported version", config_error: "Config error" };
// Short form for one-line summaries (Dashboard headline): "port 24454 in use", "needs TheCore".
export function causeShort(c) {
  if (!c || !CAUSE[c.kind]) return "";
  if (c.kind === "port_in_use") return c.detail?.port ? `${c.detail.protocol || ""} port ${c.detail.port} in use`.trim() : "port in use";
  if (c.kind === "missing_dependency") { const d = (c.detail?.dependencies || []).filter(x => !x.installed).map(x => x.name); return d.length ? `needs ${d.join(", ")}` : "missing dependency"; }
  return CAUSE[c.kind].toLowerCase();
}
export function Cause({ cause, server }) {
  if (!cause || !CAUSE[cause.kind]) return null;
  const deps = cause.kind === "missing_dependency" ? (cause.detail?.dependencies || []).filter(d => !d.installed && d.on_source && d.source_jar && server) : [];
  return html`<div class="cause small"><${Icon} n="info" cls="i-xs" /><span><b>${CAUSE[cause.kind]}:</b> ${cause.suggestion || ""}
    ${deps.map(d => html` <a class="link" href=${`#/deploy?source=${encodeURIComponent(d.source)}&jar=${encodeURIComponent(d.source_jar)}&targets=${encodeURIComponent(server)}&action=install`}>Deploy ${d.name} from ${d.source}</a>`)}</span></div>`;
}

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
      ${st === "failed" && html`<div class="canary-ex"><${Cause} cause=${r.canary_health?.cause} server=${r.server} /><${Excerpt} ...${ex(r.canary_health)} label=${r.canary_health?.reason || "Why it was held"} /></div>`}
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
    ? report.preexisting_errors.map(e => ({ key: e.key, repeats: e.repeats, group_size: e.group_size, name: e.name || e.plugin, reason: brief(e.title || first(e)) || e.reason, excerpt: e.excerpt, match_index: e.match_index, log: e.log, line: e.line, seen_in_runs: e.seen_in_runs, level: e.level }))
    : (report.plugins || []).flatMap(p => (p.preexisting_errors || []).map(e => ({ key: p.key, repeats: e.repeats, group_size: e.group_size, name: pname(p), reason: brief(e.title || first(e)) || "error seen in earlier starts too", excerpt: e.excerpt, match_index: e.match_index, log: e.log, line: e.line, seen_in_runs: e.seen_in_runs, level: e.level })));
  // Errors that can't be judged yet (no earlier start to compare with) are only listed per plugin.
  const warn = (report.plugins || []).flatMap(p => (p.warnings || []).map(e => ({ key: p.key, group_size: e.group_size, name: pname(p), reason: `${brief(e.title || first(e))} (no earlier start to compare)`, excerpt: e.excerpt, match_index: e.match_index, log: e.log, line: e.line })));
  return [...known, ...warn];
}
// Plugins nagging about their own updates in the log; separate from issues. null = backend doesn't send them (old shape).
export function updateNotices(report) {
  if (!report) return null;
  const list = report.update_notices ?? (report.plugins?.some(p => p.update_notices) ? report.plugins.flatMap(p => p.update_notices || []) : null);
  return list && list.map(n => ({ name: n.name || n.plugin, title: n.title }));
}

// Warnings that were already there before the latest change (e.g. a UDP port already in use).
// "2 errors, 1 warning" — by the backend's level, else by the matched log line.
const levelOf = (i) => i.level || (/\b(ERROR|SEVERE)\b/.test(i.reason || "") || /\b(ERROR|SEVERE)\b/.test(lines(i.excerpt)) ? "error" : "warning");
function byLevel(issues) {
  const e = issues.filter(i => levelOf(i) === "error").length, w = issues.length - e;
  return [e && plural(e, "error"), w && plural(w, "warning")].filter(Boolean).join(", ");
}
// Banner rules (~~~~, ====) carry no information.
const DECOR = /^[\W_]{8,}$/;
// "A new release for X is available", "update available", "out of date"…: plugin nags, not problems.
const NAG = /new (release|version|update)|update (is )?available|updates? found|out of date|outdated|newer version|latest version is/i;

// Collapses a multi-line banner (same plugin, same log, lines next to each other) into one issue titled by its first real line.
function group(issues) {
  const out = [];
  for (const i of issues) {
    const prev = out[out.length - 1];
    if (prev && prev.name === i.name && prev.log === i.log && i.line != null && prev.lastLine != null && i.line - prev.lastLine <= 2 && levelOf(prev) === levelOf(i)) {
      prev.extra += 1 + (i.extra_lines || 0); prev.lastLine = i.line;
      if (DECOR.test(prev.reason) && !DECOR.test(i.reason)) prev.reason = i.reason;
      continue;
    }
    out.push({ ...i, extra: i.lines ? i.lines - 1 : i.group_size ? i.group_size - 1 : 0, lastLine: i.line });
  }
  return out.filter(i => !DECOR.test(i.reason || ""));
}

export function KnownIssues({ issues, notices, title = "Known issues on this server", sub = "seen in earlier starts — not caused by recent changes" }) {
  const [showWarn, setShowWarn] = useState(false);
  const [wid] = useState(() => "kw-" + Math.random().toString(36).slice(2, 8));
  issues = issues || [];
  if (!issues.length && !notices?.length) return null;
  // The backend groups banners and splits out update nags; the client-side versions are a fallback for the old shape.
  const nags = notices ?? issues.filter(i => NAG.test(i.reason || ""));
  const rest = notices ? issues : issues.filter(i => !nags.includes(i));
  const real = rest.some(i => i.group_size != null) ? rest.filter(i => !DECOR.test(i.reason || "")).map(i => ({ ...i, extra: (i.group_size || 1) - 1 })) : group(rest);
  // The same message logged by several code paths reads as one issue with a count.
  const merged = [...real.reduce((m, i) => { const k = `${i.name}|${i.reason}`; const g = m.get(k); g ? g.n += (i.repeats || 1) : m.set(k, { ...i, n: i.repeats || 1 }); return m; }, new Map()).values()];
  const errors = merged.filter(i => levelOf(i) === "error"), warnings = merged.filter(i => levelOf(i) !== "error");
  const nagPlugins = [...new Set(nags.map(i => i.name).filter(Boolean))];
  if (!merged.length && !nagPlugins.length) return null;
  const row = (i) => html`<li><span class="small"><b>${i.name || pname(i) || "Server"}</b> — ${i.reason}${i.n > 1 ? html` <span class="muted">×${i.n}</span>` : ""}</span>
      ${i.seen_in_runs > 1 && html`<span class="small muted"> · in the last ${i.seen_in_runs} starts</span>`}
      <${Excerpt} ...${ex(i)} label=${i.extra ? `Log excerpt (+${plural(i.extra, "line")})` : "Log excerpt"} /></li>`;
  return html`<section class="known" aria-label=${title}>
    <div class="row" style="gap:8px"><${Icon} n="info" cls="i-sm" /><b class="small">${title}</b>
      <span class="small muted">${merged.length ? `${byLevel(merged)} ${sub}` : ""}</span></div>
    ${errors.length > 0 && html`<ul>${errors.map(row)}</ul>`}
    ${warnings.length > 0 && (!errors.length && warnings.length <= 2 ? html`<ul class="known-warn">${warnings.map(row)}</ul>`
      : html`<button type="button" class="linkbtn small known-more" aria-expanded=${showWarn ? "true" : "false"} aria-controls=${wid} onClick=${() => setShowWarn(v => !v)}>
          <${Icon} n="chevron-right" cls=${"i-xs" + (showWarn ? " rot90" : "")} />${showWarn ? "Hide" : "Show"} ${plural(warnings.length, "warning")}</button>
        <ul class="known-warn" id=${wid} hidden=${!showWarn}>${warnings.map(row)}</ul>`)}
    ${nagPlugins.length > 0 && html`<p class="small muted known-nag"><${Icon} n="circle-arrow-up" cls="i-xs" />${plural(nagPlugins.length, "plugin")} announce${nagPlugins.length === 1 ? "s" : ""} updates in ${nagPlugins.length === 1 ? "its" : "their"} logs (${nagPlugins.slice(0, 4).join(", ")}${nagPlugins.length > 4 ? ` +${nagPlugins.length - 4}` : ""}) — <a class="link" href="#/updates">see Updates</a></p>`}
  </section>`;
}
// The failure's own log line is already the card's excerpt; don't list it again as "also logged".
const sameLine = (i, p) => i.log === p.log && i.line != null && i.line === p.line;

export function ServerKnownIssues({ server }) {
  const q = useQuery(`/servers/${encodeURIComponent(server)}/health`);
  const failing = (q.data?.plugins || []).filter(p => p.status === "failed");
  // A plugin that isn't running is shown once, in the red card, with its logged errors; not again under Known issues.
  const all = knownIssues(q.data);
  const isFailing = (i) => failing.some(p => (i.key && i.key === p.key) || (!i.key && i.name === pname(p)));
  const related = (p) => all.filter(i => (i.key ? i.key === p.key : i.name === pname(p)) && i.level !== "warning" && !sameLine(i, p));
  return html`${failing.length > 0 && html`<section class="known is-failing" aria-label="Plugins failing at startup">
      <div class="row" style="gap:8px"><${Icon} n="circle-x" cls="i-sm" /><b class="small">${plural(failing.length, "plugin")} ${failing.some(p => p.running === false) ? "not running" : "failed to start"} after the last start</b>
        <span class="small muted">${q.data.restarted_at ? `started ${relTime(q.data.restarted_at)}` : ""}</span></div>
      <ul>${failing.map(p => html`<li><span class="small"><b>${pname(p)}</b> — ${p.reason}${p.preexisting && !/every start/.test(p.reason || "") ? " (on every start)" : ""}</span>
        ${p.running === false && html` <${Tag} kind="danger">Not running<//>`}<${Cause} cause=${p.cause} server=${server} /><${Excerpt} ...${ex(p)} />
        ${related(p).map(i => html`<div class="related small"><span class="muted">Also logged:</span> ${i.reason}${i.repeats > 1 ? ` ×${i.repeats}` : ""}
          <${Excerpt} ...${ex(i)} label=${(i.group_size || 1) > 1 ? `Log excerpt (+${plural(i.group_size - 1, "line")})` : "Log excerpt"} /></div>`)}</li>`)}</ul>
    </section>`}
    <${KnownIssues} issues=${all.filter(i => !isFailing(i))} notices=${updateNotices(q.data)} />`;
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
    <${KnownIssues} issues=${knownIssues(st)} notices=${updateNotices(st)} title="Known issues (already there before)" />
  </div>`;
}
