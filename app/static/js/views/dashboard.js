// Dashboard — one headline ("is anything out of date?"), every server at a glance, recent real changes.
import { html, useMemo, useState, useEffect } from "../lib.js";
import { FIXTURES } from "../api.js";
import { useQuery, useStore } from "../store.js";
import { Icon, Btn, Tag, Platform, VerArrow, Skel, ErrorState, Empty } from "../components/ui.js";
import { openChangeset } from "../components/changeset.js";
import { checkUpdates } from "../actions.js";
import { relTime, absTime, plural } from "../fmt.js";
import { JOB_TITLES, KIND_ICON, jobTone, isActive, jobSummary, jobTitle } from "../jobs.js";
import { updateCounts, updateHeadline, updateDetail, restartList, checkLine, updatesOf } from "../summary.js";
import { CanaryStatus, causeShort } from "../components/health.js";

const MODE = { off: "Off", notify: "Notify only", apply: "Automatic" };
const pref = (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } };
const save = (k, v) => { try { localStorage.setItem(k, v); } catch {} };
// Move the viewer's "last looked" marker forward. keepalive lets it finish while the tab is closing.
const markSeen = () => fetch("/api/v2/seen", { method: "POST", keepalive: true, headers: { "X-Requested-With": "lgt-amp-sync", "Content-Type": "application/json" }, body: "{}" }).catch(() => {});

export function Dashboard() {
  const ov = useQuery("/overview");
  const up = useQuery("/updates");
  const mx = useQuery("/matrix");
  const jobs = useQuery("/jobs");
  const [view, setView] = useState(() => pref("amp.dash.view", "tiles"));
  const setV = (v) => { setView(v); save("amp.dash.view", v); };

  const byServer = useMemo(() => {
    const m = {};
    for (const u of updatesOf(up.data)) for (const t of u.targets || u.servers.map(s => ({ server: s }))) (m[t.server] ||= []).push({ ...u, from: t.from });
    return m;
  }, [up.data]);
  const versions = useMemo(() => {
    const v = {};
    for (const p of mx.data?.plugins || []) for (const [s, cell] of Object.entries(p.cells)) (v[s] ||= {})[p.key] = cell.version;
    return v;
  }, [mx.data]);

  const d = ov.data;
  // "Since you last looked" is frozen for this visit, so it doesn't vanish while being read;
  // the marker moves forward when the user leaves the Dashboard or hides the tab.
  const [since, setSince] = useState(null);
  useEffect(() => { if (d && since === null) setSince(d.since_last_visit || false); }, [d]);
  useEffect(() => {
    if (FIXTURES) return;
    const hide = () => document.visibilityState === "hidden" && markSeen();
    document.addEventListener("visibilitychange", hide);
    return () => { document.removeEventListener("visibilitychange", hide); markSeen(); };
  }, []);
  const c = d && updateCounts(d, up.data);
  const restarts = restartList(d);

  return html`
    <h1 class="sr-only">Dashboard</h1>
    ${ov.error ? html`<${ErrorState} error=${ov.error} retry=${ov.reload} />` : html`<${Headline} d=${d} c=${c} restarts=${restarts} since=${since} />`}

    <div class="dash-grid">
      <section aria-labelledby="srv-h">
        <div class="section-title"><h2 id="srv-h" style="font:inherit">Servers</h2><span class="spacer"></span>
          <span class="legend hide-sm" style="text-transform:none;letter-spacing:0;font-weight:500">
            <span><i class="dot dot-update"></i>update</span><span><i class="dot dot-drift"></i>drift</span><span><i class="dot dot-warn"></i>restart</span></span>
          <div class="seg" role="group" aria-label="Server layout" style="text-transform:none;letter-spacing:0">
            <button type="button" aria-pressed=${view === "tiles" ? "true" : "false"} onClick=${() => setV("tiles")}><${Icon} n="layout-dashboard" cls="i-xs" />Tiles</button>
            <button type="button" aria-pressed=${view === "list" ? "true" : "false"} onClick=${() => setV("list")}><${Icon} n="menu" cls="i-xs" />List</button>
          </div></div>
        ${!d && ov.error ? html`<p class="small muted">Server tiles appear once the overview loads.</p>`
          : !d ? html`<div class="tiles">${Array.from({ length: 9 }, () => html`<div class="tile tile-skel"><${Skel} w="55%" h=${12} /><${Skel} w="40%" h=${10} /><${Skel} w="80%" h=${8} /><${Skel} w="70%" h=${8} /><${Skel} w="60%" h=${8} /></div>`)}</div>`
          : view === "list" ? html`<${ServerList} servers=${d.servers} byServer=${byServer} />`
          : html`<div class="tiles">${d.servers.map(s => html`<${ServerTile} key=${s.id} s=${s} ups=${byServer[s.id] || []} loadingUps=${up.loading} vers=${versions[s.id]} checked=${!!d.last_check} />`)}</div>`}
      </section>
      <aside aria-labelledby="feed-h"><${Feed} q=${jobs} /></aside>
    </div>`;
}

// "Since you last looked (yesterday 9:14 PM): 3 new updates (LuckPerms 5.5.23, …) · 1 plugin stopped running · 2 changes by others".
function SinceLine({ s, pending }) {
  if (!s?.at) return null;
  const nu = s.new_updates_total ?? s.new_updates.length, nf = s.new_failures.length, nj = s.jobs_by_others.length;
  const backlog = s.is_backlog === true;
  if (!nu && !nf && !nj) return html`<p class="hl-since"><${Icon} n="clock" cls="i-sm" /><span>Nothing new since you last looked <span class="muted" title=${absTime(s.at)}>(${relTime(s.at)})</span></span></p>`;
  // The 3 newest by name, then "+N more" against the full count (lists are capped server-side).
  const names = (xs, f, total = xs.length, n = 3) => xs.slice(0, n).map(f).join(", ") + (total > n ? ` +${total - n} more` : "");
  const who = [...new Set(s.jobs_by_others.map(j => j.user || "someone"))];
  // Honest counts (backend's is_backlog): when every pending update, or the whole first-check backlog, is "new",
  // say they were found since then, not that they're news. Otherwise the new ones are a subset of what's waiting.
  const newest = names(s.new_updates, u => `${u.name} ${u.to_version}`, nu);
  const parts = [
    nu && (backlog
      ? html`<a class="link" href="#/updates">${nu === 1 ? "the only pending update was" : `all ${nu} pending updates were`} found since then</a> <span class="muted">(${newest})</span>`
      : html`<a class="link" href="#/updates">${plural(nu, "new update")}</a> <span class="muted">(${newest}${pending > nu ? `; ${pending} waiting in all` : ""})</span>`),
    nf && html`<a class="link" href=${`#/servers/${encodeURIComponent(s.new_failures[0].server)}`}>${plural(nf, "plugin")} stopped running</a> <span class="muted">(${names(s.new_failures, f => `${f.name} on ${f.server}`)})</span>`,
    nj && html`<a class="link" href=${`#/activity/${s.jobs_by_others[0].id}`}>${plural(nj, "change")} by ${who.slice(0, 2).join(" and ")}${who.length > 2 ? " and others" : ""}</a>`,
  ].filter(Boolean);
  return html`<p class="hl-since"><${Icon} n="clock" cls="i-sm" /><span><b>Since you last looked</b> <span class="muted" title=${absTime(s.at)}>(${relTime(s.at)})</span>: ${parts.map((x, i) => i ? [" · ", x] : x)}</span></p>`;
}

function Headline({ d, c, restarts, since }) {
  const checking = useStore(s => s.jobs.some(j => isActive(j.status) && j.title === "Update check"));
  if (!d) return html`<div class="headline" aria-busy="true"><span class="ub-icon skel"></span><div class="grow"><${Skel} w="40%" h=${14} /><${Skel} w="60%" h=${9} style="margin-top:8px" /></div><${Skel} w="160px" h=${32} /></div>`;
  const has = c.plugins > 0;
  if (!d.last_check) return html`<section class="headline is-new" aria-label="Update status">
    <span class="ub-icon"><${Icon} n="circle-dashed" cls="i-lg" /></span>
    <div class="grow" style="min-width:220px"><p class="hl-title">No update check yet</p>
      <p class="hl-meta">Run the first check to see which plugins have newer compatible versions. Checking never installs anything.</p></div>
    <${Btn} kind="primary" icon="refresh-cw" busy=${checking} onClick=${checkUpdates}>${checking ? "Checking…" : "Run first check"}<//>
  </section>`;
  return html`<section class=${"headline" + (has ? " has-updates" : "")} aria-label="Update status">
    <span class="ub-icon"><${Icon} n=${has ? "circle-arrow-up" : "circle-check"} cls="i-lg" /></span>
    <div class="grow" style="min-width:220px">
      <p class="hl-title">${updateHeadline(c)}${has && html`<span class="hl-detail"> · ${updateDetail(c)}</span>`}</p>
      <p class="hl-meta">${checkLine(d)} · auto-update <a class="link" href="#/updates">${(MODE[d.auto_update?.mode] || "off").toLowerCase()}</a>
        ${d.auto_update?.next_run ? ` · next ${relTime(d.auto_update.next_run)}` : ""}
        ${d.auto_update?.mode === "apply" && d.auto_update?.effective_canary && html` · canary <b>${d.auto_update.effective_canary}</b>${(() => { const soak = (d.auto_update.canary || []).filter(c => c.canary_health !== "failed"); return soak.length ? ` soaking ${plural(soak.length, "update")}, ${Math.ceil(Math.max(...soak.map(c => c.soak_hours_left || 0)))} h left` : ""; })()}`}</p>
<${CanaryStatus} au=${d.auto_update} compact=${true} />
      ${(() => { const f = d.servers.flatMap(s => (s.startup?.failed || []).map(p => ({ ...p, server: s.id }))); return f.length > 0 && html`<p class="hl-canary-fail"><${Icon} n="circle-x" cls="i-sm" />
        <b>${plural(f.length, "plugin")} ${f.length === 1 ? "is" : "are"} not running:</b> <span>${f.slice(0, 3).map(p => html`<a class="link" href=${`#/servers/${encodeURIComponent(p.server)}`}>${p.name} on ${p.server}</a>${causeShort(p.cause) ? html`<span class="muted"> (${causeShort(p.cause)})</span>` : ""}`).reduce((a, x, i) => i ? [...a, ", ", x] : [x], [])}${f.length > 3 ? ` +${f.length - 3} more` : ""}</span></p>`; })()}
      <${SinceLine} s=${since} pending=${c.plugins} />
      ${restarts.length > 0 && html`<p class="hl-restart"><${Icon} n="rotate-ccw" cls="i-sm" /><b class="tip" tabindex="0" data-tip=${restarts.map(r => r.server + (r.jobs?.length ? ` — ${r.jobs.flatMap(j => j.items || []).slice(0, 4).join("; ")}` : "")).join("\n")}>${plural(restarts.length, "server")} need${restarts.length === 1 ? "s" : ""} a restart</b>
        <span class="muted ellipsis">${restarts.map(r => r.server).join(", ")}</span></p>`}
    </div>
    <div class="row wrap" style="gap:8px">
      ${has ? html`<${Btn} kind="primary" icon="circle-arrow-up" onClick=${() => openChangeset("all", "Review: update everything")}>Review & update all<//>`
        : html`<${Btn} icon="refresh-cw" busy=${checking} onClick=${checkUpdates}>${checking ? "Checking…" : "Check now"}<//>`}
    </div>
  </section>`;
}

function Flags({ s, n, restart = true, compact = false }) {
  const driftTip = s.drift_plugins?.length ? `Differs from the network majority: ${s.drift_plugins.map(p => `${p.name} ${p.version} (network: ${p.expected})`).join(", ")}` : `${plural(s.drift, "plugin")} on a different version than the network majority`;
  return html`<div class="tile-flags">
    ${n > 0 && html`<span class="tag tag-update tip" tabindex="0" data-tip=${`${plural(n, "plugin")} can be updated on ${s.id}`} aria-label=${compact ? plural(n, "update") : undefined}>${compact ? html`<${Icon} n="circle-arrow-up" />${n}` : plural(n, "update")}</span>`}
    ${s.drift > 0 && html`<span class="tag tag-drift tip" tabindex="0" data-tip=${driftTip} aria-label=${`${s.drift} drift: ${driftTip}`}><${Icon} n="git-compare-arrows" />${s.drift}${compact ? "" : " drift"}</span>`}
    ${(s.startup?.failed?.length || s.startup_failed) > 0 && html`<span class="tag tag-danger tip" tabindex="0" data-tip=${`Not running after the last start: ${(s.startup?.failed || []).map(f => f.name).join(", ") || s.startup_failed}`}><${Icon} n="circle-x" />${s.startup?.failed?.length || s.startup_failed}${compact ? "" : " failed"}</span>`}
    ${restart && s.pending_restart && html`<span class="tag tag-warn tip" tabindex="0" data-tip="Files changed since the server last started"><${Icon} n="rotate-ccw" />Restart</span>`}
    ${!compact && s.is_source && html`<span class="tag tag-plain tip" tabindex="0" data-tip="Default deploy source"><${Icon} n="circle-dot" />source</span>`}
  </div>`;
}

function ServerTile({ s, ups, loadingUps, vers, checked }) {
  const href = `#/servers/${encodeURIComponent(s.id)}`;
  const empty = s.plugin_count === 0;
  const n = ups.length;
  return html`<article class=${"tile" + (empty ? " is-empty" : "")} aria-labelledby=${`t-${s.id}`}>
    <a class="tile-link" href=${href} aria-label=${`Open ${s.id}`} tabindex="-1"></a>
    <div class="tile-head">
      <a class="tile-name" id=${`t-${s.id}`} href=${href} data-nav>${s.id}</a>
      <${Flags} s=${s} n=${n} restart=${false} compact=${true} />
    </div>
    <div class="watch">
      ${empty ? html`<div class="watch-empty"><${Icon} n="blocks" cls="i-sm" />${s.note || (s.platform === "fabric" ? "Fabric server — no plugins" : "No plugins installed")}</div>`
        : loadingUps ? html`<${Skel} w="80%" h=${8} /><${Skel} w="66%" h=${8} />`
        : n ? ups.slice(0, 3).map(u => html`<div class="watch-row"><span class="n">${u.name}</span>
            <${VerArrow} from=${u.from} to=${u.to_version} compact=${true} /></div>`).concat(n > 3 ? [html`<a class="watch-more link" href=${href} aria-label=${`${n - 3} more updates on ${s.id}`}>+${n - 3} more</a>`] : [])
        : !checked ? html`<div class="watch-empty"><${Icon} n="circle-dashed" cls="i-sm" />Not checked yet · ${plural(s.plugin_count, "plugin")}</div>`
        : html`<div class="watch-empty ok"><${Icon} n="circle-check" cls="i-sm" />All tracked plugins current</div>`}
    </div>
    <div class="tile-foot">
      ${html`<span class="tile-plat ellipsis" title=${plural(s.plugin_count, "plugin")}>${s.platform === "velocity" ? "Velocity proxy" : html`<${Platform} p=${s.platform} mc=${s.mc_version} short=${!!s.pending_restart} />`}${s.is_source ? html` <span class="muted">· source</span>` : ""}</span>`}
      ${s.pending_restart && html`<span class="tag tag-warn tip" tabindex="0" data-tip="Files changed since the server last started — restart it, then mark it restarted on the server page"><${Icon} n="rotate-ccw" />Restart</span>`}
      ${n > 0 && html`<${Btn} size="sm" icon="circle-arrow-up" onClick=${() => openChangeset({ server: s.id }, `Review updates on ${s.id}`)}
        aria-label=${`Review ${plural(n, "update")} on ${s.id}`}>Review ${n}<//>`}
    </div>
  </article>`;
}

function ServerList({ servers, byServer }) {
  return html`<div class="panel tbl-wrap"><table class="tbl">
    <thead><tr><th scope="col">Server</th><th scope="col" class="hide-sm">Platform</th><th scope="col">Status</th><th scope="col" class="num hide-sm">Plugins</th><th class="col-actions"><span class="sr-only">Actions</span></th></tr></thead>
    <tbody>${servers.map(s => { const n = (byServer[s.id] || []).length; return html`<tr>
      <td><a class="strong" href=${`#/servers/${encodeURIComponent(s.id)}`}>${s.id}</a></td>
      <td class="hide-sm"><${Platform} p=${s.platform} mc=${s.mc_version} /></td>
      <td><div class="flags-left"><${Flags} s=${s} n=${n} /></div>${!n && !s.drift && !s.pending_restart && html`<span class="small muted">${s.plugin_count ? "Current" : s.note || "No plugins"}</span>`}</td>
      <td class="num hide-sm">${s.plugin_count}</td>
      <td class="col-actions">${n > 0 && html`<${Btn} size="sm" icon="circle-arrow-up" onClick=${() => openChangeset({ server: s.id }, `Review updates on ${s.id}`)}>Review ${n}<//>`}</td>
    </tr>`; })}</tbody></table></div>`;
}

// Real changes by default; checks and dry runs collapse behind a toggle.
function Feed({ q }) {
  const [all, setAll] = useState(() => pref("amp.feed.all", "0") === "1");
  const toggle = (v) => { setAll(v); save("amp.feed.all", v ? "1" : "0"); };
  const list = useMemo(() => {
    // Real changes only by default: no checks, dry runs, or jobs that changed nothing.
    const noop = (j) => !isActive(j.status) && j.counts && !j.counts.changed && j.kind !== "update-check";
    // Kinds this version doesn't know (e.g. from a removed feature) stay in Activity only.
    const src = (q.data || []).filter(j => JOB_TITLES[j.kind] && (all || isActive(j.status) || (!j.dry_run && j.kind !== "update-check" && !noop(j))));
    const out = [];
    for (const j of src) {
      const prev = out[out.length - 1];
      if (all && prev && j.kind === "update-check" && prev.kind === "update-check" && !isActive(j.status)) { prev.group = (prev.group || 1) + 1; continue; }
      out.push({ ...j });
    }
    return out.slice(0, 8);
  }, [q.data, all]);
  return html`<div class="section-title"><h2 id="feed-h" style="font:inherit">Recent changes</h2><span class="spacer"></span>
      <label class="switch small" style="text-transform:none;letter-spacing:0;font-weight:500"><input type="checkbox" checked=${all} onChange=${e => toggle(e.currentTarget.checked)} />Checks & dry runs</label></div>
    <div class="panel">
    ${q.error ? html`<div class="panel-body"><${ErrorState} error=${q.error} retry=${q.reload} /></div>`
      : q.loading ? html`<div class="feed">${Array.from({ length: 5 }, () => html`<div class="feed-item"><${Skel} w="22px" h=${22} r=${11} /><div><${Skel} w="85%" /><${Skel} w="50%" h=${8} /></div></div>`)}</div>`
      : !list.length ? html`<div class="empty" style="padding:24px"><p>No changes yet. Updates, deploys and removals appear here.</p></div>`
      : html`<ol class="feed">${list.map(j => { const undone = j.status === "undone" || j.undone_by; return html`<li class=${"feed-item" + (undone ? " is-undone" : "")}>
        <span class=${"feed-icon " + (undone ? "" : jobTone(j))}><${Icon} n=${isActive(j.status) ? "loader-circle" : KIND_ICON[j.kind] || "terminal"} cls=${"i-xs" + (isActive(j.status) ? " spin" : "")} /></span>
        <div style="min-width:0"><a class="feed-title" href=${`#/activity/${j.id}`}>${j.group ? `${j.group} update checks` : jobTitle(j)}</a>
          ${j.dry_run ? html` <${Tag}>dry run<//>` : ""}${undone ? html` <${Tag}>Reverted<//>` : j.undo_failed_by ? html` <${Tag} kind="danger">Undo failed<//>` : ""}
          <div class="feed-meta">${!j.group && jobSummary(j) ? html`<span class="feed-sum ellipsis">${jobSummary(j)}</span><span>·</span>` : ""}<span class="ellipsis">${j.user || "system"}</span>${j.servers?.length > 1 ? html`<span>·</span><span>${plural(j.servers.length, "server")}</span>` : ""}</div></div>
        <span class="when" title=${absTime(j.started || j.created)}>${j.group ? "last " : ""}${relTime(j.started || j.created)}</span>
      </li>`; })}</ol>
      <div class="panel-foot"><a class="link small" href="#/activity">All activity</a></div>`}
  </div>`;
}
