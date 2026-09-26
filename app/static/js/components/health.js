// Plugin startup health: the canary check the scheduler runs, and an on-demand "Check plugin startup"
// report for a server after a manual apply (GET /servers/{id}/health?since=).
import { html, useState, useEffect } from "../lib.js";
import { get } from "../api.js";
import { Icon, Btn, Tag, Skel } from "./ui.js";
import { relTime, plural } from "../fmt.js";

const HEALTH = { healthy: ["ok", "circle-check", "enabled cleanly"], failed: ["danger", "circle-x", "failed to enable"], unknown: ["", "clock", "waiting for restart"] };

export function HealthTag({ h }) {
  const [k, i, l] = HEALTH[h] || HEALTH.unknown;
  return html`<${Tag} kind=${k} icon=${i}>${l}<//>`;
}

// Redacted log lines, collapsed by default.
export const Excerpt = ({ text, label = "Log excerpt" }) => text ? html`<details class="excerpt">
  <summary>${label}</summary><pre class="log">${text}</pre></details>` : null;

// Canary rows + held plugins from overview.auto_update.
export function CanaryStatus({ au, compact }) {
  const rows = au?.canary || [];
  const held = au?.held || rows.filter(r => r.canary_health === "failed");
  if (!rows.length && !held.length) return null;
  if (compact) {
    if (held.length) return html`<p class="hl-canary-fail"><${Icon} n="circle-x" cls="i-sm" /><b>Canary check failed:</b>
      ${held.map(h => `${h.name || h.key.split(":").pop()} ${h.version} on ${h.server}`).join(", ")} — held back from the other servers
      <a class="link" href="#/updates">Details</a></p>`;
    return null;
  }
  return html`<div class="canary">
    <div class="field-label">Canary</div>
    <ul>${rows.map(r => html`<li>
      <span class="grow"><b>${r.name || r.key.split(":").pop()} ${r.version}</b> <span class="muted">on ${r.server}</span></span>
      <${HealthTag} h=${r.canary_health} />
      ${r.canary_health === "healthy" && r.soak_hours_left > 0 && html`<span class="small muted">${Math.ceil(r.soak_hours_left)} h soak left</span>`}
      ${r.canary_health === "failed" && html`<span class="small" style="color:var(--danger)">held</span>`}
      ${r.canary_health === "failed" && html`<div class="canary-ex"><${Excerpt} text=${r.health_detail?.excerpt} label=${r.health_detail?.reason || "Why it was held"} /></div>`}
    </li>`)}</ul>
  </div>`;
}

// On-demand startup report for one server since a point in time.
export function StartupCheck({ server, since, auto }) {
  const [st, setSt] = useState(null); // null | "loading" | report | Error
  useEffect(() => { if (auto && !st) run(); }, [auto]);
  const run = async () => {
    setSt("loading");
    try { setSt(await get(`/servers/${encodeURIComponent(server)}/health?since=${encodeURIComponent(since || "")}`)); }
    catch (e) { setSt(e); }
  };
  if (!st) return html`<${Btn} size="sm" icon="scroll-text" onClick=${run}>Check plugin startup<//>`;
  if (st === "loading") return html`<div class="startup"><${Skel} w="60%" /><${Skel} w="40%" /></div>`;
  if (st instanceof Error) return html`<div class="startup small" style="color:var(--danger)">Couldn't read ${server}'s log: ${st.message} <button type="button" class="linkbtn" onClick=${run}>Retry</button></div>`;
  const failed = (st.plugins || []).filter(p => p.status !== "enabled");
  if (!st.restarted) return html`<div class="startup small muted"><${Icon} n="clock" cls="i-xs" />${server} hasn't restarted since the change — restart it, then check again.
    <button type="button" class="linkbtn" onClick=${run}>Check again</button></div>`;
  return html`<div class=${"startup" + (failed.length || (st.errors || []).length ? " is-bad" : " is-good")}>
    <div class="row wrap" style="gap:8px"><${Icon} n=${failed.length ? "circle-x" : "circle-check"} cls="i-sm" />
      <b class="small">${failed.length ? `${plural(failed.length, "plugin")} didn't enable cleanly` : `All ${plural((st.plugins || []).length, "updated plugin")} enabled`}</b>
      <span class="small muted">restarted ${relTime(st.restarted_at)}${st.done ? " · server finished starting" : " · still starting"}</span>
      <button type="button" class="linkbtn" onClick=${run}>Refresh</button></div>
    ${(st.plugins || []).length > 0 && html`<ul class="startup-list">${st.plugins.map(p => html`<li><span class="grow">${p.name} ${p.version || ""}</span>
      <${Tag} kind=${p.status === "enabled" ? "ok" : "danger"}>${p.status}<//>${p.excerpt && html`<div class="canary-ex"><${Excerpt} text=${p.excerpt} /></div>`}</li>`)}</ul>`}
    ${(st.errors || []).length > 0 && html`<${Excerpt} label=${`${plural(st.errors.length, "other error")} in the log`} text=${st.errors.map(e => e.excerpt || e.line).join("\n")} />`}
  </div>`;
}
