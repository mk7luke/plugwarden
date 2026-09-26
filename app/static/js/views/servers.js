// Servers list + server detail (plugin table; per-row review, pin/ignore with explicit scope, source mapping).
import { html, useState, useMemo, useEffect, useRef } from "../lib.js";
import { useQuery, invalidate, toast, setState, useStore, getState } from "../store.js";
import { post, put } from "../api.js";
import { Icon, Btn, Tag, StatusTag, Platform, VerArrow, SkelRows, ErrorState, Empty, PageHead, Check, trapTab } from "../components/ui.js";
import { openChangeset } from "../components/changeset.js";
import { ServerKnownIssues } from "../components/health.js";
import { relTime, bytes, plural, safeUrl } from "../fmt.js";
import { navigate } from "../router.js";

export function ServersList() {
  const q = useQuery("/servers");
  return html`<${PageHead} title="Servers" sub="Every server with a plugins folder under the datastore." />
    <div class="panel">
      ${q.error ? html`<div class="panel-body"><${ErrorState} error=${q.error} retry=${q.reload} /></div>`
        : q.loading ? html`<${SkelRows} n=${8} />`
        : !q.data.length ? html`<${Empty} icon="server" title="No servers found">No instance directories with <code>Minecraft/plugins</code> were found under the datastore base. Check Settings → General.<//>`
        : html`<div class="tbl-wrap"><table class="tbl">
          <thead><tr><th scope="col">Server</th><th scope="col" class="hide-sm">Platform</th><th scope="col" class="num hide-sm">Plugins</th><th scope="col">Status</th><th scope="col" class="hide-md">Role</th><th class="col-actions"><span class="sr-only">Open</span></th></tr></thead>
          <tbody>${q.data.map(s => html`<tr key=${s.id} onClick=${() => navigate(`#/servers/${encodeURIComponent(s.id)}`)} style="cursor:pointer">
            <td><div class="cell-name"><a class="strong" data-nav href=${`#/servers/${encodeURIComponent(s.id)}`} onClick=${e => e.stopPropagation()}>${s.id}</a>
              <span class="only-sm small muted" style="margin-top:2px">${s.platform} ${s.mc_version || ""} · ${plural(s.plugin_count, "plugin")}</span></div></td>
            <td class="hide-sm"><${Platform} p=${s.platform} mc=${s.mc_version} /></td>
            <td class="num hide-sm">${s.plugin_count}</td>
            <td><div class="row wrap" style="gap:4px">
              ${s.updates ? html`<${Tag} kind="update" icon="circle-arrow-up">${plural(s.updates, "update")}<//>` : s.plugin_count ? html`<${Tag} kind="ok" icon="check">Current<//>` : html`<${Tag}>No plugins<//>`}
              ${s.drift > 0 && html`<span class="tag tag-drift tip" tabindex="0" data-tip=${driftTip(s)}><${Icon} n="git-compare-arrows" />${s.drift} drift</span>`}
              ${s.pending_restart && html`<${Tag} kind="warn" icon="rotate-ccw">Restart needed<//>`}</div></td>
            <td class="hide-md muted small">${s.is_source ? html`<${Tag} kind="plain" icon="circle-dot">Default source<//>` : s.note || (s.platform === "velocity" ? "Proxy — separate plugin ecosystem" : s.eligible_target ? "Deploy target" : "Not a target")}</td>
            <td class="col-actions"><${Icon} n="chevron-right" cls="i-sm muted" /></td>
          </tr>`)}</tbody></table></div>`}
    </div>`;
}

export const driftTip = (s) => [
  s.drift_plugins?.length ? `Differs from the network majority: ${s.drift_plugins.map(p => `${p.name} ${p.version} (network: ${p.expected})`).join(", ")}` : `${plural(s.drift, "plugin")} on a different version than the rest of the network`,
  s.drift_pinned?.length ? `Pinned on purpose: ${s.drift_pinned.map(p => `${p.name} ${p.version}`).join(", ")}` : "",
].filter(Boolean).join("\n");

const FILTERS = [["all", "All"], ["outdated", "Updates"], ["unknown", "Untracked"], ["held", "Held"]];
const held = (p) => p.status === "pinned" || p.status === "ignored";

export function ServerDetail({ id }) {
  const servers = useQuery("/servers");
  // Resolve the id against the server list first so unknown ids show "not found" without a failing request.
  const known = servers.data ? servers.data.some(s => s.id === id) : null;
  const plugins = useQuery(known === false ? null : `/servers/${encodeURIComponent(id)}/plugins`);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [sel, setSel] = useState(new Set());
  const srv = servers.data?.find(s => s.id === id);
  const mx = useQuery("/matrix");
  const cellsOf = (key) => mx.data?.plugins.find(p => p.key === key)?.cells || {};

  const rows = useMemo(() => {
    let r = plugins.data || [];
    if (filter === "outdated") r = r.filter(p => p.status === "outdated");
    else if (filter === "unknown") r = r.filter(p => p.status === "unknown");
    else if (filter === "held") r = r.filter(held);
    if (search) { const s = search.toLowerCase(); r = r.filter(p => p.name.toLowerCase().includes(s) || p.jar.toLowerCase().includes(s)); }
    const order = { outdated: 0, unknown: 1, pinned: 2, ignored: 3, current: 4 };
    return [...r].sort((a, b) => (order[a.status] - order[b.status]) || a.name.localeCompare(b.name));
  }, [plugins.data, filter, search]);
  const outdated = (plugins.data || []).filter(p => p.status === "outdated");
  const reading = (plugins.data || []).some(p => p.indexing);
  // Plugins the last start left not running (from the log-based health report; same query as the failures card).
  const health = useQuery(srv && srv.plugin_count > 0 ? `/servers/${encodeURIComponent(id)}/health` : null);
  const notRunning = new Set((health.data?.plugins || []).filter(h => h.status === "failed").map(h => h.key));
  const counts = { all: plugins.data?.length, outdated: plugins.data ? outdated.length : undefined, unknown: plugins.data?.filter(p => p.status === "unknown").length, held: plugins.data?.filter(held).length };

  const review = (list, title) => openChangeset(list.length === outdated.length ? { server: id } : { items: list.map(p => ({ key: p.key, servers: [id] })) }, title);

  async function markRestarted() {
    try { await post(`/servers/${encodeURIComponent(id)}/restarted`); toast({ kind: "ok", title: `${id} marked as restarted` }); invalidate("/servers", "/overview"); }
    catch (e) { toast({ kind: "err", title: "Couldn't update", body: e.message }); }
  }

  if (known === false || plugins.error?.status === 404) return html`<${Empty} icon="server" title=${`No server “${id}”`} action=${html`<a class="btn" href="#/servers">All servers</a>`}>It may have been removed from the datastore.<//>`;

  const selectable = rows.filter(p => p.status === "outdated");
  const allSel = selectable.length > 0 && selectable.every(p => sel.has(p.key));
  const toggle = (k, v) => setSel(s => { const n = new Set(s); v ? n.add(k) : n.delete(k); return n; });

  return html`
    <${PageHead} title=${id} sub=${srv ? html`<span class="row wrap" style="gap:10px"><${Platform} p=${srv.platform} mc=${srv.mc_version} /> · ${plural(srv.plugin_count, "plugin")}
        ${srv.drift > 0 && html` · <span class="tag tag-drift tip" tabindex="0" data-tip=${driftTip(srv)}><${Icon} n="git-compare-arrows" />${srv.drift} drift</span>`}</span>` : " "}>
      ${srv?.eligible_target && html`<a class="btn" href=${`#/deploy?targets=${encodeURIComponent(id)}`}><${Icon} n="rocket" cls="i-sm" />Deploy to ${id}</a>`}
      ${!plugins.error && html`<${Btn} kind="primary" icon="circle-arrow-up" disabled=${!outdated.length} aria-busy=${!plugins.data ? "true" : undefined} onClick=${() => review(outdated, `Review updates on ${id}`)}>
        ${!plugins.data ? "Review updates" : reading ? "Reading plugins…" : outdated.length ? `Review ${plural(outdated.length, "update")}` : "Nothing to update"}<//>`}
    <//>
    ${srv?.pending_restart && html`<div class="restart-banner" role="status"><${Icon} n="rotate-ccw" cls="i-sm" /><div class="grow"><b>Restart needed</b> — files changed since ${id} last started.</div>
      <${Btn} size="sm" icon="check" onClick=${markRestarted}>Mark restarted<//></div>`}
    ${srv && srv.plugin_count > 0 && html`<${ServerKnownIssues} server=${id} />`}
    ${srv?.platform === "velocity" && html`<div class="plan-warn" style="border:1px solid var(--warn-line);border-radius:var(--r-md);margin-bottom:var(--s-4)"><${Icon} n="shield" cls="i-sm" />Velocity proxy — it uses a different plugin ecosystem, and Bukkit/Paper plugins are never pushed here.</div>`}

    <div class="toolbar">
      <div class="seg" role="group" aria-label="Filter plugins">
        ${FILTERS.map(([k, l]) => html`<button type="button" aria-pressed=${filter === k ? "true" : "false"} onClick=${() => setFilter(k)} title=${k === "held" ? "Pinned or ignored" : undefined}>${l}${counts[k] != null ? html` <span class="muted num">${counts[k]}</span>` : ""}</button>`)}
      </div>
      <span class="spacer"></span>
      <div class="input-wrap"><${Icon} n="search" cls="i-sm" /><input class="input" type="search" placeholder="Filter by name or jar" aria-label="Filter plugins" value=${search} onInput=${e => setSearch(e.currentTarget.value)} /></div>
    </div>

    <div class="panel">
      ${plugins.error ? html`<div class="panel-body"><${ErrorState} error=${plugins.error} retry=${plugins.reload} /></div>`
        : plugins.loading ? html`<${SkelRows} n=${10} cols=${[4, 22, 26, 12, 12]} />`
        : !plugins.data.length ? html`<${Empty} icon="blocks" title="No plugins on this server">${srv?.platform === "fabric" ? "This is a Fabric modded server — mods are managed outside the plugins folder." : "The plugins folder is empty."}<//>`
        : !rows.length ? html`<${Empty} icon="filter" title="Nothing matches" action=${html`<${Btn} size="sm" onClick=${() => { setFilter("all"); setSearch(""); }}>Clear filters<//>`}>Try another filter.<//>`
        : html`<div class="tbl-wrap"><table class="tbl tbl-plugins">
          <thead><tr>
            <th class="col-check"><${Check} label="Select all updatable" checked=${allSel} indeterminate=${!allSel && selectable.some(p => sel.has(p.key))} onChange=${v => setSel(v ? new Set(selectable.map(p => p.key)) : new Set())} /></th>
            <th scope="col">Plugin</th><th scope="col" class="hide-sm">Installed</th><th scope="col" class="hide-sm">Latest compatible</th><th scope="col" class="hide-sm">Status</th>
            <th class="col-actions"><span class="sr-only">Actions</span></th></tr></thead>
          <tbody>${rows.map(p => p.indexing ? html`<tr key=${p.key} class="is-reading"><td class="col-check"></td>
            <td><div class="cell-name"><b>${p.name}</b><span class="jar">${p.jar}</span></div></td>
            <td class="hide-sm"><span class="ver muted">${p.version || "—"}</span></td><td class="hide-sm"></td>
            <td class="hide-sm"><span class="tag"><${Icon} n="loader-circle" cls="i-xs spin" />reading…</span></td><td class="col-actions"></td></tr>`
            : html`<tr key=${p.key} class=${sel.has(p.key) ? "is-selected" : ""}>
            <td class="col-check">${p.status === "outdated" ? html`<${Check} label=${`Select ${p.name}`} checked=${sel.has(p.key)} onChange=${v => toggle(p.key, v)} />` : null}</td>
            <td><div class="cell-name"><b>${p.name}</b><span class="jar" title=${`${p.jar} · ${bytes(p.size)} · modified ${relTime(p.mtime)}`}>${p.jar}</span>
              <span class="only-sm" style="margin-top:4px">${p.status === "outdated" ? html`<${VerArrow} from=${p.version} to=${p.latest.version} />` : html`<span class="row wrap" style="gap:6px"><span class="ver">${p.version || "—"}</span><${Status} p=${p} id=${id} /></span>`}</span></div></td>
            <td class="hide-sm"><span class="ver">${p.version || "—"}</span>
              ${p.current_compat?.ok === false && html`<div><${Tag} kind="warn" icon="triangle-alert" title=${`${p.jar} lists ${p.current_compat.mc_versions.join(", ")}; this server runs ${p.current_compat.mc}`}>built for MC ${p.current_compat.mc_versions.slice(-1)[0]}<//></div>`}
              ${p.drift && p.expected_version && html`<div class="small" style="color:var(--drift)">network runs ${p.expected_version}</div>`}</td>
            <td class="hide-sm">${p.latest ? html`<span class=${"ver" + (p.status === "outdated" ? " ver-new" : " muted")}>${p.latest.version}</span>
                ${safeUrl(p.latest.changelog_url) && p.status === "outdated" && html` <a class="link small" href=${safeUrl(p.latest.changelog_url)} target="_blank" rel="noopener">Changelog<span class="sr-only"> for ${p.name} (opens in new tab)</span></a>`}`
              : html`<span class="muted small">—</span>`}</td>
            <td class="hide-sm">${notRunning.has(p.key) && html`<${Tag} kind="danger" icon="circle-x" title="Failed to start or disabled itself after the last server start — see the card above">Not running<//> `}<${Status} p=${p} id=${id} />${p.source?.overrides_modrinth && html`<div><${Tag} kind="warn" icon="triangle-alert" title="A manual source_map entry is used instead of the Modrinth match for this jar">manual source overrides Modrinth (${p.source.overrides_modrinth.name || p.source.overrides_modrinth.slug})<//></div>`}</td>
            <td class="col-actions"><div class="row-actions">
              ${p.status === "outdated" && html`<${Btn} size="sm" icon="circle-arrow-up" onClick=${() => review([p], `Update ${p.name} on ${id}`)} aria-label=${`Review ${p.name} update`}><span class="hide-sm">Review</span><//>`}
              <${RowMenu} p=${p} id=${id} cells=${cellsOf(p.key)} />
            </div></td>
          </tr>`)}</tbody></table></div>`}
    </div>
    ${sel.size > 0 && html`<div class="bulkbar" role="region" aria-label="Selection actions">
      <b>${plural(sel.size, "plugin")} selected</b><span class="spacer"></span>
      <${Btn} kind="ghost" onClick=${() => setSel(new Set())}>Clear<//>
      <${Btn} kind="primary" icon="circle-arrow-up" onClick=${() => review(outdated.filter(p => sel.has(p.key)), `Review ${plural(sel.size, "update")} on ${id}`)}>Review selected<//>
    </div>`}
    <${SourceDialog} />`;
}

// Status chip; says where a pin/ignore applies. Untracked rows get one "No source · Map…" action.
function Status({ p, id }) {
  if (p.valid === false || p.descriptor_error) return html`<${Tag} kind="warn" icon="file-code" title=${p.descriptor_error || "The jar's plugin.yml could not be read"}>unreadable plugin.yml<//>`;
  const where = (scope) => scope === "*" ? "network-wide" : Array.isArray(scope) && scope.length > 1 ? `on ${plural(scope.length, "server")}` : "this server";
  if (p.status === "pinned") return html`<${Tag} kind="plain" icon="pin" title=${`Pinned ${where(p.pin_scope)}`}>Pinned ${p.pinned_version || p.version} · ${where(p.pin_scope)}<//>`;
  if (p.status === "ignored") return html`<${Tag} kind="plain" icon="eye-off">Ignored · ${where(p.ignore_scope)}<//>`;
  if (p.status === "unknown") return html`<button type="button" class="tag tag-btn" onClick=${() => setState({ mapSource: p })} aria-label=${`No update source for ${p.name}. Map one`}><${Icon} n="circle-dashed" />No source · Map</button>`;
  return html`<${StatusTag} status=${p.status} />`;
}

async function hold(p, kind, on, servers, label) {
  const k = encodeURIComponent(p.key);
  try {
    if (kind === "pin") await post(`/plugins/${k}/pin`, { version: on ? p.version : null, servers });
    else await post(`/plugins/${k}/ignore`, { ignored: on, servers });
    toast({ kind: "ok", title: label });
    invalidate("/settings", "/servers", "/matrix", "/updates", "/overview");
  } catch (e) { toast({ kind: "err", title: "Couldn't save", body: e.message }); }
}

function RowMenu({ p, id, cells }) {
  const on = Object.keys(cells);
  const others = Object.entries(cells).filter(([s, c]) => s !== id && c.version !== p.version);
  const netNote = others.length ? `${others.map(([s, c]) => `${s} is on ${c.version}`).join(", ")} — pinning blocks their updates but doesn't downgrade them` : "";
  const [open, setOpen] = useState(false);
  const ref = useRef();
  useEffect(() => {
    if (!open) return;
    const first = ref.current?.querySelector('[role="menuitem"]'); first?.focus();
    const out = (e) => !ref.current?.contains(e.target) && setOpen(false);
    const key = (e) => {
      const items = [...(ref.current?.querySelectorAll('[role="menuitem"]') || [])];
      const i = items.indexOf(document.activeElement);
      if (e.key === "Escape") { setOpen(false); ref.current?.querySelector("button")?.focus(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length]?.focus(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length]?.focus(); }
    };
    document.addEventListener("pointerdown", out); document.addEventListener("keydown", key);
    return () => { document.removeEventListener("pointerdown", out); document.removeEventListener("keydown", key); };
  }, [open]);
  const pinnedHere = p.status === "pinned";
  const ignoredHere = p.status === "ignored";
  const net = (scope) => scope === "*";
  const items = [
    pinnedHere
      ? [`Unpin on ${id}`, "pin", () => hold(p, "pin", false, [id], `Unpinned ${p.name} on ${id}`)]
      : [`Pin at ${p.version} on ${id}`, "pin", () => hold(p, "pin", true, [id], `Pinned ${p.name} at ${p.version} on ${id}`)],
    pinnedHere && net(p.pin_scope)
      ? [`Unpin on all servers`, "pin", () => hold(p, "pin", false, "*", `Unpinned ${p.name} network-wide`)]
      : !pinnedHere && [`Pin at ${p.version} on all ${on.length} servers with ${p.name}`, "pin", () => hold(p, "pin", true, "*", `Pinned ${p.name} at ${p.version} on ${on.length} servers`), netNote],
    ignoredHere
      ? [`Track updates again on ${id}`, "eye", () => hold(p, "ignore", false, [id], `Tracking ${p.name} on ${id} again`)]
      : [`Ignore updates on ${id}`, "eye-off", () => hold(p, "ignore", true, [id], `Ignoring updates for ${p.name} on ${id}`)],
    ignoredHere && net(p.ignore_scope)
      ? [`Track updates again everywhere`, "eye", () => hold(p, "ignore", false, "*", `Tracking ${p.name} everywhere again`)]
      : !ignoredHere && [`Ignore updates network-wide`, "eye-off", () => hold(p, "ignore", true, "*", `Ignoring updates for ${p.name} network-wide`)],
    [p.source ? "Change update source…" : "Map update source…", "sliders-horizontal", () => setState({ mapSource: p })],
  ].filter(Boolean);
  return html`<div class="menu-wrap" ref=${ref}>
    <${Btn} size="sm" kind="ghost" icon="ellipsis" aria-haspopup="menu" aria-expanded=${open ? "true" : "false"} aria-label=${`More actions for ${p.name}`} onClick=${() => setOpen(!open)} />
    ${open && html`<div class="menu" role="menu" aria-label=${`${p.name} actions`}>
      ${items.map(([label, icon, run, note]) => html`<button type="button" role="menuitem" class="menu-item" onClick=${() => { setOpen(false); run(); }}><${Icon} n=${icon} cls="i-sm" />
        <span class="menu-label">${label}${note && html`<small>${note}</small>`}</span></button>`)}
    </div>`}
  </div>`;
}

const KINDS = [["modrinth", "Modrinth", "project slug, e.g. coreprotect"], ["hangar", "Hangar", "owner/slug"], ["spiget", "Spiget", "numeric resource id"], ["github", "GitHub", "owner/repo"]];

// Inline source mapping for one plugin (writes settings.source_map[key]).
function SourceDialog() {
  const p = useStore(s => s.mapSource);
  const st = useQuery(p ? "/settings" : null);
  const [kind, setKind] = useState("modrinth");
  const [id, setId] = useState("");
  const [autoApply, setAutoApply] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef();
  useEffect(() => {
    if (!p) return;
    const cur = getState().mapSource && st.data?.source_map?.[p.key];
    setKind(cur?.kind || "modrinth");
    setId(cur?.id || p.name.toLowerCase().replace(/[^a-z0-9]+/g, "-"));
    setAutoApply(!!cur?.auto_apply);
    const prev = document.activeElement;
    setTimeout(() => ref.current?.querySelector("select")?.focus(), 0);
    const k = (e) => { if (e.key === "Escape") setState({ mapSource: null }); trapTab(e, ref.current); };
    document.addEventListener("keydown", k);
    return () => { document.removeEventListener("keydown", k); prev?.focus?.(); };
  }, [p?.key, st.data]);
  if (!p) return null;
  const close = () => setState({ mapSource: null });
  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      await put("/settings", { source_map: { ...(st.data?.source_map || {}), [p.key]: { kind, id: id.trim(), ...(autoApply ? { auto_apply: true } : {}) } } });
      toast({ kind: "ok", title: `${p.name} mapped to ${KINDS.find(k => k[0] === kind)[1]}`, body: "Run an update check to fetch its latest version." });
      invalidate("/settings", "/servers", "/matrix", "/updates");
      close();
    } catch (err) { toast({ kind: "err", title: "Couldn't save mapping", body: err.message }); }
    setBusy(false);
  };
  return html`<div class="scrim" onClick=${close}></div>
  <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="ms-t" ref=${ref}>
    <form onSubmit=${save}>
      <div class="dialog-body">
        <h2 id="ms-t" style="gap:8px"><${Icon} n="sliders-horizontal" cls="i-sm" style="color:var(--fg-2)" />Update source for ${p.name}</h2>
        <p>Applies to ${p.name} on every server. Modrinth matches most jars automatically by file hash; map the rest here.</p>
        <div class="row wrap" style="gap:10px;align-items:flex-end">
          <div class="field" style="width:140px"><label for="ms-k">Source</label>
            <select id="ms-k" class="select" value=${kind} onChange=${e => setKind(e.currentTarget.value)}>${KINDS.map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select></div>
          <div class="field grow"><label for="ms-i">Id</label>
            <input id="ms-i" class="input mono" required value=${id} placeholder=${KINDS.find(k => k[0] === kind)[2]} onInput=${e => setId(e.currentTarget.value)} /></div>
        </div>
        <p class="small muted">${KINDS.find(k => k[0] === kind)[2]}</p>
        <label class="check"><input type="checkbox" checked=${autoApply} onChange=${e => setAutoApply(e.currentTarget.checked)} />Allow automatic updates from this source</label>
        ${p.source?.kind === "modrinth" && kind !== "modrinth" && html`<p class="small" style="color:var(--warn)">This replaces the Modrinth match (${p.source.name || p.source.slug || p.source.id}).</p>`}
      </div>
      <div class="dialog-foot"><button type="button" class="btn" onClick=${close}>Cancel</button><${Btn} type="submit" kind="primary" busy=${busy}>Save mapping<//></div>
    </form>
  </div>`;
}
