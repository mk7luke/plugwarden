// Plugins matrix — rows = plugins, columns = servers, cells = installed version.
import { html, useState, useMemo, useEffect } from "../lib.js";
import { useQuery } from "../store.js";
import { openRemove } from "../components/removedialog.js";
import { ineligible } from "./deploy.js";
import { Icon, Btn, SkelRows, ErrorState, Empty, PageHead, Skel } from "../components/ui.js";
import { applyUpdates } from "../actions.js";
import { navigate } from "../router.js";
import { plural } from "../fmt.js";

const FILTERS = [["all", "All"], ["outdated", "Updates"], ["drift", "Drift"], ["unknown", "Untracked"]];
const LABEL = { current: "up to date", outdated: "update available", drift: "differs from other servers", unknown: "source unknown", pinned: "pinned", ignored: "ignored" };

const shortVer = (v) => !v ? "?" : v.replace(/-SNAPSHOT-/i, "-S").replace(/-build-/i, "-b");

export function Matrix({ query }) {
  const q = useQuery("/matrix");
  const srv = useQuery("/servers");
  const st = useQuery("/settings");
  const [search, setSearch] = useState(query.q || "");
  const [filter, setFilter] = useState(query.status || "all");
  const [hideNA, setHideNA] = useState(true);
  useEffect(() => { if (query.q != null) setSearch(query.q); if (query.status) setFilter(query.status); }, [query.q, query.status]);

  const source = st.data?.default_source;
  const pf = Object.fromEntries((q.data?.server_info || srv.data || []).map(s => [s.id, s]));
  const cols = useMemo(() => (q.data?.servers || []).filter(id => !hideNA || (pf[id]?.plugin_count ?? 1) > 0), [q.data, hideNA, srv.data]);

  const rows = useMemo(() => {
    let r = (q.data?.plugins || []).map(p => {
      const cells = Object.values(p.cells);
      const versions = new Set(cells.map(c => c.version));
      return { ...p, drift: p.drift ?? (versions.size > 1 || cells.some(c => c.status === "drift")), outdated: cells.filter(c => c.status === "outdated").length, unknown: cells.every(c => c.status === "unknown") };
    });
    if (filter === "outdated") r = r.filter(p => p.outdated);
    if (filter === "drift") r = r.filter(p => p.drift);
    if (filter === "unknown") r = r.filter(p => p.unknown);
    if (search) { const s = search.toLowerCase(); r = r.filter(p => p.name.toLowerCase().includes(s) || p.key.includes(s)); }
    return r;
  }, [q.data, filter, search]);

  const counts = useMemo(() => {
    const all = (q.data?.plugins || []);
    return {
      all: all.length,
      outdated: all.filter(p => Object.values(p.cells).some(c => c.status === "outdated")).length,
      drift: all.filter(p => p.drift ?? (new Set(Object.values(p.cells).map(c => c.version)).size > 1)).length,
      unknown: all.filter(p => Object.values(p.cells).every(c => c.status === "unknown")).length,
    };
  }, [q.data]);

  const updateEverywhere = (p) => applyUpdates([{ key: p.key, servers: Object.entries(p.cells).filter(([, c]) => c.status === "outdated").map(([s]) => s) }], { title: `Update ${p.name} everywhere` });
  const align = (p) => {
    const src = p.cells[source];
    const targets = Object.entries(p.cells).filter(([s, c]) => s !== source && c.version !== src.version && pf[s] && !ineligible(pf[s], pf[source])).map(([s]) => s);
    navigate(`#/deploy?action=replace&jar=${encodeURIComponent(src.jar)}&targets=${targets.map(encodeURIComponent).join(",")}`);
  };
  const remove = (p) => openRemove(p);

  return html`
    <${PageHead} title="Plugins matrix" sub="Every plugin on every server. Spot what's outdated or out of step at a glance.">
      <div class="legend" aria-label="Legend">
        <span><i class="lg-sw" style="background:var(--ok-soft);border-color:var(--ok-line)"></i>Current</span>
        <span><i class="lg-sw" style="background:var(--update-soft);border-color:var(--update-line)"></i>Update</span>
        <span><i class="lg-sw" style="background:var(--drift-soft);border-color:var(--drift-line)"></i>Drift</span>
        <span><i class="lg-sw" style="background:var(--surface-3);border-color:var(--line-strong)"></i>Untracked</span>
        <span><i class="lg-sw" style="background:repeating-linear-gradient(135deg,transparent 0 2px,var(--fg-4) 2px 3px);border-color:var(--line-strong)"></i>Not installed</span>
      </div>
    <//>
    <div class="toolbar">
      <div class="seg" role="group" aria-label="Filter rows">
        ${FILTERS.map(([k, l]) => html`<button type="button" aria-pressed=${filter === k ? "true" : "false"} onClick=${() => setFilter(k)}>${l} <span class="muted num">${q.data ? counts[k] : ""}</span></button>`)}
      </div>
      <label class="switch hide-sm"><input type="checkbox" checked=${!hideNA} onChange=${e => setHideNA(!e.currentTarget.checked)} />Show empty servers</label>
      <span class="spacer"></span>
      <div class="input-wrap"><${Icon} n="search" cls="i-sm" /><input class="input" type="search" placeholder="Filter plugins" aria-label="Filter plugins" value=${search} onInput=${e => setSearch(e.currentTarget.value)} /></div>
    </div>
    ${q.error ? html`<${ErrorState} error=${q.error} retry=${q.reload} />`
      : q.loading ? html`<div class="panel"><div class="row" style="padding:12px 16px;gap:10px">${Array.from({ length: 10 }, () => html`<${Skel} w="80px" h=${60} />`)}</div><${SkelRows} n=${10} cols=${[20, 8, 8, 8, 8, 8, 8, 8]} /></div>`
      : !q.data.plugins.length ? html`<div class="panel"><${Empty} icon="grid-3x3" title="No plugins found">No jars were found in any server's plugins folder.<//></div>`
      : !rows.length ? html`<div class="panel"><${Empty} icon="filter" title="No plugins match" action=${html`<${Btn} size="sm" onClick=${() => { setFilter("all"); setSearch(""); }}>Clear filters<//>`}>Nothing matches “${search || filter}”.<//></div>`
      : html`<div class="matrix-wrap" tabindex="0" role="region" aria-label="Plugin version matrix (scrollable)">
        <table class="matrix">
          <caption class="sr-only">Installed plugin versions by server. ${plural(rows.length, "plugin")}.</caption>
          <thead><tr>
            <th class="corner" scope="col"><span class="small muted">${plural(rows.length, "plugin")} × ${plural(cols.length, "server")}</span></th>
            ${cols.map(id => html`<th scope="col" class=${id === source ? "is-source" : ""}>
              <a class="colhead" href=${`#/servers/${encodeURIComponent(id)}`} title=${`${id}${pf[id] ? ` · ${pf[id].platform}` : ""}${id === source ? " · source" : ""}`}>
                ${id}${id === source && html`<${Icon} n="circle-dot" cls="i-xs" label="source" />`}</a></th>`)}
          </tr></thead>
          <tbody>${rows.map(p => html`<tr key=${p.key}>
            <th scope="row"><div class="mx-name">
              <span title=${p.name}>${p.name}</span>
              ${p.drift && html`<${Icon} n="git-compare-arrows" cls="i-xs" style="color:var(--drift)" label="versions differ" />`}
              <span class="mx-count">${Object.keys(p.cells).length}/${cols.length}</span>
              <span class="row-actions">
                ${p.outdated > 0 && html`<${Btn} size="sm" kind="ghost" icon="circle-arrow-up" title="Update everywhere" aria-label=${`Update ${p.name} everywhere`} onClick=${() => updateEverywhere(p)} />`}
                ${p.drift && source && p.cells[source] && html`<${Btn} size="sm" kind="ghost" icon="git-compare-arrows" title=${`Align all to ${source} version`} aria-label=${`Align ${p.name} to source version`} onClick=${() => align(p)} />`}
                <${Btn} size="sm" kind="ghost" icon="trash-2" title="Remove…" aria-label=${`Remove ${p.name}`} onClick=${() => remove(p)} />
              </span></div></th>
            ${cols.map(id => {
              const c = p.cells[id];
              if (!c) { const na = p.family && pf[id]?.family && pf[id].family !== p.family; return html`<td class=${na ? "cell-na" : "cell-empty"} title=${na ? `${id} runs a different plugin ecosystem` : undefined}><span class="sr-only">${na ? "not applicable" : "not installed"}</span></td>`; }
              const s = p.drift && c.status === "unknown" ? "drift" : c.status;
              return html`<td><a class=${"mcell st-" + s} href=${`#/servers/${encodeURIComponent(id)}`} title=${`${c.jar}\n${c.version} — ${LABEL[s] || s}`}>
                ${s === "pinned" && html`<${Icon} n="pin" cls="i-xs" />`}${shortVer(c.version)}<span class="sr-only"> (${LABEL[s] || s})</span></a></td>`;
            })}
          </tr>`)}</tbody>
        </table></div>`}`;
}
