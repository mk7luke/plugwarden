// Dashboard — "is anything out of date?" at a glance, per server, with one-click fixes.
import { html, useMemo } from "../lib.js";
import { useQuery } from "../store.js";
import { Icon, Btn, Tag, Platform, VerArrow, Skel, ErrorState, PageHead } from "../components/ui.js";
import { openUpdateAll, applyUpdates, checkUpdates } from "../actions.js";
import { relTime, plural } from "../fmt.js";
import { JOB_TITLES, KIND_ICON, jobTone, isActive } from "../jobs.js";

const MODE = { off: "Off", notify: "Notify only", apply: "Automatic" };

export function Dashboard() {
  const ov = useQuery("/overview");
  const up = useQuery("/updates");
  const mx = useQuery("/matrix");
  const jobs = useQuery("/jobs");

  const byServer = useMemo(() => {
    const m = {};
    for (const u of up.data || []) for (const s of u.servers) (m[s] ||= []).push(u);
    return m;
  }, [up.data]);
  const counts = useMemo(() => {
    const c = {};
    for (const p of mx.data?.plugins || []) for (const [s, cell] of Object.entries(p.cells)) {
      const o = c[s] ||= { current: 0, outdated: 0, drift: 0, unknown: 0 };
      const k = cell.status === "current" || cell.status === "outdated" || cell.status === "drift" ? cell.status : "unknown";
      o[k]++;
    }
    return c;
  }, [mx.data]);

  const versions = useMemo(() => {
    const v = {};
    for (const p of mx.data?.plugins || []) for (const [s, cell] of Object.entries(p.cells)) (v[s] ||= {})[p.key] = cell.version;
    return v;
  }, [mx.data]);

  if (ov.error) return html`<${PageHead} title="Dashboard" /><${ErrorState} error=${ov.error} retry=${ov.reload} />`;
  const d = ov.data;
  const t = d && { ...d.totals, updates: d.totals.update_plugins ?? d.totals.updates, installs: d.totals.update_plugins != null ? d.totals.updates : null };
  const affected = new Set((up.data || []).flatMap(u => u.servers)).size;

  return html`
    <${PageHead} title="Network overview" sub=${d ? `${plural(t.servers, "server")} · auto-update ${MODE[d.auto_update?.mode]?.toLowerCase() || "off"}` : "Loading network…"}>
      <a class="btn" href="#/deploy"><${Icon} n="rocket" cls="i-sm" />New deploy</a>
    <//>

    <section class="kpis" aria-label="Key figures">
      ${d ? html`
        <a class="kpi" href="#/servers"><span class="label"><${Icon} n="server" cls="i-xs" />Servers</span><span class="value">${t.servers}</span>
          <span class="foot">${t.pending_restart ? `${t.pending_restart} pending restart` : `${d.servers.filter(s => s.eligible_target).length} deploy targets`}</span></a>
        <a class="kpi" href="#/plugins"><span class="label"><${Icon} n="package" cls="i-xs" />Plugins tracked</span><span class="value">${t.plugins}</span>
          <span class="foot">unique across network</span></a>
        <a class=${"kpi" + (t.updates ? " is-update" : "")} href="#/updates"><span class="label"><${Icon} n="circle-arrow-up" cls="i-xs" />Updates available</span><span class="value">${t.updates}</span>
          <span class="foot">${t.updates ? `${t.installs ? plural(t.installs, "install") + " on " : "on "}${plural(affected, "server")}` : "all current"}</span></a>
        <a class=${"kpi" + (t.drift ? " is-drift" : "")} href="#/plugins?status=drift"><span class="label"><${Icon} n="git-compare-arrows" cls="i-xs" />Version drift</span><span class="value">${t.drift}</span>
          <span class="foot">${t.drift ? "plugins differ between servers" : "versions aligned"}</span></a>
        <a class="kpi" href="#/activity"><span class="label"><${Icon} n="clock" cls="i-xs" />Last check</span><span class="value small">${relTime(d.last_check)}</span>
          <span class="foot">${d.auto_update?.next_run ? `next ${relTime(d.auto_update.next_run)}` : "no schedule"}</span></a>`
      : Array.from({ length: 5 }, () => html`<div class="kpi"><${Skel} w="50%" /><${Skel} w="36%" h=${26} style="margin:8px 0 6px" /><${Skel} w="70%" h=${8} /></div>`)}
    </section>

    ${d && html`<div class=${"update-bar" + (t.updates ? "" : " calm")}>
      <span class="ub-icon"><${Icon} n=${t.updates ? "zap" : "circle-check"} cls="i-lg" /></span>
      <div class="ub-text">
        ${t.updates
          ? html`<b>${plural(t.updates, "update")} ready across ${plural(affected, "server")}</b><span>Preview as a dry run, then apply. Old jars are backed up for one-click undo.</span>`
          : html`<b>Everything is up to date</b><span>Last checked ${relTime(d.last_check)}. ${d.auto_update?.mode !== "off" ? `Next automatic check ${relTime(d.auto_update.next_run)}.` : "Automatic checks are off."}</span>`}
      </div>
      <div class="ub-actions">
        <a class="btn" href="#/updates"><${Icon} n="calendar-clock" cls="i-sm" />Auto-update: ${MODE[d.auto_update?.mode] || "Off"}</a>
        ${t.updates ? html`<${Btn} kind="primary" icon="zap" onClick=${openUpdateAll}>Update all<//>` : html`<${Btn} icon="refresh-cw" onClick=${checkUpdates}>Check now<//>`}
      </div>
    </div>`}

    <div class="dash-grid">
      <section aria-labelledby="srv-h">
        <h2 class="section-title" id="srv-h">Servers<span class="spacer"></span>
          <span class="legend" style="text-transform:none;letter-spacing:0;font-weight:500">
            <span><i class="dot dot-ok"></i>current</span><span><i class="dot dot-update"></i>update</span><span><i class="dot dot-drift"></i>drift</span></span></h2>
        <div class="tiles">
          ${d ? d.servers.map(s => html`<${ServerTile} key=${s.id} s=${s} ups=${byServer[s.id] || []} c=${counts[s.id]} loadingUps=${up.loading} vers=${versions[s.id]} />`)
            : Array.from({ length: 8 }, () => html`<div class="tile" style="padding:14px;gap:10px;min-height:196px"><${Skel} w="55%" h=${12} /><${Skel} w="35%" h=${8} /><${Skel} h=${4} style="margin:8px 0" /><${Skel} w="80%" h=${8} /><${Skel} w="70%" h=${8} /></div>`)}
        </div>
      </section>
      <aside class="stack" aria-label="Recent activity">
        <h2 class="section-title">Recent activity<span class="spacer"></span><a class="link small" href="#/activity" style="text-transform:none;letter-spacing:0">View all</a></h2>
        <div class="panel"><${Feed} q=${jobs} /></div>
      </aside>
    </div>`;
}

function ServerTile({ s, ups, c, loadingUps, vers }) {
  const href = `#/servers/${encodeURIComponent(s.id)}`;
  const proxy = s.platform === "velocity";
  const empty = s.plugin_count === 0;
  const total = c ? c.current + c.outdated + c.drift + c.unknown : 0;
  const pct = (n) => `${(n / total) * 100}%`;
  const updateServer = (e) => {
    e.preventDefault();
    applyUpdates(ups.map(u => ({ key: u.key, servers: [s.id] })), { title: `Update ${s.id}` });
  };
  return html`<article class=${"tile" + (proxy ? " is-proxy" : "") + (empty ? " is-empty" : "")} aria-labelledby=${`t-${s.id}`}>
    <a class="tile-link" href=${href} aria-label=${`Open ${s.id}`}></a>
    <div class="tile-head">
      <div style="min-width:0">
        <div class="tile-name" id=${`t-${s.id}`}>${s.id}</div>
        <div class="tile-meta"><${Platform} p=${s.platform} mc=${s.mc_version} /></div>
      </div>
      <div class="tile-flags">
        ${s.is_source && html`<${Tag} kind="accent" icon="circle-dot" title="Default deploy source">source<//>`}
        ${s.pending_restart && html`<${Tag} kind="warn" icon="rotate-ccw" title="Files changed since the server last started">Restart<//>`}
        ${s.updates > 0 && html`<${Tag} kind="update">${s.updates}<span class="sr-only"> updates</span><//>`}
        ${s.drift > 0 && html`<${Tag} kind="drift" icon="git-compare-arrows" title="Plugins on a different version than other servers">${s.drift}<//>`}
      </div>
    </div>
    ${!empty && html`<div class="meter" role="img" aria-label=${c ? `${c.current} current, ${c.outdated} outdated, ${c.drift} drifted, ${c.unknown} untracked` : "loading"}>
      ${c && total ? html`<span class="m-ok" style=${`width:${pct(c.current)}`}></span><span class="m-update" style=${`width:${pct(c.outdated)}`}></span><span class="m-drift" style=${`width:${pct(c.drift)}`}></span><span class="m-unknown" style=${`width:${pct(c.unknown)}`}></span>` : null}
    </div>`}
    <div class="watch">
      ${empty ? html`<div class="watch-empty"><${Icon} n="blocks" cls="i-sm" style="color:var(--fg-3)" />No plugins — ${s.note || (s.platform === "fabric" ? "Fabric modded server" : "empty plugins folder")}</div>`
        : loadingUps ? html`<${Skel} w="80%" h=${8} /><${Skel} w="66%" h=${8} /><${Skel} w="72%" h=${8} />`
        : ups.length ? html`${ups.slice(0, 4).map(u => html`<div class="watch-row"><i class="dot dot-update"></i><span class="n">${u.name}</span>
            <${VerArrow} from=${vers?.[u.key] || u.from_versions[0]} to=${u.to_version} /></div>`)}
            ${ups.length > 4 && html`<span class="watch-more">+${ups.length - 4} more</span>`}`
        : html`<div class="watch-empty"><${Icon} n="circle-check" cls="i-sm" />All tracked plugins current</div>`}
    </div>
    <div class="tile-foot">
      <span>${plural(s.plugin_count, "plugin")}${proxy ? " · Velocity" : ""}</span>
      ${ups.length > 0 && html`<${Btn} size="sm" icon="circle-arrow-up" onClick=${updateServer} aria-label=${`Update ${ups.length} plugins on ${s.id}`}>Update ${ups.length}<//>`}
    </div>
  </article>`;
}


function Feed({ q }) {
  if (q.error) return html`<div class="panel-body"><${ErrorState} error=${q.error} retry=${q.reload} /></div>`;
  if (q.loading) return html`<div class="feed">${Array.from({ length: 5 }, () => html`<div class="feed-item"><${Skel} w="22px" h=${22} r=${11} /><div><${Skel} w="85%" /><${Skel} w="50%" h=${8} /></div></div>`)}</div>`;
  const list = (q.data || []).slice(0, 7);
  if (!list.length) return html`<div class="empty" style="padding:28px"><p>No jobs yet. Deploys, update checks and undos will appear here.</p></div>`;
  return html`<ol class="feed">${list.map(j => html`<li class="feed-item">
    <span class=${"feed-icon " + jobTone(j)}><${Icon} n=${isActive(j.status) ? "loader-circle" : KIND_ICON[j.kind] || "terminal"} cls=${"i-xs" + (isActive(j.status) ? " spin" : "")} /></span>
    <div style="min-width:0"><a class="feed-title" href=${`#/activity/${j.id}`}>${JOB_TITLES[j.kind] || j.kind}${j.dry_run ? html` <span class="tag" style="vertical-align:1px">dry run</span>` : ""}</a>
      ${j.summary && html`<div class="feed-sum">${j.summary}</div>`}
      <div class="feed-meta"><span>${relTime(j.started || j.created)}</span><span>·</span><span class="ellipsis">${j.user || "system"}</span>${j.servers?.length ? html`<span>·</span><span>${plural(j.servers.length, "server")}</span>` : ""}</div></div>
  </li>`)}</ol>`;
}
