// Dashboard — one headline ("is anything out of date?"), every server at a glance, recent real changes.
import { html, useMemo, useState } from "../lib.js";
import { useQuery } from "../store.js";
import { Icon, Btn, Tag, Platform, VerArrow, Skel, ErrorState, Empty } from "../components/ui.js";
import { openChangeset } from "../components/changeset.js";
import { checkUpdates } from "../actions.js";
import { relTime, plural } from "../fmt.js";
import { JOB_TITLES, KIND_ICON, jobTone, isActive, jobSummary } from "../jobs.js";
import { updateCounts, updateHeadline, updateDetail, restartList, checkLine, updatesOf } from "../summary.js";

const MODE = { off: "Off", notify: "Notify only", apply: "Automatic" };
const pref = (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } };
const save = (k, v) => { try { localStorage.setItem(k, v); } catch {} };

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
  const c = d && updateCounts(d, up.data);
  const restarts = restartList(d);

  return html`
    <h1 class="sr-only">Dashboard</h1>
    ${ov.error ? html`<${ErrorState} error=${ov.error} retry=${ov.reload} />` : html`<${Headline} d=${d} c=${c} restarts=${restarts} />`}

    <div class="dash-grid">
      <section aria-labelledby="srv-h">
        <div class="section-title"><h2 id="srv-h" style="font:inherit">Servers</h2><span class="spacer"></span>
          <span class="legend hide-sm" style="text-transform:none;letter-spacing:0;font-weight:500">
            <span><i class="dot dot-update"></i>update</span><span><i class="dot dot-drift"></i>drift</span><span><i class="dot dot-warn"></i>restart</span></span>
          <div class="seg" role="group" aria-label="Server layout" style="text-transform:none;letter-spacing:0">
            <button type="button" aria-pressed=${view === "tiles" ? "true" : "false"} onClick=${() => setV("tiles")}><${Icon} n="layout-dashboard" cls="i-xs" />Tiles</button>
            <button type="button" aria-pressed=${view === "list" ? "true" : "false"} onClick=${() => setV("list")}><${Icon} n="menu" cls="i-xs" />List</button>
          </div></div>
        ${!d ? html`<div class="tiles">${Array.from({ length: 9 }, () => html`<div class="tile tile-skel"><${Skel} w="55%" h=${12} /><${Skel} w="40%" h=${10} /><${Skel} w="80%" h=${8} /><${Skel} w="70%" h=${8} /><${Skel} w="60%" h=${8} /></div>`)}</div>`
          : view === "list" ? html`<${ServerList} servers=${d.servers} byServer=${byServer} />`
          : html`<div class="tiles">${d.servers.map(s => html`<${ServerTile} key=${s.id} s=${s} ups=${byServer[s.id] || []} loadingUps=${up.loading} vers=${versions[s.id]} />`)}</div>`}
      </section>
      <aside aria-labelledby="feed-h"><${Feed} q=${jobs} /></aside>
    </div>`;
}

function Headline({ d, c, restarts }) {
  if (!d) return html`<div class="headline" aria-busy="true"><span class="ub-icon skel"></span><div class="grow"><${Skel} w="40%" h=${14} /><${Skel} w="60%" h=${9} style="margin-top:8px" /></div><${Skel} w="160px" h=${32} /></div>`;
  const has = c.plugins > 0;
  return html`<section class=${"headline" + (has ? " has-updates" : "")} aria-label="Update status">
    <span class="ub-icon"><${Icon} n=${has ? "circle-arrow-up" : "circle-check"} cls="i-lg" /></span>
    <div class="grow" style="min-width:220px">
      <p class="hl-title">${updateHeadline(c)}${has && html`<span class="hl-detail"> · ${updateDetail(c)}</span>`}</p>
      <p class="hl-meta">${checkLine(d)} · auto-update <a class="link" href="#/updates">${(MODE[d.auto_update?.mode] || "off").toLowerCase()}</a>
        ${d.auto_update?.next_run ? ` · next ${relTime(d.auto_update.next_run)}` : ""}</p>
${restarts.length > 0 && html`<p class="hl-restart"><${Icon} n="rotate-ccw" cls="i-sm" /><b class="tip" tabindex="0" data-tip=${restarts.map(r => r.server + (r.jobs?.length ? ` — ${r.jobs.flatMap(j => j.items || []).slice(0, 4).join("; ")}` : "")).join("\n")}>${plural(restarts.length, "server")} need${restarts.length === 1 ? "s" : ""} a restart</b>
        <span class="muted ellipsis">${restarts.map(r => r.server).join(", ")}</span></p>`}
    </div>
    <div class="row wrap" style="gap:8px">
      ${has ? html`<${Btn} kind="primary" icon="circle-arrow-up" onClick=${() => openChangeset("all", "Review: update everything")}>Review & update all<//>`
        : html`<${Btn} icon="refresh-cw" onClick=${checkUpdates}>Check now<//>`}
    </div>
  </section>`;
}

function Flags({ s, n, restart = true }) {
  const driftTip = s.drift_plugins?.length ? `Differs from the network majority: ${s.drift_plugins.map(p => `${p.name} ${p.version} (network: ${p.expected})`).join(", ")}` : `${plural(s.drift, "plugin")} on a different version than the network majority`;
  return html`<div class="tile-flags">
    ${n > 0 && html`<span class="tag tag-update tip" tabindex="0" data-tip=${`${plural(n, "plugin")} can be updated on ${s.id}`}>${plural(n, "update")}</span>`}
    ${s.drift > 0 && html`<span class="tag tag-drift tip" tabindex="0" data-tip=${driftTip}><${Icon} n="git-compare-arrows" />${s.drift} drift</span>`}
    ${restart && s.pending_restart && html`<span class="tag tag-warn tip" tabindex="0" data-tip="Files changed since the server last started"><${Icon} n="rotate-ccw" />Restart</span>`}
    ${s.is_source && html`<span class="tag tag-plain tip" tabindex="0" data-tip="Default deploy source"><${Icon} n="circle-dot" />source</span>`}
  </div>`;
}

function ServerTile({ s, ups, loadingUps, vers }) {
  const href = `#/servers/${encodeURIComponent(s.id)}`;
  const empty = s.plugin_count === 0;
  const n = ups.length;
  return html`<article class=${"tile" + (empty ? " is-empty" : "")} aria-labelledby=${`t-${s.id}`}>
    <a class="tile-link" href=${href} aria-label=${`Open ${s.id}`} tabindex="-1"></a>
    <div class="tile-head">
      <a class="tile-name" id=${`t-${s.id}`} href=${href}>${s.id}</a>
      <${Flags} s=${s} n=${n} restart=${false} />
    </div>
    <div class="watch">
      ${empty ? html`<div class="watch-empty"><${Icon} n="blocks" cls="i-sm" />${s.note || (s.platform === "fabric" ? "Fabric server — no plugins" : "No plugins installed")}</div>`
        : loadingUps ? html`<${Skel} w="80%" h=${8} /><${Skel} w="66%" h=${8} />`
        : n ? ups.slice(0, 3).map(u => html`<div class="watch-row"><span class="n">${u.name}</span>
            <${VerArrow} from=${u.from} to=${u.to_version} /></div>`).concat(n > 3 ? [html`<a class="watch-more link" href=${href} aria-label=${`${n - 3} more updates on ${s.id}`}>+${n - 3} more</a>`] : [])
        : html`<div class="watch-empty ok"><${Icon} n="circle-check" cls="i-sm" />All tracked plugins current</div>`}
    </div>
    <div class="tile-foot">
      ${s.pending_restart ? html`<span class="tag tag-warn tip" tabindex="0" data-tip="Files changed since the server last started — restart it, then mark it restarted on the server page"><${Icon} n="rotate-ccw" />Restart needed</span>`
        : html`<span class="ellipsis" title=${plural(s.plugin_count, "plugin")}>${s.platform === "velocity" ? "Velocity proxy" : html`<${Platform} p=${s.platform} mc=${s.mc_version} />`}</span>`}
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
      <td><${Flags} s=${s} n=${n} />${!n && !s.drift && !s.pending_restart && html`<span class="small muted">${s.plugin_count ? "Current" : s.note || "No plugins"}</span>`}</td>
      <td class="num hide-sm">${s.plugin_count}</td>
      <td class="col-actions">${n > 0 && html`<${Btn} size="sm" icon="circle-arrow-up" onClick=${() => openChangeset({ server: s.id }, `Review updates on ${s.id}`)}>Review ${n}<//>`}</td>
    </tr>`; })}</tbody></table></div>`;
}

// Real changes by default; checks and dry runs collapse behind a toggle.
function Feed({ q }) {
  const [all, setAll] = useState(() => pref("amp.feed.all", "0") === "1");
  const toggle = (v) => { setAll(v); save("amp.feed.all", v ? "1" : "0"); };
  const list = useMemo(() => {
    const src = (q.data || []).filter(j => all || isActive(j.status) || (!j.dry_run && j.kind !== "update-check"));
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
        <div style="min-width:0"><a class="feed-title" href=${`#/activity/${j.id}`}>${j.group ? `${j.group} update checks` : JOB_TITLES[j.kind] || j.kind}</a>
          ${j.dry_run ? html` <${Tag}>dry run<//>` : ""}${undone ? html` <${Tag}>reverted<//>` : ""}
          ${j.summary && !j.group && html`<div class="feed-sum">${jobSummary(j)}</div>`}
          <div class="feed-meta"><span>${j.group ? "last " : ""}${relTime(j.started || j.created)}</span><span>·</span><span class="ellipsis">${j.user || "system"}</span>${j.servers?.length ? html`<span>·</span><span>${plural(j.servers.length, "server")}</span>` : ""}</div></div>
      </li>`; })}</ol>
      <div class="panel-foot"><a class="link small" href="#/activity">All activity</a></div>`}
  </div>`;
}
