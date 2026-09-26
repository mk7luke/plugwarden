// Deploy composer — pick items from a source, pick targets and an action, review the
// automatic dry-run plan, then execute with a live log.
import { html, useState, useEffect, useMemo, useRef } from "../lib.js";
import { useQuery, useStore, confirmDialog, toast, setState } from "../store.js";
import { api, get, post, searchFiles } from "../api.js";
import { trackJob, isActive, jobTone } from "../jobs.js";
import { LogView } from "../components/overlays.js";
import { Icon, Btn, Tag, SkelRows, ErrorState, Empty, PageHead, Check, Skel } from "../components/ui.js";
import { bytes, plural, midTrunc, isDataFile } from "../fmt.js";

const ACTIONS = [
  ["sync", "Sync existing", "refresh-cw", "Overwrite items that already exist on targets"],
  ["install", "Install", "plus", "Copy items, adding them where missing"],
  ["replace", "Replace jar", "arrow-up-down", "Swap in the new jar, remove the old version"],
  ["delete", "Delete", "trash-2", "Remove items from targets (backed up)"],
];
const OUT = { changed: ["refresh-cw", "op-update"], unchanged: ["check", "op-same"], skipped: ["minus", "op-same"], error: ["triangle-alert", "op-delete"] };
const emptyItems = () => ({ jars: [], folders: [], paths: [], uploads: [] });
const FAMILY = { velocity: "velocity", fabric: "fabric" };
export const familyOf = (s) => s?.family || FAMILY[s?.platform] || "bukkit";
const kindOf = (e) => e.type === "dir" ? "dir" : (e.jar || /\.jar$/i.test(e.name)) ? "jar" : "file";
// Why a server can't receive items from `src`, or null if it can.
export function ineligible(s, src) {
  const f = familyOf(s);
  if (f === "fabric") return "Fabric server — it has no plugins";
  if (src && f !== familyOf(src)) return f === "velocity" ? "Velocity proxy — Bukkit plugins are never pushed here" : `A ${s.platform} server can't receive items from a ${src.platform} source`;
  return null;
}

export function Deploy({ query }) {
  const settings = useQuery("/settings");
  const servers = useQuery("/servers");
  const [source, setSource] = useState(null);
  const [items, setItems] = useState(emptyItems);
  const [targets, setTargets] = useState(() => new Set((query.targets || "").split(",").filter(Boolean)));
  const [action, setAction] = useState(query.action || "sync");
  const [opts, setOpts] = useState({ install: false, include_data: false });
  const [jobId, setJobId] = useState(null);
  const [force, setForce] = useState(false);
  const [identity, setIdentity] = useState(null); // null | "keep" | "overwrite" — required when a push changes server-specific keys
  const [nonce, setNonce] = useState(0);       // bump to re-plan
  const [planInfo, setPlanInfo] = useState(null); // {changes, servers} for the mobile bar

  useEffect(() => { if (!source && settings.data) setSource(settings.data.default_source || servers.data?.[0]?.id); }, [settings.data, servers.data]);
  useEffect(() => {
    const list = (v) => (v || "").split(",").filter(Boolean);
    if (query.jar || query.folders || query.paths) setItems(i => ({ ...i, jars: list(query.jar), folders: list(query.folders), paths: list(query.paths) }));
    if (query.targets) setTargets(new Set(query.targets.split(",").filter(Boolean)));
    if (query.action) setAction(query.action);
    if (query.data === "1") setOpts(o => ({ ...o, include_data: true }));
  }, [query.data, query.jar, query.folders, query.paths, query.targets, query.action]);

  // "Remove plugin X" from the matrix: resolve its jar + folder in the source root.
  const root = useQuery(source && query.plugin ? `/servers/${encodeURIComponent(source)}/tree?path=` : null);
  useEffect(() => {
    if (!query.plugin || !root.data) return;
    const n = query.plugin.toLowerCase().replace(/[^a-z0-9]/g, "");
    const norm = (s) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
    const jar = root.data.entries.find(e => kindOf(e) === "jar" && norm(e.name).startsWith(n));
    const dir = root.data.entries.find(e => e.type === "dir" && norm(e.name) === n);
    setItems({ ...emptyItems(), jars: jar ? [jar.name] : [], folders: dir ? [dir.name] : [] });
  }, [query.plugin, root.data]);

  const count = items.jars.length + items.folders.length + items.paths.length + items.uploads.length;
  // Replace jar swaps versioned jars only; a folder here would become an rsync --delete mirror.
  const nonJars = [...items.folders.map(f => f + "/"), ...items.paths.filter(p => !/\.jar$/i.test(p))];
  const replaceBlocked = action === "replace" && nonJars.length > 0;
  const sv = servers.data || [];
  const srcObj = sv.find(x => x.id === source);
  const tgt = [...targets].filter(t => t !== source && sv.some(x => x.id === t && !ineligible(x, srcObj)));
  // Drop ineligible targets (e.g. from a #/deploy?targets= link) once servers are known, so no chip shows as picked.
  useEffect(() => {
    if (!sv.length || !srcObj) return;
    const keep = [...targets].filter(t => sv.some(x => x.id === t && x.id !== source && !ineligible(x, srcObj)));
    if (keep.length !== targets.size) setTargets(new Set(keep));
  }, [sv.length, source, [...targets].join(",")]);
  const body = useMemo(() => ({
    source, targets: tgt, action,
    items: { jars: items.jars, folders: items.folders, paths: items.paths, uploads: items.uploads.map(u => u.upload_id) },
    options: opts, ...(force && action === "delete" ? { force: true } : {}),
    ...(identity === "keep" || identity === "keep_skip" ? { preserve_keys: "server_specific" } : identity === "overwrite" ? { overwrite_server_specific: true } : {}),
    ...(identity === "keep_skip" ? { skip_unmergeable: true } : {}),
  }), [source, tgt.join(","), action, items, opts, force, identity]);

  const running = useStore(s => s.jobs.find(j => j.id === jobId));
  useEffect(() => { setState({ inlineJob: jobId }); return () => setState({ inlineJob: null }); }, [jobId]);

  const reset = () => { setItems(emptyItems()); setJobId(null); };
  const execute = async (plan) => {
    const changes = plan.servers.reduce((a, s) => a + s.changed, 0);
    const affected = plan.servers.filter(s => s.changed);
    const failing = plan.servers.flatMap(s => s.rows.filter(r => r.outcome === "error"));
    if (failing.length && action !== "delete") {
      const ok = await confirmDialog({
        title: `Execute ${plural(changes, "change")} and skip ${failing.length} that would fail?`,
        body: "These items can't be applied as planned. They'll be left untouched on their servers; everything else runs. Backups are kept for undo.",
        list: failing.map(r => `${r.server}: ${r.item} — ${r.detail}`),
        confirmLabel: `Execute ${changes} · skip ${failing.length}`,
      });
      if (!ok) return;
    }
    if (action === "delete") {
      const ok = await confirmDialog({
        danger: true, title: `Delete from ${plural(affected.length, "server")}?`, confirmLabel: "Delete",
        body: `${plural(changes, "item")} will be removed. Backups are kept so you can undo from Activity.`,
        list: affected.map(s => `${s.server}: ${s.rows.filter(r => r.outcome === "changed").map(r => r.item).join(", ")}`),
        typeToConfirm: affected.length > 1 ? `delete ${affected.length} servers` : null,
      });
      if (!ok) return;
    } else if (affected.length >= 6) {
      const ok = await confirmDialog({ title: `Apply to ${plural(affected.length, "server")}?`, body: `${plural(changes, "change")} will be written. Backups are kept for undo.`, confirmLabel: "Execute" });
      if (!ok) return;
    }
    try {
      const failingNow = plan.servers.some(s => s.rows.some(r => r.outcome === "error"));
      const r = await post("/deploy", { plan_id: plan.plan_id, ...(failingNow ? { skip_failing: true } : {}) });
      setJobId(r.job_id);
      trackJob(r.job_id, { title: `Deploy · ${ACTIONS.find(a => a[0] === action)[1]}` });
    } catch (e) {
      toast({ kind: "err", title: e.status === 409 ? "Targets changed since this preview" : "Deploy didn't start",
        body: e.status === 409 ? `${e.detail?.code === "has_failing" || e.code === "has_failing" ? "some items would fail" : e.conflicts.map(c => c.reason === "needs_decision" ? "a server-specific value appeared since the preview" : `${c.server || "source"} · ${c.item}: ${c.reason}`).join("; ") || e.message}. The plan was refreshed — review it and execute again.` : e.message });
      if (e.status === 409) setNonce(n => n + 1);
    }
  };

  const loadErr = servers.error || settings.error;
  if (loadErr) return html`<${PageHead} title="Deploy" sub="Push configs, roll out jars, or remove plugins — with a dry-run plan before anything is touched." />
    <div class="panel"><div class="panel-body"><${ErrorState} error=${loadErr} retry=${() => { servers.reload(); settings.reload(); }} /></div></div>`;

  return html`
    <${PageHead} title="Deploy" sub="Push configs, roll out jars, or remove plugins — with a dry-run plan before anything is touched." >
      ${(count > 0 || targets.size > 0) && html`<${Btn} kind="ghost" icon="x" onClick=${() => { reset(); setTargets(new Set()); history.replaceState(null, "", "#/deploy"); }}>Clear<//>`}
    <//>
    <div class="composer">
      <${SourceCol} source=${source} setSource=${(s) => { setSource(s); setItems(emptyItems()); }} servers=${sv} items=${items} setItems=${setItems} setAction=${setAction} autoUpload=${query.upload} initialFilter=${query.q} action=${action} />
      <${TargetCol} source=${source} servers=${sv} groups=${settings.data?.groups || {}} targets=${targets} setTargets=${setTargets} action=${action} setAction=${setAction} opts=${opts} setOpts=${setOpts} hasFolders=${items.folders.length > 0} />
      <div class="plan-col" id="plan">
        ${jobId ? html`<${Execution} job=${running} jobId=${jobId} onNew=${reset} />`
          : replaceBlocked ? html`<${ReplaceBlocked} nonJars=${nonJars} onRemove=${() => setItems(it => ({ ...it, folders: [], paths: it.paths.filter(p => /\.jar$/i.test(p)) }))} onSync=${() => setAction("sync")} />`
          : html`<${PlanCol} body=${body} nonce=${nonce} ready=${count > 0 && tgt.length > 0} count=${count} targets=${tgt.length} onExecute=${execute} action=${action}
              force=${force} setForce=${setForce} identity=${identity} setIdentity=${setIdentity}
              onRemoveFailing=${(paths) => setItems(it => ({ ...it, jars: it.jars.filter(j => !paths.includes(j)), paths: it.paths.filter(p => !paths.includes(p)), folders: it.folders.filter(f => !paths.includes(f + "/") && !paths.includes(f)) }))} onInstall=${() => setOpts(o => ({ ...o, install: true }))} onSummary=${setPlanInfo} />`}
      </div>
    </div>
    ${!jobId && (count > 0 || tgt.length > 0) && html`<div class="plan-bar only-sm" role="region" aria-label="Plan summary">
      <span class="plan-bar-sum">${plural(count, "item")} · ${plural(tgt.length, "server")}${planInfo ? html` · <b>${plural(planInfo.changes, "change")}</b>` : ""}</span>
      ${planInfo && planInfo.changes === 0
        ? html`<${Btn} kind="ghost" disabled>Nothing to change<//>`
        : html`<${Btn} kind="primary" onClick=${() => document.getElementById("plan")?.scrollIntoView({ behavior: "smooth", block: "start" })}>Review plan<//>`}
    </div>`}`;
}

// ---------- column 1: source browser ----------
function SourceCol({ source, setSource, servers, items, setItems, setAction, autoUpload, initialFilter, action }) {
  const jarOnly = action === "replace";
  const blocked = (kind) => jarOnly && kind !== "jar";
  const [path, setPath] = useState("");
  const [filter, setFilter] = useState(initialFilter || "");
  const [over, setOver] = useState(false);
  const [uploading, setUploading] = useState([]);
  const fileRef = useRef();
  useEffect(() => setPath(""), [source]);
  useEffect(() => { if (autoUpload) setTimeout(() => fileRef.current?.click(), 300); }, [autoUpload]);
  const tree = useQuery(source ? `/servers/${encodeURIComponent(source)}/tree?path=${encodeURIComponent(path)}` : null);
  // Two or more characters search the whole plugins folder (recursive), not just this folder.
  const [q, setQ] = useState("");
  useEffect(() => { const t = setTimeout(() => setQ(filter.trim().length >= 2 ? filter.trim() : ""), 220); return () => clearTimeout(t); }, [filter]);
  const [found, setFound] = useState({ loading: false });
  useEffect(() => {
    if (!source || !q) { setFound({ loading: false }); return; }
    let live = true;
    setFound({ loading: true });
    searchFiles(source, q).then(d => live && setFound({ data: d }), e => live && setFound({ error: e, reload: () => setQ(q + "") }));
    return () => { live = false; };
  }, [source, q]);

  const full = (name) => (path ? `${path}/${name}` : name);
  const isPicked = (e) => path === ""
    ? (e.kind === "jar" ? items.jars.includes(e.name) : e.kind === "dir" ? items.folders.includes(e.name) : items.paths.includes(e.name))
    : items.paths.includes(full(e.name));
  // Top-level jars and folders are picked as jars/folders; anything nested is a path.
  const pickOf = (rel, kind) => !rel.includes("/") && kind === "jar" ? ["jars", rel] : !rel.includes("/") && kind === "dir" ? ["folders", rel] : ["paths", rel];
  const pickedRel = (rel, kind) => { const [k, v] = pickOf(rel, kind); return items[k].includes(v); };
  const toggleRel = (rel, kind) => setItems(it => {
    const [k, v] = pickOf(rel, kind);
    return { ...it, [k]: it[k].includes(v) ? it[k].filter(x => x !== v) : [...it[k], v] };
  });
  const toggle = (e) => !blocked(e.kind) && toggleRel(full(e.name), e.kind);
  const entries = useMemo(() => {
    const es = (tree.data?.entries || []).map(e => ({ ...e, kind: kindOf(e) }));
    const f = filter.toLowerCase();
    const order = { jar: 0, dir: 1, file: 2 };
    return es.filter(e => !f || e.name.toLowerCase().includes(f))
      .sort((a, b) => (a.name.startsWith(".") - b.name.startsWith(".")) || (order[a.kind] - order[b.kind]) || a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
  }, [tree.data, filter]);

  async function upload(files) {
    for (const f of files) {
      if (!/\.jar$/i.test(f.name)) { toast({ kind: "err", title: `${f.name} isn't a .jar`, body: "Only plugin jars can be uploaded." }); continue; }
      setUploading(u => [...u, f.name]);
      const fd = new FormData(); fd.append("file", f);
      try {
        const r = await api("/upload", { method: "POST", form: fd });
        setItems(it => ({ ...it, uploads: [...it.uploads, r] }));
        const exists = (tree.data?.entries || []).some(e => kindOf(e) === "jar" && r.plugin_name && e.name.toLowerCase().startsWith(r.plugin_name.toLowerCase()));
        setAction(exists ? "replace" : "install");
        toast({ kind: "ok", title: `Uploaded ${r.plugin_name || r.name}${r.version ? " " + r.version : ""}`, body: exists ? "Action set to Replace jar." : "Action set to Install." });
      } catch (e) { toast({ kind: "err", title: `Upload failed: ${f.name}`, body: e.message }); }
      setUploading(u => u.filter(n => n !== f.name));
    }
  }
  const drop = {
    onDragOver: (e) => { e.preventDefault(); setOver(true); },
    onDragLeave: (e) => { if (!e.currentTarget.contains(e.relatedTarget)) setOver(false); },
    onDrop: (e) => { e.preventDefault(); setOver(false); upload([...e.dataTransfer.files]); },
  };
  const crumbs = path ? path.split("/") : [];
  const picked = [...items.uploads.map(u => ["upload", u.name, u]), ...items.jars.map(j => ["jar", j]), ...items.folders.map(f => ["dir", f + "/"]), ...items.paths.map(p => ["file", p])];
  const unpick = ([k, v, u]) => setItems(it => ({
    ...it, uploads: k === "upload" ? it.uploads.filter(x => x !== u) : it.uploads, jars: it.jars.filter(x => x !== v),
    folders: it.folders.filter(x => x + "/" !== v), paths: it.paths.filter(x => x !== v),
  }));

  return html`<section class="panel browser" aria-labelledby="src-h" ...${drop}>
    <div class="panel-head"><span class=${"step-n" + (picked.length ? " done" : "")}>${picked.length ? html`<${Icon} n="check" cls="i-xs" />` : "1"}</span><h2 id="src-h">What</h2>
      <span class="spacer"></span>
      <label class="sr-only" for="src-sel">Source server</label>
      <select id="src-sel" class="select" style="width:auto;height:28px;font-size:var(--fs-xs)" value=${source || ""} onChange=${e => setSource(e.currentTarget.value)}>
        ${servers.filter(s => s.plugin_count > 0).map(s => html`<option value=${s.id}>from ${s.id}</option>`)}</select>
    </div>
    <div class="browser-tools">
      <div class="input-wrap"><${Icon} n="search" cls="i-sm" /><input class="input" type="search" placeholder="Search all files, e.g. config.yml" aria-label="Search source files" value=${filter} onInput=${e => setFilter(e.currentTarget.value)} /></div>
      ${jarOnly && html`<p class="small jar-only-note"><${Icon} n="info" cls="i-xs" />Replace jar only works on .jar files — folders and config files are disabled.</p>`}
      <nav class="path-bar" aria-label="Folder path" hidden=${!!q}>
        <button type="button" onClick=${() => setPath("")}>plugins</button>
        ${crumbs.map((c, i) => html`<span aria-hidden="true">/</span><button type="button" onClick=${() => setPath(crumbs.slice(0, i + 1).join("/"))}>${c}</button>`)}
      </nav>
    </div>
    <div class="tree" role="list" aria-label=${q ? `Search results for ${q}` : "Source files"}>
      ${q ? html`<${SearchResults} found=${found} q=${q} picked=${pickedRel} toggle=${(p, k) => !blocked(k) && toggleRel(p, k)} blocked=${blocked} open=${(p) => { setPath(p); setFilter(""); }} />`
        : tree.error ? html`<div style="padding:8px"><${ErrorState} error=${tree.error} retry=${tree.reload} /></div>`
        : tree.loading ? Array.from({ length: 10 }, (_, i) => html`<div class="tree-row"><${Skel} w="16px" h=${16} /><${Skel} w=${`${40 + (i * 13) % 45}%`} /></div>`)
        : !entries.length ? html`<div class="empty" style="padding:24px"><p>${filter ? `Nothing matches “${filter}”.` : "This folder is empty."}</p></div>`
        : entries.map(e => {
          const pk = isPicked(e);
          const ico = e.kind === "jar" ? ["package", "ico-jar"] : e.kind === "dir" ? ["folder", "ico-dir"] : [/\.(ya?ml|json|toml|conf|properties)$/.test(e.name) ? "file-code" : "file-text", "ico-file"];
          return html`<div class=${"tree-row" + (pk ? " is-picked" : "") + (blocked(e.kind) ? " is-blocked" : "")} title=${blocked(e.kind) ? "Replace jar only works on .jar files" : undefined} role="listitem" onClick=${ev => !ev.target.closest("button, input, label") && toggle(e)} onDblClick=${() => e.kind === "dir" && (setPath(full(e.name)), setFilter(""))}>
            <${Check} label=${`Pick ${e.name}`} checked=${pk} disabled=${blocked(e.kind)} onChange=${() => toggle(e)} />
            <${Icon} n=${ico[0]} cls=${"i-sm " + ico[1]} />
            <span class=${"name" + (e.kind !== "dir" ? " mono" : "") + (e.name.startsWith(".") ? " muted" : "")} title=${e.name}>${e.name}</span>
            ${e.kind === "file" && isDataFile(full(e.name)) && html`<span class="tag tag-warn" title="Live server data (spawns, saves, per-player records), not configuration">data file</span>`}
            ${e.size != null && html`<span class="size">${bytes(e.size)}</span>`}
            ${e.kind === "dir" && html`<${Btn} size="sm" kind="ghost" icon="chevron-right" cls="open" aria-label=${`Open ${e.name}`} onClick=${() => { setPath(full(e.name)); setFilter(""); }} />`}
          </div>`;
        })}
    </div>
    ${picked.length > 0 && html`<div style="padding:8px 12px;border-top:1px solid var(--line)"><div class="field-label" style="margin-bottom:6px">${plural(picked.length, "item")} picked</div>
      <div class="picked">${picked.map(p => html`<span class=${"pick-chip" + (p[0] === "upload" ? " is-upload" : "")}>
        <${Icon} n=${p[0] === "upload" ? "upload" : p[0] === "jar" ? "package" : p[0] === "dir" ? "folder" : "file"} cls="i-xs" /><span title=${p[1]}>${p[1]}</span>
        <button type="button" aria-label=${`Remove ${p[1]}`} onClick=${() => unpick(p)}><${Icon} n="x" cls="i-xs" /></button></span>`)}</div></div>`}
    <div class=${"dropzone" + (over ? " is-over" : "")}>
      ${uploading.length ? html`<${Icon} n="loader-circle" cls="i-sm spin" />Uploading ${uploading.join(", ")}…`
        : html`<${Icon} n="upload" cls="i-sm" /><span>Drop a .jar here or <label for="jar-in" tabindex="0" onKeyDown=${e => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), fileRef.current.click())}>browse</label></span>`}
      <input id="jar-in" ref=${fileRef} type="file" accept=".jar" multiple class="sr-only" onChange=${e => { upload([...e.currentTarget.files]); e.currentTarget.value = ""; }} />
    </div>
  </section>`;
}

// ---------- column 2: targets + action ----------
function TargetCol({ source, servers, groups, targets, setTargets, action, setAction, opts, setOpts, hasFolders }) {
  const src = servers.find(s => s.id === source);
  const eligible = servers.filter(s => s.id !== source && !ineligible(s, src));
  const set = (ids) => setTargets(new Set(ids.filter(id => eligible.some(s => s.id === id))));
  const presets = [["All eligible", eligible.map(s => s.id)], ...Object.entries(groups)];
  const same = (ids) => { const a = ids.filter(id => eligible.some(s => s.id === id)); return a.length && a.length === targets.size && a.every(i => targets.has(i)); };
  // Only the first preset matching the selection is pressed (several can resolve to the same set).
  const activePreset = presets.find(([, ids]) => same(ids))?.[0];
  const flip = (id) => setTargets(t => { const n = new Set(t); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const n = [...targets].filter(t => t !== source).length;
  return html`<div class="stack" style="gap:var(--s-4)">
    <section class="panel" aria-labelledby="tg-h">
      <div class="panel-head"><span class=${"step-n" + (n ? " done" : "")}>${n ? html`<${Icon} n="check" cls="i-xs" />` : "2"}</span><h2 id="tg-h">Where</h2><span class="sub">${n ? plural(n, "server") : "no targets"}</span>
        <span class="spacer"></span>${n > 0 && html`<${Btn} size="sm" kind="ghost" onClick=${() => setTargets(new Set())}>Clear<//>`}</div>
      <div class="panel-body stack" style="gap:10px">
        <div class="presets" role="group" aria-label="Target presets">
          ${presets.map(([name, ids]) => html`<${Btn} size="sm" kind=${activePreset === name ? "" : "ghost"} aria-pressed=${activePreset === name ? "true" : "false"} icon="layers" onClick=${() => set(ids)}>${name}<//>`)}
          <a class="btn btn-sm btn-ghost" href="#/settings/groups" title="Edit groups"><${Icon} n="plus" cls="i-sm" />Group</a>
        </div>
        <div class="targets" role="group" aria-label="Target servers">
          ${servers.filter(s => s.id !== source).map(s => {
            const why = ineligible(s, src);
            const tag = why && (familyOf(s) === "velocity" ? "proxy" : familyOf(s) === "fabric" ? "Fabric" : s.platform);
            return html`<button type="button" class="target" aria-pressed=${targets.has(s.id) ? "true" : "false"} disabled=${!!why} title=${why || s.id} aria-label=${why ? `${s.id} — ${why}` : undefined} onClick=${() => flip(s.id)}>
              <span class="tick">${targets.has(s.id) && html`<${Icon} n="check" />`}</span>${s.id}${tag && html`<span class="target-why">· ${tag}</span>`}</button>`;
          })}
          ${!servers.length && Array.from({ length: 8 }, () => html`<${Skel} w="96px" h=${28} r=${14} />`)}
        </div>
      </div>
    </section>
    <section class="panel" aria-labelledby="ac-h">
      <div class="panel-head"><span class="step-n done"><${Icon} n="check" cls="i-xs" /></span><h2 id="ac-h">How</h2></div>
      <div class="panel-body stack">
        <div class="action-grid" role="radiogroup" aria-label="Action">
          ${ACTIONS.map(([k, l, i, d]) => html`<button type="button" role="radio" aria-checked=${action === k ? "true" : "false"} class=${"action-opt" + (k === "delete" ? " danger" : "")} onClick=${() => setAction(k)}>
            <${Icon} n=${i} /><div><b>${l}</b><span>${d}</span></div></button>`)}
        </div>
        <div class="row wrap" style="gap:16px">
          ${action === "sync" && html`<label class="switch small"><input type="checkbox" checked=${opts.install} onChange=${e => setOpts({ ...opts, install: e.currentTarget.checked })} />Also install where missing</label>`}
          ${hasFolders && (action === "sync" || action === "install") && html`<label class="switch small"><input type="checkbox" checked=${opts.include_data} onChange=${e => setOpts({ ...opts, include_data: e.currentTarget.checked })} />Include player data, logs and databases</label>`}
          <span class="small muted row" style="gap:6px"><${Icon} n="shield" cls="i-sm" />Every change is backed up and can be undone from Activity</span>
        </div>
      </div>
    </section>
  </div>`;
}

// ---------- column 3: live plan ----------
// Group the flat dry-run results into per-server rows, in target order.
function groupPlan(raw, targets) {
  const by = new Map(targets.map(t => [t, []]));
  for (const r of raw.results || []) (by.get(r.server) || by.set(r.server, []).get(r.server)).push(r);
  return {
    // Reported even when force is set, so the acknowledgement stays visible.
    shared: (raw.warnings || []).filter(w => w.shared_with?.length).map(w => ({ server: w.server, item: w.folder, shared_with: w.shared_with })),
    identity: (raw.warnings || []).filter(w => w.type === "server_specific"),
    dataWarn: (raw.warnings || []).find(w => w.type === "include_data"),
    unchecked: (raw.warnings || []).filter(w => w.type === "unchecked_config"),
    needsDecision: !!raw.needs_decision,
    servers: [...by].map(([server, rows]) => ({ server, rows, changed: rows.filter(r => r.outcome === "changed").length, skipped: rows.filter(r => r.outcome === "skipped").length, errors: rows.filter(r => r.outcome === "error").length })),
    summary: raw.summary || {},
    plan_id: raw.plan_id,
  };
}

// Collapse per-server shared-folder rows into one line per folder + plugin set.
function sharedGroups(rows) {
  const m = new Map();
  for (const r of rows) {
    const k = r.item + "|" + r.shared_with.join(",");
    const g = m.get(k) || m.set(k, { item: r.item, names: r.shared_with, servers: [] }).get(k);
    g.servers.push(r.server);
  }
  return [...m.values()];
}

function PlanCol({ body, nonce, ready, count, targets, onExecute, action, force, setForce, identity, setIdentity, onRemoveFailing, onInstall, onSummary }) {
  const [plan, setPlan] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(false);
  const key = JSON.stringify(body);
  useEffect(() => {
    if (!ready) { setPlan(null); setErr(null); return; }
    let live = true;
    setLoading(true);
    const t = setTimeout(() => post("/deploy/plan", body).then(p => live && (setPlan(groupPlan(p, body.targets)), setErr(null)), e => live && (setErr(e), setPlan(null))).finally(() => live && setLoading(false)), 350);
    return () => { live = false; clearTimeout(t); };
  }, [key, ready, nonce]);
  useEffect(() => onSummary?.(plan ? { changes: plan.servers.reduce((a, s) => a + s.changed, 0) } : null), [plan]);

  const shared = plan?.shared || [];
  const changes = plan ? plan.servers.reduce((a, s) => a + s.changed, 0) : 0;
  const affected = plan ? plan.servers.filter(s => s.changed).length : 0;
  const errors = plan ? plan.servers.reduce((a, s) => a + s.errors, 0) : 0;
  // Failing items the user picked directly (not files inside a picked folder) can simply be dropped.
  const picked = [...body.items.jars, ...body.items.paths, ...body.items.folders.map(f => f + "/")];
  const removable = plan ? [...new Set(plan.servers.flatMap(s => s.rows.filter(r => r.outcome === "error").map(r => r.item)).filter(i => picked.includes(i)))] : [];
  const unmergeable = plan ? plan.servers.some(s => s.rows.some(r => r.reason_code === "merge_unsafe")) || (plan.identity || []).some(w => w.keys.some(k => k.reason === "complex")) : false;
  return html`<section class="panel plan" aria-labelledby="pl-h" aria-busy=${loading ? "true" : "false"}>
    <div class="panel-head"><span class=${"step-n" + (plan && changes ? " done" : plan ? " idle" : "")}>${plan && changes ? html`<${Icon} n="check" cls="i-xs" />` : plan ? "–" : "3"}</span><h2 id="pl-h">Plan preview</h2>
      <span class="sub">${loading ? html`<span class="row" style="gap:4px"><${Icon} n="loader-circle" cls="i-xs spin" />dry run…</span>` : plan ? "automatic dry run" : ""}</span></div>
    ${(shared.length > 0 || force) && action === "delete" && html`<div class="shared-warn" style="margin:10px 12px 0" role="alert">
      <${Icon} n="triangle-alert" cls="i-sm" />
      <div><b>${force ? "Shared folders will be deleted" : "Shared folders are skipped"}</b>
        <p>Still used by installed plugins:</p>
        <ul class="shared-list">${sharedGroups(shared).map(g => html`<li><span class="mono">${g.item}/</span> — ${g.names.join(", ")} <span class="muted">(${g.servers.join(", ")})</span></li>`)}</ul>
        <label class="check"><input type="checkbox" checked=${force} onChange=${e => setForce(e.currentTarget.checked)} />Delete them anyway — I understand shared data will be lost</label></div>
    </div>`}
    ${plan?.unchecked.length > 0 && html`<div class="plan-warn is-info"><${Icon} n="info" cls="i-sm" /><span>Not checked for server-specific values: ${plan.unchecked.map(w => `${w.path || "config files"} on ${w.server} (${w.reason})`).join("; ")}</span></div>`}
    ${plan?.dataWarn && html`<div class="plan-warn"><${Icon} n="triangle-alert" cls="i-sm" />${plan.dataWarn.message}</div>`}
    ${errors > 0 && html`<div class="plan-warn"><${Icon} n="triangle-alert" cls="i-sm" />
      <span class="grow">${plural(errors, "item")} would fail${changes ? " — they'll be skipped if you execute" : ""}. Details below.</span>
      ${removable.length > 0 && html`<button type="button" class="linkbtn" onClick=${() => onRemoveFailing(removable)}>Remove failing items</button>`}</div>`}
    <div class="plan-body">
      ${plan?.servers.some(sv => sv.rows.some(r => r.data_excluded)) && html`<div class="data-note small"><${Icon} n="shield" cls="i-sm" />
        <span><b>Player data, logs and databases are protected.</b> Databases, userdata/playerdata, per-player files, logs, caches, backups and LuckPerms storage inside folders are left untouched. Turn on “Include player data, logs and databases” to mirror them too.</span></div>`}
      ${plan?.identity.length > 0 && html`<${IdentityBlock} warnings=${plan.identity} choice=${identity} setChoice=${setIdentity} unmergeable=${unmergeable || identity === "keep_skip"} />`}
      ${!ready ? html`<${Empty} icon="list-checks" title="Nothing planned yet">
          ${!count && !targets ? "Pick items on the left and target servers, and a dry-run plan appears here automatically."
            : !count ? "Now pick at least one jar, folder or file to deploy." : "Now pick at least one target server."}<//>`
        : err ? html`<div class="panel-body"><${ErrorState} title="This plan can't run" error=${err} /></div>`
        : !plan ? html`<${SkelRows} n=${5} cols=${[30, 14]} />`
        : plan.servers.map(s => html`<details class="plan-srv" open=${s.rows.length <= 8 && (s.changed > 0 || s.errors > 0 || s.skipped > 0)}>
            <summary><${Icon} n="chevron-right" cls="i-sm chev" />${s.server}
              <span class="counts">${s.errors ? html`<${Tag} kind="danger">${s.errors} error${s.errors > 1 ? "s" : ""}<//>` : ""}
                ${s.changed ? html`<${Tag} kind=${action === "delete" ? "danger" : "update"}>${plural(s.changed, "change")}<//>` : ""}
                ${s.skipped ? html`<${Tag} icon="minus">${s.skipped} skipped<//>` : ""}
                ${!s.changed && !s.skipped && !s.errors ? html`<${Tag} kind="ok" icon="check">no change<//>` : ""}</span></summary>
            <div class="plan-ops">
              ${!s.rows.length ? html`<div class="small muted">Nothing to do on this server.</div>`
                : s.rows.map(r => html`<${PlanRow} r=${r} action=${action} source=${body.source} install=${body.options.install} onInstall=${onInstall}
                    idKeys=${(plan.identity || []).filter(w => w.server === s.server && (w.path === r.item || w.path.startsWith(r.item.replace(/\/$/, "") + "/")))} identity=${identity} />`)}
            </div></details>`)}
    </div>
    <div class="panel-foot exec-bar" style="align-items:stretch">
      <span class="summary">${!plan ? "Execute unlocks when the plan has changes."
        : changes ? `${plural(changes, "change")} on ${plural(affected, "server")} · backed up for undo`
        : errors ? html`<span style="color:var(--warn)">Nothing can be pushed${identity === "keep" ? " with “Keep”" : ""} — ${plural(errors, "file")} can't be applied safely. ${identity === "keep" ? "Overwrite, skip them, or remove them." : "Remove them or change the plan."}</span>`
        : "Targets already match. Nothing to do."}</span>
      ${plan?.needsDecision && html`<span class="summary" style="color:var(--warn)">Choose how to handle server-specific values above.</span>`}
      <${Btn} kind=${action === "delete" ? "danger-solid" : errors ? "warn" : "primary"} size="lg" icon=${action === "delete" ? "trash-2" : "play"} disabled=${!plan || !changes || loading || plan.needsDecision} onClick=${() => onExecute(plan)}>
        ${action === "delete" ? "Delete…" : "Execute"}${plan && changes ? ` ${plural(changes, "change")}` : ""}${plan && changes && errors ? ` · skip ${errors} that would fail` : ""}<//>
    </div>
  </section>`;
}


function PlanRow({ r, action, source, install, onInstall, idKeys = [], identity }) {
  const [diff, setDiff] = useState(null); // null | "loading" | {lines} | Error
  // Rows that change server-specific keys open their diff straight away.
  const refused = r.reason_code === "merge_unsafe" && (r.outcome === "error" || r.skipped_by_choice);
  useEffect(() => { if (idKeys.length && r.outcome === "changed" && !diff && !r.item.endsWith("/")) loadDiff(); }, [idKeys.length]);
  // An open diff follows the Keep/Overwrite choice: with Keep it shows the merged file that would be written.
  useEffect(() => { if (diff && diff !== "loading") { setDiff(null); setTimeout(() => loadDiffRef.current?.(), 0); } }, [identity]);
  const loadDiffRef = useRef();
  const del = action === "delete" && r.outcome === "changed";
  const [i, cl] = del ? ["minus", "op-delete"] : OUT[r.outcome] || OUT.changed;
  const swap = r.outcome === "changed" && r.new_jar;
  const diffable = r.outcome === "changed" && !del && r.item.includes("/") && !/\/$/.test(r.item) && source;
  const loadDiff = async () => {
    if (diff && diff !== "loading") { setDiff(null); return; }
    setDiff("loading");
    try {
      const keep = (identity === "keep" || identity === "keep_skip") && !refused ? "&preserve_keys=server_specific" : "";
      const d = await get(`/diff?source=${encodeURIComponent(source)}&target=${encodeURIComponent(r.server)}&path=${encodeURIComponent(r.item)}${keep}`);
      setDiff(d);
    } catch (e) { setDiff(e); }
  };
  loadDiffRef.current = loadDiff;
  return html`<div class=${"plan-op-wrap" + (idKeys.length ? " has-identity" : "")}>
    ${idKeys.length > 0 && !refused && r.outcome !== "skipped" && html`<div class=${"id-chip" + (identity && identity !== "overwrite" ? " is-kept" : "")}><${Icon} n=${identity && identity !== "overwrite" ? "shield" : "triangle-alert"} cls="i-xs" />${identity === "overwrite" ? "Overwrites" : identity ? "Keeps" : "Changes"} ${(() => { const all = idKeys.flatMap(w => w.keys.map(k => `${k.key}: ${midTrunc(String(k.target_value), 18)}`)); return all.slice(0, 3).join(", ") + (all.length > 3 ? ` +${all.length - 3} more` : ""); })()}</div>`}

    <div class="plan-op"><${Icon} n=${i} cls=${"i-xs " + cl} />
      ${swap ? html`<div class="jar-swap">${(r.old_jars || []).map((j, k) => html`<span class="old">${j}${r.old_versions?.[k] ? ` (${r.old_versions[k]})` : ""}</span>`)}
          ${!(r.old_jars || []).length && html`<span class="small muted" style="font-family:var(--font-sans)">new install</span>`}
          <span class="new"><${Icon} n="arrow-right" cls="i-xs" />${r.new_jar}${r.new_version ? ` (${r.new_version})` : ""}</span></div>`
        : html`<span class="op-text"><span class=${r.outcome === "skipped" || r.outcome === "unchanged" ? "muted" : ""}>${r.item}</span>
          ${isDataFile(r.item) && html`<span class="tag tag-warn">data file</span>`}
          ${r.detail && !(refused && r.skipped_by_choice) && html`<span class=${"op-detail" + (r.outcome === "skipped" ? " is-skip" : r.outcome === "error" ? " is-err" : "")}>${r.outcome === "skipped" ? "Skipped: " : ""}${r.detail}</span>`}
          ${r.reason_code === "not_installed" && !install && action !== "delete" && html`<button type="button" class="linkbtn" onClick=${onInstall}>Also install where missing</button>`}
          ${diffable && html`<button type="button" class="linkbtn" aria-expanded=${diff && diff !== "loading" ? "true" : "false"} onClick=${loadDiff}>${diff && diff !== "loading" ? "Hide diff" : "Show diff"}</button>`}</span>`}
    </div>
    ${refused && html`<div class=${"refused small" + (r.skipped_by_choice ? " by-choice" : "")}><${Icon} n=${r.skipped_by_choice ? "minus" : "circle-x"} cls="i-xs" /><div><div>${r.skipped_by_choice ? "Skipped by choice — " : ""}Nothing will be written to this file on ${r.server}.</div>
      <button type="button" class="linkbtn" aria-expanded=${diff && diff !== "loading" ? "true" : "false"} onClick=${loadDiff}>${diff && diff !== "loading" ? "Hide" : "Show what Overwrite would do"}</button></div></div>`}
    ${r.delete_count > 0 && html`<${Deletions} r=${r} />`}
    ${diff && html`<${DiffView} d=${diff} />`}
  </div>`;
}

// A folder mirror deletes target files that aren't on the source: list them in red.
function Deletions({ r }) {
  const [open, setOpen] = useState(r.delete_count <= 5);
  return html`<div class="deletes">
    <button type="button" class="linkbtn del-toggle" aria-expanded=${open ? "true" : "false"} onClick=${() => setOpen(!open)}>
      <${Icon} n="minus" cls="i-xs" />${plural(r.delete_count, "file")} would be deleted${r.delete_bytes ? ` (${bytes(r.delete_bytes)})` : ""}</button>
    ${open && html`<ul>${(r.deletes || []).map(d => html`<li class="mono">− ${d}</li>`)}${r.delete_count > (r.deletes || []).length ? html`<li class="muted">… and ${r.delete_count - r.deletes.length} more</li>` : ""}</ul>`}
  </div>`;
}

function DiffView({ d }) {
  if (d === "loading") return html`<div class="diff"><${Skel} w="60%" /><${Skel} w="45%" /></div>`;
  if (d instanceof Error) return html`<div class="diff small" style="color:var(--danger)">Couldn't load diff: ${d.message}</div>`;
  if (d.binary) return html`<div class="diff small muted">Binary file — no text diff.</div>`;
  if (d.too_large) return html`<div class="diff small muted">File too large to diff.</div>`;
  const red = d.redacted || [];
  const redNote = red.length > 0 && html`<div class="diff-note small muted"><${Icon} n="shield" cls="i-xs" />${plural(red.length, "secret value")} hidden (${[...new Set(red.map(x => x.key.replace(/ \(\d+\)$/, "")))].join(", ")})${d.redacted_changed ? " — at least one differs" : ""}</div>`;
  const kept = d.kept_keys?.length > 0 && html`<div class="diff-note small"><${Icon} n="shield" cls="i-xs" style="color:var(--ok)" />Merged preview — keeps this server's ${d.kept_keys.join(", ")}</div>`;
  const mergeErr = d.merge_error && html`<div class="diff-note small" style="color:var(--danger)"><${Icon} n="triangle-alert" cls="i-xs" />Can't keep this server's values here (${d.merge_error}) — this file will be refused; diff shows the raw source.</div>`;
  if (d.identical) return html`${kept}<div class="diff small muted">${kept ? "Identical after merge" : "Identical"} — nothing would change.</div>`;
  if (!d.diff && d.redacted_changed) return html`<div class="diff small">Only redacted values differ (${red.filter(x => x.changed).map(x => x.key).join(", ")}).</div>`;
  if (!d.target_exists) return html`<div class="diff small muted">New file on this server.</div>`;
  const lines = (d.diff || "").split("\n").filter(l => !/^(---|\+\+\+) /.test(l));
  return html`${kept}${mergeErr}${redNote}<pre class="diff" aria-label=${`Diff of ${d.path}: this server becomes the source version`}>${lines.map(l => html`<span class=${l.startsWith("@@") ? "d-hunk" : l[0] === "+" ? "d-add" : l[0] === "-" ? "d-del" : ""}>${l || " "}</span>`)}</pre>`;
}

function SearchResults({ found, q, picked, toggle, open, blocked }) {
  if (found.error) return html`<div style="padding:8px"><${ErrorState} error=${found.error} retry=${found.reload} /></div>`;
  if (found.loading) return Array.from({ length: 6 }, (_, i) => html`<div class="tree-row"><${Skel} w="16px" h=${16} /><${Skel} w=${`${40 + (i * 13) % 45}%`} /></div>`);
  // Jars first: a search for "CoreProtect" offers the jar before its data folder.
  const order = { jar: 0, file: 1, dir: 2 };
  const rs = (found.data?.results || []).map(r => ({ ...r, kind: kindOf({ ...r, name: r.path }) })).sort((a, b) => order[a.kind] - order[b.kind]);
  if (!rs.length) return html`<div class="empty" style="padding:24px"><p>No files match “${q}” anywhere in plugins/.</p></div>`;
  return html`<div class="small muted" style="padding:4px 8px">${plural(rs.length, "match", "matches")} in all folders</div>
    ${rs.map(r => { const pk = picked(r.path, r.kind); const dir = r.path.includes("/") ? r.path.slice(0, r.path.lastIndexOf("/") + 1) : "";
      const off = blocked(r.kind);
      return html`<div class=${"tree-row" + (pk ? " is-picked" : "") + (off ? " is-blocked" : "")} title=${off ? "Replace jar only works on .jar files" : undefined} role="listitem" onClick=${ev => !ev.target.closest("button, input, label") && toggle(r.path, r.kind)}>
        <${Check} label=${`Pick ${r.path}`} checked=${pk} disabled=${off} onChange=${() => toggle(r.path, r.kind)} />
        <${Icon} n=${r.kind === "jar" ? "package" : r.kind === "dir" ? "folder" : "file-code"} cls=${"i-sm " + (r.kind === "jar" ? "ico-jar" : r.kind === "dir" ? "ico-dir" : "ico-file")} />
        <span class="name mono" title=${r.path}><span class="muted">${dir}</span>${r.path.slice(dir.length)}</span>
        ${r.kind === "file" && isDataFile(r.path) && html`<span class="tag tag-warn" title="Live server data (spawns, saves, per-player records), not configuration">data file</span>`}
        ${r.size != null && html`<span class="size">${bytes(r.size)}</span>`}
        ${r.kind === "dir" && html`<${Btn} size="sm" kind="ghost" icon="chevron-right" cls="open" aria-label=${`Open ${r.path}`} onClick=${() => open(r.path)} />`}
      </div>`; })}`;
}

// Long values (UUIDs, channel ids) are shown middle-truncated; click copies the full value.
function Val({ v }) {
  const s = String(v);
  if (s.length <= 24) return html`<span class="mono">${s}</span>`;
  return html`<button type="button" class="val-copy mono" title=${`${s} — click to copy`} onClick=${() => { navigator.clipboard?.writeText(s); toast({ kind: "ok", title: "Value copied" }); }}>${midTrunc(s, 20)}</button>`;
}

// Per-file block listing keys whose value is this server's own (identity, ports, DB names…).
function IdentityBlock({ warnings, choice, setChoice, unmergeable }) {
  const [editing, setEditing] = useState(false);
  // "server (hub, hungergames, skyblock, +4)" per key, so the kept values stay visible after choosing.
  const byKey = new Map();
  for (const w of warnings) for (const k of w.keys) (byKey.get(k.key) || byKey.set(k.key, []).get(k.key)).push(k.target_value);
  const keys = [...byKey];
  const named = keys.slice(0, 3).map(([k, vs]) => `${k} (${vs.slice(0, 3).map(v => midTrunc(String(v), 16)).join(", ")}${vs.length > 3 ? `, +${vs.length - 3}` : ""})`).join("; ")
    + (keys.length > 3 ? ` +${keys.length - 3} more keys` : "");
  if (choice && !editing) return html`<div class=${"identity is-chosen" + (choice === "overwrite" ? " is-overwrite" : "")} role="status">
    <${Icon} n=${choice === "overwrite" ? "triangle-alert" : "shield"} cls="i-sm" />
    <span class="grow small">${choice === "overwrite" ? `Overwriting with the source's values: ${named}` : `Keeping ${named}${choice === "keep_skip" ? " · skipping files that can't be merged" : ""}`}</span>
    <button type="button" class="linkbtn" onClick=${() => setEditing(true)}>Change</button></div>`;
  const files = new Map();
  for (const w of warnings) for (const k of w.keys) {
    const f = files.get(w.path) || files.set(w.path, new Map()).get(w.path);
    (f.get(k.key) || f.set(k.key, { source: k.source_value, targets: [] }).get(k.key)).targets.push([w.server, k.target_value, k.reason]);
  }
  const servers = new Set(warnings.map(w => w.server)).size;
  return html`<section class="identity" aria-labelledby="id-h" role="group">
    <h3 id="id-h"><${Icon} n="triangle-alert" cls="i-sm" />Server-specific values detected</h3>
    <p class="small">This push would change values that differ on each server (identity, ports, database names) on ${plural(servers, "server")}.</p>
    ${[...files].map(([path, keys]) => html`<div class="id-file"><div class="mono small id-path">${path}</div>
      <table class="id-tbl"><thead><tr><th scope="col">Key</th><th scope="col">This server now</th><th scope="col">Source value</th></tr></thead>
        <tbody>${[...keys].map(([key, v]) => v.targets.map(([srv, tv, reason], i) => html`<tr>
          ${i === 0 && html`<th scope="row" rowspan=${v.targets.length} class="mono">${key}</th>`}
          <td><span class="small muted">${srv}</span> <${Val} v=${tv} />
            ${reason === "target_only" && html`<div class="small id-why">not in the source file — overwriting drops it</div>`}
            ${reason === "complex" && html`<div class="small id-why">list or multi-line value</div>`}</td>
          ${i === 0 && html`<td rowspan=${v.targets.length}>${v.source == null ? "—" : html`<${Val} v=${v.source} />`}</td>`}</tr>`))}</tbody></table></div>`)}
    <div class="id-choice" role="radiogroup" aria-label="Server-specific values" aria-required="true">
      <label class="radio-card"><input type="radio" name="idchoice" checked=${choice === "keep"} onChange=${() => { setChoice("keep"); setEditing(false); }} />
        <div><b>Keep each server's own values <span class="tag tag-ok">recommended</span></b><span>Push everything else; these keys stay as they are on every target.</span></div></label>
      <label class="radio-card"><input type="radio" name="idchoice" checked=${choice === "overwrite"} onChange=${() => { setChoice("overwrite"); setEditing(false); }} />
        <div><b>Overwrite with source values</b><span>Every target gets the source's values above — only if you really want them identical.</span></div></label>
      ${unmergeable && html`<label class="radio-card"><input type="radio" name="idchoice" checked=${choice === "keep_skip"} onChange=${() => { setChoice("keep_skip"); setEditing(false); }} />
        <div><b>Keep, and skip files that can't be merged</b><span>Files whose values can't be kept safely (lists, multi-line values) are left untouched on those servers.</span></div></label>`}
    </div>
  </section>`;
}

function ReplaceBlocked({ nonJars, onRemove, onSync }) {
  return html`<section class="panel plan" aria-labelledby="rb-h">
    <div class="panel-head"><span class="step-n idle">–</span><h2 id="rb-h">Plan preview</h2></div>
    <div class="panel-body stack">
      <div class="shared-warn" role="alert"><${Icon} n="triangle-alert" cls="i-sm" />
        <div><b>Replace jar only works on .jar files</b>
          <p>${nonJars.join(", ")} ${nonJars.length === 1 ? "is" : "are"} not a jar. A folder would be mirrored with deletions, which can wipe plugin data such as a CoreProtect database.</p>
          <div class="row wrap" style="gap:8px"><${Btn} size="sm" icon="x" onClick=${onRemove}>Remove non-jar items<//><${Btn} size="sm" icon="refresh-cw" onClick=${onSync}>Switch to Sync instead<//></div></div>
      </div>
    </div>
    <div class="panel-foot exec-bar"><${Btn} kind="primary" size="lg" icon="play" disabled>Execute<//></div>
  </section>`;
}

function Execution({ job, jobId, onNew }) {
  const st = job?.status || "running";
  const run = isActive(st);
  const tone = run ? "run" : jobTone(job?.job || st);
  const res = job?.job?.results || [];
  return html`<section class="panel plan" aria-labelledby="ex-h">
    <div class="panel-head">${run ? html`<${Icon} n="loader-circle" cls="i-sm spin" style="color:var(--accent)" />` : tone === "ok" ? html`<${Icon} n="circle-check" cls="i-sm outcome-changed" />` : html`<${Icon} n="circle-x" cls="i-sm outcome-failed" />`}
      <h2 id="ex-h">${run ? "Executing…" : tone === "ok" ? "Deploy finished" : tone === "warn" ? "Finished with errors" : "Deploy failed"}</h2>
      <span class="spacer"></span><a class="btn btn-sm btn-ghost" href=${`#/activity/${jobId}`} title=${`Job ${jobId}`}>Details<${Icon} n="chevron-right" cls="i-xs" /></a></div>
    <div class=${"progress" + (run ? "" : tone === "ok" ? " done" : " fail")}></div>
    ${res.length > 0 && html`<div class="plan-body" style="flex:none;max-height:200px"><table class="tbl"><tbody>${res.map(r => html`<tr><td class="strong">${r.server}</td><td class="mono small ellipsis" style="max-width:160px">${r.item}</td><td class=${"small outcome-" + r.outcome}>${r.outcome}</td></tr>`)}</tbody></table></div>`}
    <${LogView} lines=${job?.lines || []} live=${run} />
    <div class="panel-foot">${!run && html`<${Btn} icon="plus" onClick=${onNew}>New deploy<//>`}<span class="grow"></span>
      ${!run && job?.job?.undoable && html`<a class="btn" href=${`#/activity/${jobId}`}><${Icon} n="undo-2" cls="i-sm" />Undo from Activity</a>`}</div>
  </section>`;
}
