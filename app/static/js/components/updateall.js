// "Update all" modal: preview every pending update, optionally dry-run first, then apply.
import { html, useState, useRef, useEffect } from "../lib.js";
import { useStore, setState, useQuery, toast } from "../store.js";
import { applyUpdates } from "../actions.js";
import { Icon, Btn, VerArrow, SkelRows, ErrorState, Empty, Check } from "./ui.js";
import { plural } from "../fmt.js";

export function UpdateAllHost() {
  const open = useStore(s => s.updateAll);
  return open ? html`<${UpdateAll} />` : null;
}

function UpdateAll() {
  const q = useQuery("/updates");
  const [dry, setDry] = useState(true);
  const [busy, setBusy] = useState(false);
  const ref = useRef();
  const close = () => setState({ updateAll: false });
  useEffect(() => {
    const prev = document.activeElement;
    ref.current?.querySelector("[data-autofocus]")?.focus();
    const k = (e) => e.key === "Escape" && close();
    document.addEventListener("keydown", k);
    return () => { document.removeEventListener("keydown", k); prev?.focus?.(); };
  }, []);
  const ups = q.data || [];
  const installs = ups.reduce((a, u) => a + u.servers.length, 0);
  const go = async () => {
    setBusy(true);
    close();
    const job = await applyUpdates("all", { dryRun: dry, title: dry ? "Dry run: update all" : "Update all" });
    if (dry && job && job.status === "done") toast({ kind: "info", title: "Dry run complete — nothing changed yet", body: job.summary, href: `#/activity/${job.id}`, sticky: true,
      action: { label: "Apply for real", run: () => applyUpdates("all", { title: "Update all" }) } });
  };
  return html`<div class="scrim" onClick=${close}></div>
  <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="ua-t" ref=${ref} style="width:min(560px, calc(100vw - 32px))">
    <div class="dialog-body">
      <h2 id="ua-t" style="gap:10px"><span class="ub-icon" style="color:var(--accent)"><${Icon} n="zap" /></span>Update everything</h2>
      ${q.error ? html`<${ErrorState} error=${q.error} retry=${q.reload} />`
        : q.loading ? html`<${SkelRows} n=${4} cols=${[30, 30, 20]} />`
        : !ups.length ? html`<${Empty} ok icon="circle-check" title="Nothing to update">Every tracked plugin is on its latest compatible version.<//>`
        : html`<p>${plural(ups.length, "plugin")} · ${plural(installs, "install")} across the network. Old jars are backed up and can be restored from Activity.</p>
          <div class="panel" style="max-height:280px;overflow:auto">
            ${ups.map(u => html`<div class="upd-row" style="grid-template-columns:minmax(0,1fr) auto;padding:8px 12px">
              <div class="ellipsis"><b style="font-weight:600">${u.name}</b> <span class="muted small">· ${plural(u.servers.length, "server")}</span></div>
              <${VerArrow} from=${u.from_versions} to=${u.to_version} /></div>`)}
          </div>
          <${Check} checked=${dry} onChange=${setDry}>Dry run first — show what would change without touching any server<//>`}
    </div>
    <div class="dialog-foot">
      <button class="btn" type="button" onClick=${close}>Cancel</button>
      <${Btn} kind="primary" icon=${dry ? "eye" : "zap"} busy=${busy} disabled=${!ups.length} onClick=${go} data-autofocus>${dry ? "Preview changes" : `Apply ${ups.length} updates`}<//>
    </div>
  </div>`;
}
