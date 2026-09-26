// Deploy composer — pick items from a source, pick targets and an action, review the
// automatic dry-run plan, then execute with a live log.
import { html, useState, useEffect, useMemo, useRef } from "../lib.js";
import { useQuery, useStore, confirmDialog, toast, setState } from "../store.js";
import { api, get, post } from "../api.js";
import { trackJob, isActive, jobTone } from "../jobs.js";
import { LogView } from "../components/overlays.js";
import { Icon, Btn, Tag, SkelRows, ErrorState, Empty, PageHead, Check, Skel } from "../components/ui.js";
import { bytes, plural } from "../fmt.js";

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
  const [opts, setOpts] = useState({ install: false, backup: true });
  const [jobId, setJobId] = useState(null);
  const [force, setForce] = useState(false);
  const [nonce, setNonce] = useState(0);       // bump to re-plan
  const [planInfo, setPlanInfo] = useState(null); // {changes, servers} for the mobile bar

  useEffect(() => { if (!source && settings.data) setSource(settings.data.default_source || servers.data?.[0]?.id); }, [settings.data, servers.data]);
  useEffect(() => {
    const list = (v) => (v || "").split(",").filter(Boolean);
    if (query.jar || query.folders || query.paths) setItems(i => ({ ...i, jars: list(query.jar), folders: list(query.folders), paths: list(query.paths) }));
    if (query.targets) setTargets(new Set(query.targets.split(",").filter(Boolean)));
    if (query.action) setAction(query.action);
  }, [query.jar, query.folders, query.paths, query.targets, query.action]);

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
  const sv = servers.data || [];
  const srcObj = sv.find(x => x.id === source);
  const tgt = [...targets].filter(t => t !== source && sv.some(x => x.id === t && !ineligible(x, srcObj)));
  const body = useMemo(() => ({
    source, targets: tgt, action,
    items: { jars: items.jars, folders: items.folders, paths: items.paths, uploads: items.uploads.map(u => u.upload_id) },
    options: opts, ...(force && action === "delete" ? { force: true } : {}),
  }), [source, tgt.join(","), action, items, opts, force]);

  const running = useStore(s => s.jobs.find(j => j.id === jobId));
  useEffect(() => { setState({ inlineJob: jobId }); return () => setState({ inlineJob: null }); }, [jobId]);

  const reset = () => { setItems(emptyItems()); setJobId(null); };
  const execute = async (plan) => {
    const changes = plan.servers.reduce((a, s) => a + s.changed, 0);
    const affected = plan.servers.filter(s => s.changed);
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
      const r = await post("/deploy", { plan_id: plan.plan_id });
      setJobId(r.job_id);
      trackJob(r.job_id, { title: `Deploy · ${ACTIONS.find(a => a[0] === action)[1]}` });
    } catch (e) {
      toast({ kind: "err", title: e.status === 409 ? "Targets changed since this preview" : "Deploy didn't start", body: e.status === 409 ? "The plan was refreshed — review it and execute again." : e.message });
      if (e.status === 409) setNonce(n => n + 1);
    }
  };

  return html`
    <${PageHead} title="Deploy" sub="Push configs, roll out jars, or remove plugins — with a dry-run plan before anything is touched." >
      ${(count > 0 || targets.size > 0) && html`<${Btn} kind="ghost" icon="x" onClick=${() => { reset(); setTargets(new Set()); history.replaceState(null, "", "#/deploy"); }}>Clear<//>`}
    <//>
    <div class="composer">
      <${SourceCol} source=${source} setSource=${(s) => { setSource(s); setItems(emptyItems()); }} servers=${sv} items=${items} setItems=${setItems} setAction=${setAction} autoUpload=${query.upload} initialFilter=${query.q} />
      <${TargetCol} source=${source} servers=${sv} groups=${settings.data?.groups || {}} targets=${targets} setTargets=${setTargets} action=${action} setAction=${setAction} opts=${opts} setOpts=${setOpts} />
      <div class="plan-col" id="plan">
        ${jobId ? html`<${Execution} job=${running} jobId=${jobId} onNew=${reset} />`
          : html`<${PlanCol} body=${body} nonce=${nonce} ready=${count > 0 && tgt.length > 0} count=${count} targets=${tgt.length} onExecute=${execute} action=${action}
              force=${force} setForce=${setForce} onInstall=${() => setOpts(o => ({ ...o, install: true }))} onSummary=${setPlanInfo} />`}
      </div>
    </div>
    ${!jobId && (count > 0 || tgt.length > 0) && html`<div class="plan-bar only-sm" role="region" aria-label="Plan summary">
      <span class="small"><b>${plural(count, "item")}</b> · ${plural(tgt.length, "server")}${planInfo ? html` · <b>${plural(planInfo.changes, "change")}</b>` : ""}</span>
      <${Btn} kind="primary" size="sm" onClick=${() => document.getElementById("plan")?.scrollIntoView({ behavior: "smooth", block: "start" })}>Review plan<//>
    </div>`}`;
}

// ---------- column 1: source browser ----------
function SourceCol({ source, setSource, servers, items, setItems, setAction, autoUpload, initialFilter }) {
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
  const found = useQuery(source && q ? `/servers/${encodeURIComponent(source)}/search?q=${encodeURIComponent(q)}` : null);

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
  const toggle = (e) => toggleRel(full(e.name), e.kind);
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
      <nav class="path-bar" aria-label="Folder path" hidden=${!!q}>
        <button type="button" onClick=${() => setPath("")}>plugins</button>
        ${crumbs.map((c, i) => html`<span aria-hidden="true">/</span><button type="button" onClick=${() => setPath(crumbs.slice(0, i + 1).join("/"))}>${c}</button>`)}
      </nav>
    </div>
    <div class="tree" role="list" aria-label=${q ? `Search results for ${q}` : "Source files"}>
      ${q ? html`<${SearchResults} found=${found} q=${q} picked=${pickedRel} toggle=${toggleRel} open=${(p) => { setPath(p); setFilter(""); }} />`
        : tree.error ? html`<div style="padding:8px"><${ErrorState} error=${tree.error} retry=${tree.reload} /></div>`
        : tree.loading ? Array.from({ length: 10 }, (_, i) => html`<div class="tree-row"><${Skel} w="16px" h=${16} /><${Skel} w=${`${40 + (i * 13) % 45}%`} /></div>`)
        : !entries.length ? html`<div class="empty" style="padding:24px"><p>${filter ? `Nothing matches “${filter}”.` : "This folder is empty."}</p></div>`
        : entries.map(e => {
          const pk = isPicked(e);
          const ico = e.kind === "jar" ? ["package", "ico-jar"] : e.kind === "dir" ? ["folder", "ico-dir"] : [/\.(ya?ml|json|toml|conf|properties)$/.test(e.name) ? "file-code" : "file-text", "ico-file"];
          return html`<div class=${"tree-row" + (pk ? " is-picked" : "")} role="listitem" onClick=${ev => !ev.target.closest("button, input, label") && toggle(e)} onDblClick=${() => e.kind === "dir" && (setPath(full(e.name)), setFilter(""))}>
            <${Check} label=${`Pick ${e.name}`} checked=${pk} onChange=${() => toggle(e)} />
            <${Icon} n=${ico[0]} cls=${"i-sm " + ico[1]} />
            <span class=${"name" + (e.kind !== "dir" ? " mono" : "") + (e.name.startsWith(".") ? " muted" : "")} title=${e.name}>${e.name}</span>
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
function TargetCol({ source, servers, groups, targets, setTargets, action, setAction, opts, setOpts }) {
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

function PlanCol({ body, nonce, ready, count, targets, onExecute, action, force, setForce, onInstall, onSummary }) {
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
    ${errors > 0 && html`<div class="plan-warn"><${Icon} n="triangle-alert" cls="i-sm" />${plural(errors, "item")} would fail — see details below.</div>`}
    <div class="plan-body">
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
                : s.rows.map(r => html`<${PlanRow} r=${r} action=${action} source=${body.source} install=${body.options.install} onInstall=${onInstall} />`)}
            </div></details>`)}
    </div>
    <div class="panel-foot exec-bar" style="align-items:stretch">
      <span class="summary">${plan ? (changes ? `${plural(changes, "change")} on ${plural(affected, "server")} · backed up for undo` : "Targets already match. Nothing to do.") : "Execute unlocks when the plan has changes."}</span>
      <${Btn} kind=${action === "delete" ? "danger-solid" : "primary"} size="lg" icon=${action === "delete" ? "trash-2" : "play"} disabled=${!plan || !changes || loading} onClick=${() => onExecute(plan)}>
        ${action === "delete" ? "Delete…" : "Execute"}${plan && changes ? ` ${plural(changes, "change")}` : ""}<//>
    </div>
  </section>`;
}

// "Old.jar (1.0) → New.jar (2.0)" as the API writes it for replacements.
const SWAP = /^(.+?)(?: \(([^)]*)\))? → (.+?)(?: \(([^)]*)\))?$/;

function PlanRow({ r, action, source, install, onInstall }) {
  const [diff, setDiff] = useState(null); // null | "loading" | {lines} | Error
  const del = action === "delete" && r.outcome === "changed";
  const [i, cl] = del ? ["minus", "op-delete"] : OUT[r.outcome] || OUT.changed;
  const swap = r.outcome === "changed" && SWAP.exec(r.detail || "");
  const diffable = r.outcome === "changed" && !del && r.item.includes("/") && !/\/$/.test(r.item) && source;
  const loadDiff = async () => {
    if (diff && diff !== "loading") { setDiff(null); return; }
    setDiff("loading");
    try {
      const d = await get(`/diff?source=${encodeURIComponent(source)}&target=${encodeURIComponent(r.server)}&path=${encodeURIComponent(r.item)}`);
      setDiff(d);
    } catch (e) { setDiff(e); }
  };
  return html`<div class="plan-op-wrap">
    <div class="plan-op"><${Icon} n=${i} cls=${"i-xs " + cl} />
      ${swap ? html`<div class="jar-swap"><span class="old">${swap[1]}${swap[2] ? ` (${swap[2]})` : ""}</span><span class="new"><${Icon} n="arrow-right" cls="i-xs" />${swap[3]}${swap[4] ? ` (${swap[4]})` : ""}</span></div>`
        : html`<span class="op-text"><span class=${r.outcome === "skipped" || r.outcome === "unchanged" ? "muted" : ""}>${r.item}</span>
          ${r.detail && html`<span class=${"op-detail" + (r.outcome === "skipped" ? " is-skip" : r.outcome === "error" ? " is-err" : "")}>${r.outcome === "skipped" ? "Skipped: " : ""}${r.detail}</span>`}
          ${r.outcome === "skipped" && /not installed/i.test(r.detail || "") && !install && action !== "delete" && html`<button type="button" class="linkbtn" onClick=${onInstall}>Also install where missing</button>`}
          ${diffable && html`<button type="button" class="linkbtn" aria-expanded=${diff && diff !== "loading" ? "true" : "false"} onClick=${loadDiff}>${diff && diff !== "loading" ? "Hide diff" : "Show diff"}</button>`}</span>`}
    </div>
    ${diff && html`<${DiffView} d=${diff} />`}
  </div>`;
}

function DiffView({ d }) {
  if (d === "loading") return html`<div class="diff"><${Skel} w="60%" /><${Skel} w="45%" /></div>`;
  if (d instanceof Error) return html`<div class="diff small" style="color:var(--danger)">Couldn't load diff: ${d.message}</div>`;
  if (d.binary) return html`<div class="diff small muted">Binary file — no text diff.</div>`;
  if (d.too_large) return html`<div class="diff small muted">File too large to diff.</div>`;
  if (d.identical) return html`<div class="diff small muted">Identical — nothing would change.</div>`;
  if (!d.target_exists) return html`<div class="diff small muted">New file on this server.</div>`;
  const lines = (d.diff || "").split("\n").filter(l => !/^(---|\+\+\+) /.test(l));
  return html`<pre class="diff" aria-label=${`Diff of ${d.path}: this server becomes the source version`}>${lines.map(l => html`<span class=${l.startsWith("@@") ? "d-hunk" : l[0] === "+" ? "d-add" : l[0] === "-" ? "d-del" : ""}>${l || " "}</span>`)}</pre>`;
}

function SearchResults({ found, q, picked, toggle, open }) {
  if (found.error) return html`<div style="padding:8px"><${ErrorState} error=${found.error} retry=${found.reload} /></div>`;
  if (found.loading) return Array.from({ length: 6 }, (_, i) => html`<div class="tree-row"><${Skel} w="16px" h=${16} /><${Skel} w=${`${40 + (i * 13) % 45}%`} /></div>`);
  const rs = (found.data?.results || []).map(r => ({ ...r, kind: kindOf({ ...r, name: r.path }) }));
  if (!rs.length) return html`<div class="empty" style="padding:24px"><p>No files match “${q}” anywhere in plugins/.</p></div>`;
  return html`<div class="small muted" style="padding:4px 8px">${plural(rs.length, "match", "matches")} in all folders</div>
    ${rs.map(r => { const pk = picked(r.path, r.kind); const dir = r.path.includes("/") ? r.path.slice(0, r.path.lastIndexOf("/") + 1) : "";
      return html`<div class=${"tree-row" + (pk ? " is-picked" : "")} role="listitem" onClick=${ev => !ev.target.closest("button, input, label") && toggle(r.path, r.kind)}>
        <${Check} label=${`Pick ${r.path}`} checked=${pk} onChange=${() => toggle(r.path, r.kind)} />
        <${Icon} n=${r.kind === "jar" ? "package" : r.kind === "dir" ? "folder" : "file-code"} cls=${"i-sm " + (r.kind === "jar" ? "ico-jar" : r.kind === "dir" ? "ico-dir" : "ico-file")} />
        <span class="name mono" title=${r.path}><span class="muted">${dir}</span>${r.path.slice(dir.length)}</span>
        ${r.size != null && html`<span class="size">${bytes(r.size)}</span>`}
        ${r.kind === "dir" && html`<${Btn} size="sm" kind="ghost" icon="chevron-right" cls="open" aria-label=${`Open ${r.path}`} onClick=${() => open(r.path)} />`}
      </div>`; })}`;
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
