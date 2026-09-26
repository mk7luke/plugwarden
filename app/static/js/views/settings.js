// Settings — datastore, default source, server groups, update source mapping, auto-update policy.
import { html, useState, useEffect, useMemo } from "../lib.js";
import { useQuery, invalidate, toast, confirmDialog } from "../store.js";
import { put } from "../api.js";
import { Icon, Btn, Tag, SkelRows, ErrorState, Empty, PageHead, Skel } from "../components/ui.js";
import { Policy } from "./updates.js";
import { plural } from "../fmt.js";

const TABS = [["general", "General"], ["groups", "Server groups"], ["sources", "Update sources"], ["auto", "Auto-update"]];
const KINDS = ["modrinth", "hangar", "spiget", "github"];

export function Settings({ tab }) {
  const q = useQuery("/settings");
  const servers = useQuery("/servers");
  const ov = useQuery("/overview");
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (q.data) setDraft(structuredClone(q.data)); }, [q.data]);
  const dirty = draft && q.data && JSON.stringify(draft) !== JSON.stringify(q.data);

  const save = async () => {
    setSaving(true);
    // Only send the editable sections that changed; the server merges them.
    const body = {};
    for (const k of ["groups", "default_source", "pins", "ignores", "source_map"]) if (JSON.stringify(draft[k]) !== JSON.stringify(q.data[k])) body[k] = draft[k];
    if (body.groups) body.groups = Object.fromEntries(Object.entries(body.groups).filter(([g]) => !(draft.builtin_groups || []).includes(g)));
    try { await put("/settings", body); toast({ kind: "ok", title: "Settings saved" }); invalidate("/settings", "/overview", "/servers", "/matrix", "/updates"); }
    catch (e) { toast({ kind: "err", title: "Couldn't save settings", body: e.message }); }
    setSaving(false);
  };

  return html`<${PageHead} title="Settings" sub=${ov.data?.user && ov.data.user !== "local" ? `Signed in as ${ov.data.user} via Cloudflare Access. Changes apply to everyone.` : "Local session (no Cloudflare Access identity). Changes apply to everyone."} />
    <nav class="settings-nav" aria-label="Settings sections">
      ${TABS.map(([k, l]) => html`<a href=${`#/settings/${k}`} aria-current=${tab === k ? "page" : undefined}>${l}</a>`)}
    </nav>
    ${tab === "auto" ? html`<div style="max-width:560px"><${Policy} au=${ov.data?.auto_update} /></div>`
      : q.error ? html`<${ErrorState} error=${q.error} retry=${q.reload} />`
      : !draft ? html`<div class="panel"><${SkelRows} n=${5} cols=${[20, 50]} /></div>`
      : html`<div class="panel" style="max-width:920px"><div class="panel-body">
          ${tab === "general" && html`<${General} d=${draft} set=${setDraft} servers=${servers.data || []} />`}
          ${tab === "groups" && html`<${Groups} d=${draft} set=${setDraft} servers=${servers.data || []} />`}
          ${tab === "sources" && html`<${Sources} d=${draft} set=${setDraft} />`}
        </div>
        <div class="panel-foot" style="position:sticky;bottom:0;background:var(--surface-2);border-radius:0 0 var(--r-md) var(--r-md)">
          <span class="small muted grow">${dirty ? html`<span style="color:var(--warn)">● Unsaved changes</span>` : "All changes saved"}</span>
          <${Btn} disabled=${!dirty} onClick=${() => setDraft(structuredClone(q.data))}>Discard<//>
          <${Btn} kind="primary" busy=${saving} disabled=${!dirty} onClick=${save}>Save changes<//>
        </div></div>`}`;
}

function General({ d, set, servers }) {
  return html`
    <div class="field-row"><div class="field-label">Datastore base<small>Where AMP instances live. Each instance's <span class="mono">Minecraft/plugins</span> is managed.</small></div>
      <div class="stack" style="gap:6px"><div class="readonly"><${Icon} n="folder-open" cls="i-sm" />${d.base || "Configured on the server"}</div>
        ${(d.base_overridden || d.base_readonly) && html`<span class="small muted row" style="gap:6px">${d.base_overridden && html`<${Tag} kind="warn">override<//>`}Set by the server environment — read-only here.</span>`}</div></div>
    <div class="field-row"><label class="field-label" for="def-src">Default source<small>Pre-selected in Deploy. Usually the staging server.</small></label>
      <div><select id="def-src" class="select" style="max-width:320px" value=${d.default_source || ""} onChange=${e => set({ ...d, default_source: e.currentTarget.value })}>
        ${servers.filter(s => s.plugin_count > 0).map(s => html`<option value=${s.id}>${s.id} (${s.platform})</option>`)}</select></div></div>
    ${d.backup_keep_jobs != null && html`<div class="field-row"><div class="field-label">Backups<small>Old jars and files are kept so jobs can be undone.</small></div>
      <div class="small muted" style="padding-top:7px">Backups are kept for the last <b style="color:var(--fg)">${d.backup_keep_jobs}</b> jobs.</div></div>`}
    <div class="field-row"><div class="field-label">Keyboard<small>Everything is reachable without a mouse.</small></div>
      <div class="small muted stack" style="gap:6px">
        <span><kbd class="kbd">Ctrl</kbd> <kbd class="kbd">K</kbd> or <kbd class="kbd">/</kbd> command palette</span>
        <span><kbd class="kbd">G</kbd> then <kbd class="kbd">D</kbd> <kbd class="kbd">S</kbd> <kbd class="kbd">P</kbd> <kbd class="kbd">U</kbd> <kbd class="kbd">Y</kbd> <kbd class="kbd">A</kbd> jump to Dashboard, Servers, Plugins, Updates, Deploy, Activity</span></div></div>`;
}

function Groups({ d, set, servers }) {
  const [name, setName] = useState("");
  const groups = d.groups || {};
  const eligible = servers.filter(s => (s.family || "bukkit") === "bukkit");
  const upd = (g) => set({ ...d, groups: g });
  const add = (e) => { e.preventDefault(); const n = name.trim(); if (!n || groups[n]) return; upd({ ...groups, [n]: [] }); setName(""); };
  const del = async (g) => { if (await confirmDialog({ danger: true, title: `Delete group “${g}”?`, body: "Servers aren't affected — only the preset is removed.", confirmLabel: "Delete group" })) { const n = { ...groups }; delete n[g]; upd(n); } };
  return html`<div class="stack">
    <p class="small muted">Groups become one-click target presets in Deploy. Proxy and modded servers can't be added.</p>
    ${!Object.keys(groups).length && html`<${Empty} icon="layers" title="No groups yet">Create one below, e.g. “Survival” or “Minigames”.<//>`}
    ${Object.entries(groups).map(([g, ids]) => { const builtin = (d.builtin_groups || []).includes(g); return html`<div class="group-card">
      <header><${Icon} n="layers" cls="i-sm" /><b>${g}</b><span class="small muted">${plural(ids.length, "server")}</span>${builtin && html`<${Tag}>built-in<//>`}<span class="grow"></span>
        ${!builtin && html`<${Btn} size="sm" kind="ghost" icon="trash-2" aria-label=${`Delete group ${g}`} onClick=${() => del(g)} />`}</header>
      <div class="targets" role="group" aria-label=${`Servers in ${g}`}>${eligible.map(s => { const on = ids.includes(s.id); return html`<button type="button" class="target" disabled=${builtin} aria-pressed=${on ? "true" : "false"}
        onClick=${() => upd({ ...groups, [g]: on ? ids.filter(x => x !== s.id) : [...ids, s.id] })}><span class="tick">${on && html`<${Icon} n="check" />`}</span>${s.id}</button>`; })}</div>
    </div>`; })}
    <form class="row" onSubmit=${add} style="max-width:420px">
      <input class="input" placeholder="New group name" aria-label="New group name" value=${name} onInput=${e => setName(e.currentTarget.value)} />
      <${Btn} type="submit" icon="plus" disabled=${!name.trim()}>Add group<//>
    </form>
  </div>`;
}

function Sources({ d, set }) {
  const mx = useQuery("/matrix");
  const [filter, setFilter] = useState("");
  const rows = useMemo(() => {
    const f = filter.toLowerCase();
    return (mx.data?.plugins || []).filter(p => !f || p.name.toLowerCase().includes(f))
      .map(p => ({ ...p, unknown: !p.source && Object.values(p.cells).every(c => c.status === "unknown") }))
      .sort((a, b) => (b.unknown - a.unknown) || a.name.localeCompare(b.name));
  }, [mx.data, filter]);
  const sm = d.source_map || {};
  const setMap = (key, v) => { const n = { ...sm }; if (!v.kind && !v.id) delete n[key]; else n[key] = v; set({ ...d, source_map: n }); };
  const unpin = (k) => { const p = { ...d.pins }; delete p[k]; set({ ...d, pins: p }); };
  return html`<div class="stack">
    <p class="small muted">Plugins are matched on Modrinth by file hash automatically. For anything shown as <b>untracked</b>, map it to a Modrinth slug, Hangar <span class="mono">owner/slug</span>, Spiget resource id, or GitHub <span class="mono">owner/repo</span>.</p>
    ${(Object.keys(d.pins || {}).length > 0 || (d.ignores || []).length > 0) && html`<div class="row wrap" style="gap:6px">
      ${Object.entries(d.pins || {}).map(([k, v]) => html`<span class="pick-chip"><${Icon} n="pin" cls="i-xs" /><span>${k} @ ${v}</span><button type="button" aria-label=${`Unpin ${k}`} onClick=${() => unpin(k)}><${Icon} n="x" cls="i-xs" /></button></span>`)}
      ${(d.ignores || []).map(k => html`<span class="pick-chip"><${Icon} n="eye-off" cls="i-xs" /><span>${k}</span><button type="button" aria-label=${`Stop ignoring ${k}`} onClick=${() => set({ ...d, ignores: d.ignores.filter(x => x !== k) })}><${Icon} n="x" cls="i-xs" /></button></span>`)}
    </div>`}
    <div class="input-wrap" style="max-width:300px"><${Icon} n="search" cls="i-sm" /><input class="input" type="search" placeholder="Filter plugins" aria-label="Filter plugins" value=${filter} onInput=${e => setFilter(e.currentTarget.value)} /></div>
    ${mx.error ? html`<${ErrorState} error=${mx.error} retry=${mx.reload} />` : mx.loading ? html`<${SkelRows} n=${6} />` : html`<div class="panel tbl-wrap" style="max-height:520px">
      <table class="tbl"><thead><tr><th scope="col">Plugin</th><th scope="col">Detected</th><th scope="col">Manual source</th><th scope="col">Id / slug</th></tr></thead>
      <tbody>${rows.map(p => { const m = sm[p.key] || {}; return html`<tr key=${p.key}>
        <td><span class="strong">${p.name}</span>${p.family && p.family !== "bukkit" ? html` <span class="small muted">· ${p.family}</span>` : ""}</td>
        <td>${m.kind ? html`<${Tag} kind="accent">manual<//>` : p.unknown ? html`<${Tag} icon="circle-dashed">untracked<//>` : html`<span class="row" style="gap:6px"><${Tag} kind="ok" icon="check">auto<//><span class="small muted">${p.source?.kind}</span></span>`}</td>
        <td style="width:150px"><select class="select" style="height:28px" aria-label=${`Source for ${p.name}`} value=${m.kind || ""} onChange=${e => setMap(p.key, { ...m, kind: e.currentTarget.value })}>
          <option value="">—</option>${KINDS.map(k => html`<option value=${k}>${k}</option>`)}</select></td>
        <td style="min-width:180px"><input class="input mono" style="height:28px" aria-label=${`Id for ${p.name}`} placeholder=${m.kind === "github" ? "owner/repo" : m.kind === "spiget" ? "resource id" : "slug"} value=${m.id || ""} disabled=${!m.kind} onInput=${e => setMap(p.key, { ...m, id: e.currentTarget.value })} /></td>
      </tr>`; })}</tbody></table></div>`}
  </div>`;
}
