// Plugins matrix — rows = plugins, columns = servers, cells = installed version.
// Cells are selectable (click, shift-click for a range in a row); a floating bar acts on the selection.
// A plugin's name opens a drawer with its per-server versions and actions.
import { html, useState, useMemo, useEffect, useRef } from "../lib.js";
import { useQuery, setState, useStore, getState, toast } from "../store.js";
import { openRemove } from "../components/removedialog.js";
import { openChangeset } from "../components/changeset.js";
import { Icon, Btn, Tag, StatusTag, SkelRows, ErrorState, Empty, PageHead, Skel, trapTab, restoreFocus } from "../components/ui.js";
import { navigate } from "../router.js";
import { plural, compactVer, midTrunc } from "../fmt.js";
import { ineligible } from "./deploy.js";

const FILTERS = [["all", "All"], ["outdated", "Updates"], ["drift", "Drift"], ["unknown", "Untracked"]];
const LABEL = { current: "up to date", outdated: "update available", drift: "differs from other servers", unknown: "source unknown", pinned: "pinned", ignored: "ignored" };
// Matrix cells hold 11 characters (13 in a smaller face); compact first, then elide the middle (the tooltip has the full string).
const shortVer = (v) => midTrunc(compactVer(v), 13) || "?";
const longVer = (v) => (compactVer(v) || "").length > 11;
const cid = (key, server) => `${key}\u0000${server}`;

export function Matrix({ query }) {
  const q = useQuery("/matrix");
  const srv = useQuery("/servers");
  const st = useQuery("/settings");
  const [search, setSearch] = useState(query.q || "");
  const [filter, setFilter] = useState(query.status || "all");
  const [hideNA, setHideNA] = useState(true);
  const [sel, setSel] = useState(new Set());
  const anchor = useRef(null);
  useEffect(() => { if (query.q != null) setSearch(query.q); if (query.status) setFilter(query.status); }, [query.q, query.status]);

  const source = st.data?.default_source;
  const pf = Object.fromEntries((q.data?.server_info || srv.data || []).map(s => [s.id, s]));
  const cols = useMemo(() => (q.data?.servers || []).filter(id => !hideNA || (pf[id]?.plugin_count ?? 1) > 0), [q.data, hideNA, srv.data]);

  const all = useMemo(() => (q.data?.plugins || []).map(p => {
    const cells = Object.values(p.cells);
    return { ...p, reading: p.indexing || cells.every(c => c.indexing), drift: p.drift ?? new Set(cells.map(c => c.version)).size > 1, outdated: cells.filter(c => c.status === "outdated").length, unknown: cells.every(c => c.status === "unknown") };
  }), [q.data]);
  const rows = useMemo(() => {
    let r = all;
    if (filter === "outdated") r = r.filter(p => p.outdated);
    if (filter === "drift") r = r.filter(p => p.drift);
    if (filter === "unknown") r = r.filter(p => p.unknown);
    if (search) { const s = search.toLowerCase(); r = r.filter(p => p.name.toLowerCase().includes(s) || p.key.includes(s)); }
    return r;
  }, [all, filter, search]);
  const counts = { all: all.length, outdated: all.filter(p => p.outdated).length, drift: all.filter(p => p.drift).length, unknown: all.filter(p => p.unknown).length };

  const clickCell = (p, id, e) => {
    const k = cid(p.key, id);
    setSel(s => {
      const n = new Set(s);
      if (e.shiftKey && anchor.current?.key === p.key) {
        const a = cols.indexOf(anchor.current.server), b = cols.indexOf(id);
        for (const c of cols.slice(Math.min(a, b), Math.max(a, b) + 1)) if (p.cells[c]) n.add(cid(p.key, c));
      } else n.has(k) ? n.delete(k) : n.add(k);
      return n;
    });
    anchor.current = { key: p.key, server: id };
  };
  const byKey = Object.fromEntries(all.map(p => [p.key, p]));
  // Arrow keys move between installed cells; Space toggles (native button); "u" reviews the selection.
  const gridKeys = (e) => {
    const b = e.target.closest?.(".mcell"); if (!b) return;
    const r = +b.dataset.r, c = +b.dataset.c;
    const d = { ArrowRight: [0, 1], ArrowLeft: [0, -1], ArrowDown: [1, 0], ArrowUp: [-1, 0] }[e.key];
    if (d) {
      e.preventDefault();
      const cells = [...e.currentTarget.querySelectorAll(".mcell")];
      const cand = cells.filter(x => d[0] ? Math.sign(+x.dataset.r - r) === d[0] : (+x.dataset.r === r && Math.sign(+x.dataset.c - c) === d[1]));
      const dist = (x) => Math.abs(+x.dataset.r - r) * 100 + Math.abs(+x.dataset.c - c);
      cand.sort((a, b) => dist(a) - dist(b))[0]?.focus();
    } else if (e.key === "u" && sel.size) {
      e.preventDefault();
      const btn = document.querySelector(".mx-bar .btn-primary");
      if (btn && !btn.disabled) btn.click();
      else toast({ kind: "info", title: `None of the ${plural(sel.size, "selected cell")} has an update` });
    }
  };
  const picked = [...sel].map(x => { const [key, server] = x.split("\u0000"); return { key, server, p: byKey[key], c: byKey[key]?.cells[server] }; }).filter(x => x.c);

  return html`
    <${PageHead} title="Plugins matrix" sub="Every plugin on every server. Click cells to select them; click a plugin for its details.">
      <div class="legend" aria-label="Legend">
        <span><i class="lg-sw" style="background:var(--ok-soft);border-color:var(--ok-line)"></i>Current</span>
        <span><i class="lg-sw" style="background:var(--update-soft);border-color:var(--update-line)"></i>Update</span>
        <span><i class="lg-sw" style="background:var(--drift-soft);border-color:var(--drift-line)"></i>Drift</span>
        <span><i class="lg-sw" style="background:var(--surface-3);border-color:var(--line-strong)"></i>Untracked</span>
        <span><i class="lg-sw" style="background:repeating-linear-gradient(135deg,transparent 0 2px,var(--fg-3) 2px 3px);border-color:var(--line-strong)"></i>Not installed</span>
        <span><i class="lg-sw" style="background:var(--bg-sunken);border-color:var(--line)"></i>N/A · other platform</span>
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
          <caption class="sr-only">Installed plugin versions by server. ${plural(rows.length, "plugin")}. Cells are toggle buttons that select that plugin on that server.</caption>
          <thead><tr>
            <th class="corner" scope="col"><span class="small muted">${plural(rows.length, "plugin")} × ${plural(cols.length, "server")}</span></th>
            ${cols.map((id, i) => html`<th scope="col" class=${id === source ? "is-source" : ""} style=${`z-index:${4 + cols.length - i}`}>
              <a class="colhead" href=${`#/servers/${encodeURIComponent(id)}`} title=${`${id}${pf[id] ? ` · ${pf[id].platform}` : ""}${id === source ? " · source" : ""}`}>
                ${id === source && html`<${Icon} n="circle-dot" cls="i-xs" label="source" />`}${id}</a></th>`)}
            <th class="mx-pad" aria-hidden="true"></th>
          </tr></thead>
          <tbody onKeyDown=${gridKeys}>${rows.map((p, ri) => html`<tr key=${p.key} class=${p.reading ? "is-reading" : ""}>
            <th scope="row"><div class="mx-name">
              <button type="button" class="mx-open" disabled=${p.reading} onClick=${() => setState({ pluginDrawer: p.key })} aria-label=${`${p.name} details`}>
                <span>${p.name}</span>${p.drift && html`<${Icon} n="git-compare-arrows" cls="i-xs" style="color:var(--drift)" label="versions differ" />`}</button>
              <span class="mx-count" aria-label=${`on ${Object.keys(p.cells).length} of ${cols.length} servers`}>${Object.keys(p.cells).length}/${cols.length}</span>
            </div></th>
            ${cols.map((id, ci) => {
              const c = p.cells[id];
              if (!c) { const na = p.family && pf[id]?.family && pf[id].family !== p.family; return html`<td class=${na ? "cell-na" : "cell-empty"} title=${na ? `${id} runs a different plugin ecosystem` : undefined}><span class="sr-only">${na ? "not applicable" : "not installed"}</span></td>`; }
              // The backend marks the odd cell out; pinned installs never count as drift.
              const s = c.drift ? "drift" : c.status;
              const on = sel.has(cid(p.key, id));
              return html`<td><button type="button" class=${"mcell tip st-" + s + (on ? " is-sel" : "") + (longVer(c.version) ? " is-long" : "")} aria-pressed=${on ? "true" : "false"} data-r=${ri} data-c=${ci}
                data-tip=${`${id}\n${c.jar}\n${c.version} · ${LABEL[s] || s}${p.latest_version && s === "outdated" ? ` (latest ${p.latest_version})` : ""}${c.drift_pinned ? `\nPinned: differs from the network (${p.expected_version}) on purpose` : ""}${p.expected_version && c.drift ? `\nNetwork majority: ${p.expected_version}` : ""}`}
                aria-label=${`${p.name} on ${id}: ${c.version}, ${c.indexing ? "still being read" : LABEL[s] || s}`} disabled=${c.indexing} onClick=${e => clickCell(p, id, e)}>
                <span class="mv">${(s === "pinned" || c.drift_pinned) && html`<${Icon} n="pin" cls="i-xs" />`}${shortVer(c.version)}</span></button></td>`;
            })}
            <td class="mx-pad" aria-hidden="true"></td>
          </tr>`)}</tbody>
        </table></div>`}
    ${picked.length > 0 && html`<${SelectionBar} picked=${picked} source=${source} pf=${pf} clear=${() => setSel(new Set())} />`}
    <${PluginDrawer} all=${all} source=${source} pf=${pf} />`;
}

function SelectionBar({ picked, source, pf, clear }) {
  const keys = [...new Set(picked.map(x => x.key))];
  const servers = [...new Set(picked.map(x => x.server))];
  const outdated = picked.filter(x => x.c.status === "outdated");
  const one = keys.length === 1 ? picked[0].p : null;
  const src = one && one.cells[source];
  const alignTargets = one && src ? servers.filter(s => s !== source && one.cells[s]?.version !== src.version && pf[s] && !ineligible(pf[s], pf[source])) : [];
  const review = () => {
    const items = keys.map(key => ({ key, servers: outdated.filter(x => x.key === key).map(x => x.server) })).filter(i => i.servers.length);
    openChangeset({ items }, `Review ${plural(outdated.length, "selected update")}`);
  };
  return html`<div class="bulkbar mx-bar" role="region" aria-label="Selection actions">
    <b>${plural(picked.length, "cell")}</b><span class="muted small">${one ? one.name : plural(keys.length, "plugin")} · ${plural(servers.length, "server")}</span>
    <span class="spacer"></span>
    <${Btn} kind="ghost" onClick=${clear}>Clear<//>
    <div class="mx-bar-actions">
    ${one && html`<${Btn} icon="trash-2" onClick=${() => openRemove({ ...one, cells: Object.fromEntries(servers.map(s => [s, one.cells[s]])) })} aria-label=${`Remove ${one.name} from ${plural(servers.length, "server")}`}>Remove<span class="hide-sm"> from ${plural(servers.length, "server")}</span><//>`}
    ${alignTargets.length > 0 && html`<${Btn} icon="git-compare-arrows" onClick=${() => navigate(`#/deploy?action=replace&jar=${encodeURIComponent(src.jar)}&targets=${alignTargets.map(encodeURIComponent).join(",")}`)} aria-label=${`Align to ${source} ${src.version}`}>Align<span class="hide-sm"> to ${source} (${shortVer(src.version)})</span><//>`}
    <${Btn} kind="primary" icon="circle-arrow-up" disabled=${!outdated.length} onClick=${review} title=${outdated.length ? "" : "None of the selected cells has an update"}>Review ${plural(outdated.length, "update")}<//>
    </div>
  </div>`;
}

function PluginDrawer({ all, source, pf }) {
  const key = useStore(s => s.pluginDrawer);
  const ref = useRef();
  const close = () => setState({ pluginDrawer: null });
  useEffect(() => {
    if (!key) return;
    const prev = document.activeElement;
    ref.current?.querySelector("[data-autofocus]")?.focus();
    const k = (e) => { if (e.key === "Escape" && !getState().confirm) close(); if (!getState().confirm) trapTab(e, ref.current); };
    document.addEventListener("keydown", k);
    return () => { document.removeEventListener("keydown", k); restoreFocus(prev); };
  }, [key]);
  const p = key && all.find(x => x.key === key);
  if (!p) return null;
  const cells = Object.entries(p.cells);
  const src = p.cells[source];
  const alignTargets = src ? cells.filter(([s, c]) => s !== source && c.version !== src.version && pf[s] && !ineligible(pf[s], pf[source])).map(([s]) => s) : [];
  const go = (fn) => { close(); fn(); };
  return html`<div class="scrim" onClick=${close}></div>
  <aside class="sheet sheet-narrow" role="dialog" aria-modal="true" aria-labelledby="pd-t" ref=${ref}>
    <header class="sheet-head"><div class="grow"><h2 id="pd-t">${p.name}</h2>
      <p class="small muted mono">${p.key}${p.latest_version ? ` · latest ${p.latest_version}` : ""}${p.source ? ` · via ${p.source.kind}` : " · no update source"}</p></div>
      <${Btn} kind="ghost" icon="x" aria-label="Close" onClick=${close} data-autofocus /></header>
    <div class="sheet-body">
      <div class="stack" style="padding:16px 20px;gap:8px">
        ${p.outdated > 0 && html`<${Btn} kind="primary" icon="circle-arrow-up" onClick=${() => go(() => openChangeset({ keys: [p.key] }, `Update ${p.name} everywhere`))}>Review update on ${plural(p.outdated, "server")}<//>`}
        ${alignTargets.length > 0 && html`<${Btn} icon="git-compare-arrows" onClick=${() => go(() => navigate(`#/deploy?action=replace&jar=${encodeURIComponent(src.jar)}&targets=${alignTargets.map(encodeURIComponent).join(",")}`))}>Align ${plural(alignTargets.length, "server")} to ${source} (${src.version})<//>`}
        <${Btn} icon="trash-2" onClick=${() => go(() => openRemove(p))}>Remove from servers…<//>
        ${!p.source && html`<a class="btn" href="#/settings/sources" onClick=${close}><${Icon} n="sliders-horizontal" cls="i-sm" />Map an update source</a>`}
      </div>
      <table class="tbl"><caption class="sr-only">${p.name} by server</caption>
        <thead><tr><th scope="col">Server</th><th scope="col">Version</th><th scope="col">Status</th></tr></thead>
        <tbody>${cells.map(([s, c]) => html`<tr><td><a class="strong" href=${`#/servers/${encodeURIComponent(s)}`} onClick=${close}>${s}</a>${s === source ? html` <${Tag}>source<//>` : ""}</td>
          <td><div class="cell-name"><span class="ver">${c.version}</span><span class="jar">${c.jar}</span></div></td>
          <td><${StatusTag} status=${c.drift ? "drift" : c.status} /></td></tr>`)}</tbody></table>
    </div>
  </aside>`;
}
