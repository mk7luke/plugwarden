// "Remove plugin" dialog for the matrix. Optional config-folder deletion is previewed with a
// synchronous delete plan (file counts, sizes, shared-folder warnings). If other installed plugins
// share the folder, the user must acknowledge it and the removal is sent with force:true.
import { html, useState, useEffect, useRef } from "../lib.js";
import { useStore, setState } from "../store.js";
import { get, post } from "../api.js";
import { runJob } from "../jobs.js";
import { Icon, Btn, Skel, trapTab, restoreFocus } from "./ui.js";
import { plural, bytes } from "../fmt.js";

export const openRemove = (row) => setState({ removePlugin: row });

export function RemoveHost() {
  const row = useStore(s => s.removePlugin);
  return row ? html`<${RemoveDialog} key=${row.key} row=${row} />` : null;
}

function RemoveDialog({ row }) {
  const where = Object.entries(row.cells);
  const servers = where.map(([s]) => s);
  const [withFolder, setWithFolder] = useState(false);
  const [folder, setFolder] = useState(null);
  const [plan, setPlan] = useState(null);      // {rows:[{server, files, size, outcome}], shared:{server:[names]}, warnings}
  const [planErr, setPlanErr] = useState(null);
  const [ack, setAck] = useState(false);
  const [typed, setTyped] = useState("");
  const ref = useRef();
  const close = () => setState({ removePlugin: null });

  useEffect(() => {
    const prev = document.activeElement;
    ref.current?.querySelector("[data-autofocus]")?.focus();
    const k = (e) => { if (e.key === "Escape") close(); trapTab(e, ref.current); };
    document.addEventListener("keydown", k);
    return () => { document.removeEventListener("keydown", k); restoreFocus(prev); };
  }, []);

  // Resolve the plugin's data folder name from one server's plugin list.
  useEffect(() => {
    get(`/servers/${encodeURIComponent(servers[0])}/plugins`)
      .then(ps => setFolder(ps.find(p => p.key === row.key)?.folder || row.name))
      .catch(() => setFolder(row.name));
  }, [row.key]);

  useEffect(() => {
    if (!withFolder || !folder || plan) return;
    post("/deploy/plan", { action: "delete", targets: servers, items: { folders: [folder] } }).then(p => {
      // The plan counts the folder's owner as a sharer; for a removal only *other* plugins matter.
      const others = (names) => (names || []).filter(n => n !== row.name);
      const rows = (p.results || []).map(r => ({ server: r.server, outcome: r.outcome, size: r.size,
        files: r.files || 0 }));
      const shared = {};
      for (const w of p.warnings || []) { const n = others(w.shared_with); if (n.length) shared[w.server] = n; }
      setPlan({ rows, shared });
    }, setPlanErr);
  }, [withFolder, folder]);

  const present = plan?.rows.filter(r => r.files > 0 || r.outcome === "changed") || [];
  const files = present.reduce((a, r) => a + r.files, 0);
  const size = present.some(r => r.size != null) ? present.reduce((a, r) => a + (r.size || 0), 0) : null;
  const sharedServers = plan ? Object.keys(plan.shared) : [];
  const sharedNames = [...new Set(sharedServers.flatMap(s => plan.shared[s]))];
  const needAck = withFolder && sharedServers.length > 0;
  const needType = servers.length > 1;
  const ready = (!withFolder || plan) && (!needAck || ack) && (!needType || typed.trim() === row.name);

  const go = (e) => {
    e.preventDefault();
    if (!ready) return;
    close();
    runJob(post(`/plugins/${encodeURIComponent(row.key)}/remove`, { servers, remove_folder: withFolder, ...(needAck ? { force: true } : {}) }),
      { title: `Remove ${row.name}` });
  };

  return html`<div class="scrim" onClick=${close}></div>
  <div class="dialog" role="alertdialog" aria-modal="true" aria-labelledby="rm-t" ref=${ref} style="width:min(520px, calc(100vw - 32px))">
    <form onSubmit=${go}>
      <div class="dialog-body">
        <h2 id="rm-t"><${Icon} n="triangle-alert" />Remove ${row.name}?</h2>
        <p>The jar will be removed from ${plural(servers.length, "server")}. Everything is backed up first and can be restored from Activity.</p>
        <div class="confirm-list">${where.map(([s, c]) => html`<div>${s}: ${c.jar}</div>`)}</div>
        <label class="check"><input type="checkbox" checked=${withFolder} onChange=${e => { setWithFolder(e.currentTarget.checked); setAck(false); }} />
          Also delete its config folder${folder ? html` <span class="mono">${folder}/</span>` : ""}</label>
        ${withFolder && html`<div class="folder-impact" aria-live="polite">
          ${planErr ? html`<p class="small" style="color:var(--danger)">Couldn't preview the folder: ${planErr.message}</p>`
            : !plan ? html`<${Skel} w="70%" /><${Skel} w="50%" />`
            : html`<p class="small"><b>${present.length ? `${plural(files, "file")}${size != null ? ` (${bytes(size)})` : ""} in ${plural(present.length, "folder")}` : "No folder found on these servers"}</b>
                ${present.length > 0 && html`<span class="muted">: ${present.map(r => `${r.server} (${r.files})`).join(" · ")}</span>`}</p>
              ${needAck && html`<div class="shared-warn" role="alert">
                <${Icon} n="triangle-alert" cls="i-sm" />
                <div><b>Shared with ${sharedNames.join(", ")}</b>
                  <p>Still installed on ${plural(sharedServers.length, "server")} and use <span class="mono">${folder}/</span> for data (player files, warps, kits…). They stay installed and lose that data.</p>
                  <label class="check"><input type="checkbox" checked=${ack} onChange=${e => setAck(e.currentTarget.checked)} />I understand shared data will be deleted</label></div>
              </div>`}`}
        </div>`}
        ${needType && html`<div class="field"><label for="rm-in">Type <b class="mono">${row.name}</b> to confirm</label>
          <input id="rm-in" data-autofocus class="input mono" autocomplete="off" spellcheck="false" value=${typed} onInput=${e => setTyped(e.currentTarget.value)} /></div>`}
      </div>
      <div class="dialog-foot">
        <button type="button" class="btn" onClick=${close} data-autofocus=${needType ? undefined : true}>Cancel</button>
        <button type="submit" class="btn btn-danger-solid" disabled=${!ready}>Remove from ${plural(servers.length, "server")}</button>
      </div>
    </form>
  </div>`;
}
