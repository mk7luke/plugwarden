// Servers list + server detail (plugin table with per-row update / pin / ignore).
import { html, useState, useMemo } from "../lib.js";
import { useQuery, invalidate, toast } from "../store.js";
import { post } from "../api.js";
import { Icon, Btn, Tag, StatusTag, Platform, VerArrow, SkelRows, ErrorState, Empty, PageHead, Check } from "../components/ui.js";
import { applyUpdates } from "../actions.js";
import { relTime, bytes, plural } from "../fmt.js";
import { navigate } from "../router.js";

export function ServersList() {
  const q = useQuery("/servers");
  const st = useQuery("/settings");
  return html`<${PageHead} title="Servers" sub="Every AMP instance with a plugins folder under the datastore." />
    <div class="panel">
      ${q.error ? html`<div class="panel-body"><${ErrorState} error=${q.error} retry=${q.reload} /></div>`
        : q.loading ? html`<${SkelRows} n=${8} />`
        : !q.data.length ? html`<${Empty} icon="server" title="No servers found">No instance directories with <code>Minecraft/plugins</code> were found under the datastore base. Check Settings → General.<//>`
        : html`<div class="tbl-wrap"><table class="tbl">
          <thead><tr><th scope="col">Server</th><th scope="col" class="hide-sm">Platform</th><th scope="col" class="num hide-sm">Plugins</th><th scope="col">Status</th><th scope="col" class="hide-md">Role</th><th class="col-actions"><span class="sr-only">Open</span></th></tr></thead>
          <tbody>${q.data.map(s => html`<tr key=${s.id} onClick=${() => navigate(`#/servers/${encodeURIComponent(s.id)}`)} style="cursor:pointer">
            <td><div class="cell-name"><a class="strong" href=${`#/servers/${encodeURIComponent(s.id)}`} onClick=${e => e.stopPropagation()}>${s.id}</a>
              <span class="only-sm small muted" style="margin-top:2px">${s.platform} ${s.mc_version || ""} · ${plural(s.plugin_count, "plugin")}</span></div></td>
            <td class="hide-sm"><${Platform} p=${s.platform} mc=${s.mc_version} /></td>
            <td class="num hide-sm">${s.plugin_count}</td>
            <td><div class="row wrap" style="gap:4px">
              ${s.updates ? html`<${Tag} kind="update" icon="circle-arrow-up">${s.updates} updates<//>` : s.plugin_count ? html`<${Tag} kind="ok" icon="check">Current<//>` : html`<${Tag}>Empty<//>`}
              ${s.drift > 0 && html`<${Tag} kind="drift" icon="git-compare-arrows">${s.drift} drift<//>`}
              ${s.pending_restart && html`<${Tag} kind="warn" icon="rotate-ccw">Restart pending<//>`}</div></td>
            <td class="hide-md muted small">${s.is_source || st.data?.default_source === s.id ? html`<${Tag} kind="accent" icon="circle-dot">Default source<//>` : s.note || (s.platform === "velocity" ? "Proxy — separate plugin ecosystem" : s.eligible_target ? "Deploy target" : "Not a target")}</td>
            <td class="col-actions"><${Icon} n="chevron-right" cls="i-sm muted" /></td>
          </tr>`)}</tbody></table></div>`}
    </div>`;
}

const FILTERS = [["all", "All"], ["outdated", "Updates"], ["unknown", "Untracked"], ["held", "Pinned & ignored"]];

export function ServerDetail({ id }) {
  const servers = useQuery("/servers");
  // Resolve the id against the server list first so unknown ids show "not found" without a failing request.
  const known = servers.data ? servers.data.some(s => s.id === id) : null;
  const plugins = useQuery(known === false ? null : `/servers/${encodeURIComponent(id)}/plugins`);
  const settings = useQuery("/settings");
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [sel, setSel] = useState(new Set());
  const [busyKey, setBusyKey] = useState(null);
  const srv = servers.data?.find(s => s.id === id);

  const rows = useMemo(() => {
    let r = plugins.data || [];
    if (filter === "outdated") r = r.filter(p => p.status === "outdated");
    else if (filter === "unknown") r = r.filter(p => p.status === "unknown");
    else if (filter === "held") r = r.filter(p => p.status === "pinned" || p.status === "ignored");
    if (search) { const s = search.toLowerCase(); r = r.filter(p => p.name.toLowerCase().includes(s) || p.jar.toLowerCase().includes(s)); }
    const order = { outdated: 0, unknown: 1, pinned: 2, ignored: 3, current: 4 };
    return [...r].sort((a, b) => (order[a.status] - order[b.status]) || a.name.localeCompare(b.name));
  }, [plugins.data, filter, search]);
  const outdated = (plugins.data || []).filter(p => p.status === "outdated");
  const counts = { all: plugins.data?.length, outdated: outdated.length, unknown: plugins.data?.filter(p => p.status === "unknown").length, held: plugins.data?.filter(p => p.status === "pinned" || p.status === "ignored").length };

  const update = (list, title) => applyUpdates(list.map(p => ({ key: p.key, servers: [id] })), { title }).then(() => setSel(new Set()));

  async function hold(p, kind) {
    setBusyKey(p.key + kind);
    const k = encodeURIComponent(p.key);
    let msg;
    try {
      if (kind === "pin") {
        const on = p.status === "pinned";
        await post(`/plugins/${k}/pin`, { version: on ? null : p.version });
        msg = on ? `Unpinned ${p.name}` : `Pinned ${p.name} at ${p.version}`;
      } else {
        const on = p.status === "ignored";
        await post(`/plugins/${k}/ignore`, { ignored: !on });
        msg = on ? `${p.name} is tracked again` : `Ignoring updates for ${p.name}`;
      }
      toast({ kind: "ok", title: msg });
      invalidate("/settings", "/servers", "/matrix", "/updates", "/overview");
    } catch (e) { toast({ kind: "err", title: "Couldn't save", body: e.message }); }
    setBusyKey(null);
  }
  async function markRestarted() {
    try { await post(`/servers/${encodeURIComponent(id)}/restarted`); toast({ kind: "ok", title: `${id} marked as restarted` }); invalidate("/servers", "/overview"); }
    catch (e) { toast({ kind: "err", title: "Couldn't update", body: e.message }); }
  }

  if (known === false || plugins.error?.status === 404) return html`<${Empty} icon="server" title=${`No server “${id}”`} action=${html`<a class="btn" href="#/servers">All servers</a>`}>It may have been removed from the datastore.<//>`;

  const selectable = rows.filter(p => p.status === "outdated");
  const allSel = selectable.length > 0 && selectable.every(p => sel.has(p.key));
  const toggle = (k, v) => setSel(s => { const n = new Set(s); v ? n.add(k) : n.delete(k); return n; });

  return html`
    <${PageHead} title=${id} sub=${srv ? html`<span class="row wrap" style="gap:10px"><${Platform} p=${srv.platform} mc=${srv.mc_version} /> · ${plural(srv.plugin_count, "plugin")}${srv.pending_restart ? html` · <${Tag} kind="warn" icon="rotate-ccw">Restart pending<//>` : ""}</span>` : " "}>
      ${srv?.pending_restart && html`<${Btn} icon="rotate-ccw" onClick=${markRestarted} title="Files changed since the last restart. Click once the server has been restarted.">Mark restarted<//>`}
      <a class="btn" href=${`#/deploy?targets=${encodeURIComponent(id)}`}><${Icon} n="rocket" cls="i-sm" />Deploy to this server</a>
      <${Btn} kind="primary" icon="circle-arrow-up" disabled=${!outdated.length} onClick=${() => update(outdated, `Update ${id}`)}>
        ${outdated.length ? `Update all ${outdated.length}` : "Nothing to update"}<//>
    <//>
    ${srv?.platform === "velocity" && html`<div class="plan-warn" style="border:1px solid var(--warn-line);border-radius:var(--r-md);margin-bottom:var(--s-4)"><${Icon} n="shield" cls="i-sm" />Velocity proxy — it uses a different plugin ecosystem, and Bukkit/Paper plugins are never pushed here.</div>`}

    <div class="toolbar">
      <div class="seg" role="group" aria-label="Filter plugins">
        ${FILTERS.map(([k, l]) => html`<button type="button" aria-pressed=${filter === k ? "true" : "false"} onClick=${() => setFilter(k)}>${l}${counts[k] != null ? html` <span class="muted num">${counts[k]}</span>` : ""}</button>`)}
      </div>
      <span class="spacer"></span>
      <div class="input-wrap"><${Icon} n="search" cls="i-sm" /><input class="input" type="search" placeholder="Filter by name or jar" aria-label="Filter plugins" value=${search} onInput=${e => setSearch(e.currentTarget.value)} /></div>
    </div>

    <div class="panel">
      ${plugins.error ? html`<div class="panel-body"><${ErrorState} error=${plugins.error} retry=${plugins.reload} /></div>`
        : plugins.loading ? html`<${SkelRows} n=${10} cols=${[4, 22, 26, 12, 12]} />`
        : !plugins.data.length ? html`<${Empty} icon="blocks" title="No plugins on this server">${srv?.platform === "fabric" ? "This is a Fabric modded server — mods are managed outside the plugins folder." : "The plugins folder is empty."}<//>`
        : !rows.length ? html`<${Empty} icon="filter" title="Nothing matches" action=${html`<${Btn} size="sm" onClick=${() => { setFilter("all"); setSearch(""); }}>Clear filters<//>`}>Try another filter.<//>`
        : html`<div class="tbl-wrap"><table class="tbl">
          <thead><tr>
            <th class="col-check"><${Check} label="Select all updatable" checked=${allSel} indeterminate=${!allSel && selectable.some(p => sel.has(p.key))} onChange=${v => setSel(v ? new Set(selectable.map(p => p.key)) : new Set())} /></th>
            <th scope="col">Plugin</th><th scope="col" class="hide-sm">Installed</th><th scope="col" class="hide-sm">Latest compatible</th><th scope="col" class="hide-md">Source</th><th scope="col" class="hide-sm">Status</th>
            <th class="col-actions"><span class="sr-only">Actions</span></th></tr></thead>
          <tbody>${rows.map(p => html`<tr key=${p.key} class=${sel.has(p.key) ? "is-selected" : ""}>
            <td class="col-check">${p.status === "outdated" ? html`<${Check} label=${`Select ${p.name}`} checked=${sel.has(p.key)} onChange=${v => toggle(p.key, v)} />` : null}</td>
            <td><div class="cell-name"><b>${p.name}</b><span class="jar" title=${`${p.jar} · ${bytes(p.size)} · modified ${relTime(p.mtime)}`}>${p.jar}</span>
              <span class="only-sm" style="margin-top:4px;flex-wrap:wrap">${p.status === "outdated" ? html`<${VerArrow} from=${p.version} to=${p.latest.version} />` : html`<span class="row wrap" style="gap:6px"><span class="ver">${p.version || "—"}</span><${StatusTag} status=${p.status} /></span>`}</span></div></td>
            <td class="hide-sm"><span class="ver">${p.version || "—"}</span></td>
            <td class="hide-sm">${p.latest ? (p.status === "outdated" ? html`<span class="ver" style="color:var(--update);font-weight:600">${p.latest.version}</span>` : html`<span class="ver muted">${p.latest.version}</span>`) : html`<span class="muted small">—</span>`}
              ${p.latest?.changelog_url && p.status === "outdated" && html` <a class="link small" href=${p.latest.changelog_url} target="_blank" rel="noopener" aria-label=${`${p.name} changelog`}><${Icon} n="external-link" cls="i-xs" /></a>`}</td>
            <td class="hide-md">${p.source ? html`<span class="small muted">${p.source.kind}</span>` : html`<a class="link small" href="#/settings/sources">Map source</a>`}</td>
            <td class="hide-sm"><${StatusTag} status=${p.status} /></td>
            <td class="col-actions"><div class="row-actions">
              ${p.status === "outdated" && html`<${Btn} size="sm" icon="circle-arrow-up" onClick=${() => update([p], `Update ${p.name} on ${id}`)} aria-label=${`Update ${p.name}`}><span class="hide-sm">Update</span><//>`}
              <${Btn} size="sm" kind="ghost" icon="pin" busy=${busyKey === p.key + "pin"} aria-pressed=${p.status === "pinned" ? "true" : "false"} title=${p.status === "pinned" ? `Pinned at ${p.pinned_version || p.version} — click to unpin` : "Pin at this version"} aria-label=${`${p.status === "pinned" ? "Unpin" : "Pin"} ${p.name}`} onClick=${() => hold(p, "pin")} />
              <${Btn} size="sm" kind="ghost" icon=${p.status === "ignored" ? "eye" : "eye-off"} busy=${busyKey === p.key + "ignore"} title=${p.status === "ignored" ? "Track updates again" : "Ignore updates"} aria-label=${`${p.status === "ignored" ? "Track" : "Ignore"} ${p.name}`} onClick=${() => hold(p, "ignore")} />
            </div></td>
          </tr>`)}</tbody></table></div>`}
    </div>
    ${sel.size > 0 && html`<div class="bulkbar" role="region" aria-label="Selection actions">
      <b>${plural(sel.size, "plugin")} selected</b><span class="spacer"></span>
      <${Btn} kind="ghost" onClick=${() => setSel(new Set())}>Clear<//>
      <${Btn} kind="primary" icon="circle-arrow-up" onClick=${() => update(outdated.filter(p => sel.has(p.key)), `Update ${sel.size} on ${id}`)}>Update selected<//>
    </div>`}`;
}
