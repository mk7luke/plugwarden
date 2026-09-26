// Updates — the queue of available updates, plus the auto-update schedule and policy.
import { html, useState, useEffect, useMemo } from "../lib.js";
import { useQuery, invalidate, toast } from "../store.js";
import { put } from "../api.js";
import { Icon, Btn, VerArrow, SkelRows, ErrorState, Empty, PageHead, Check, ServerChip, Skel } from "../components/ui.js";
import { applyUpdates, checkUpdates, openUpdateAll } from "../actions.js";
import { relTime, absTime, plural } from "../fmt.js";

export function Updates() {
  const q = useQuery("/updates");
  const ov = useQuery("/overview");
  const [sel, setSel] = useState(new Set());
  const [dry, setDry] = useState(false);
  const ups = q.data || [];
  useEffect(() => setSel(s => new Set([...s].filter(k => ups.some(u => u.key === k)))), [q.data]);

  const all = ups.length > 0 && ups.every(u => sel.has(u.key));
  const chosen = ups.filter(u => sel.has(u.key));
  const installs = chosen.reduce((a, u) => a + u.servers.length, 0);
  const apply = async (list) => {
    await applyUpdates(list.map(u => ({ key: u.key, servers: u.servers })), { dryRun: dry, title: dry ? `Dry run: ${plural(list.length, "update")}` : `Apply ${plural(list.length, "update")}` });
    if (!dry) setSel(new Set());
  };

  return html`
    <${PageHead} title="Updates" sub=${ov.data ? `Last checked ${relTime(ov.data.last_check)} · Modrinth by hash, then Hangar / Spiget / GitHub for mapped plugins` : " "}>
      <${Btn} icon="refresh-cw" onClick=${checkUpdates}>Check now<//>
      <${Btn} kind="primary" icon="zap" disabled=${!ups.length} onClick=${openUpdateAll}>Update all<//>
    <//>
    <div class="upd-layout">
      <section class="panel" aria-labelledby="q-h">
        <div class="panel-head">
          ${ups.length > 0 && html`<${Check} label="Select all" checked=${all} indeterminate=${!all && sel.size > 0} onChange=${v => setSel(v ? new Set(ups.map(u => u.key)) : new Set())} />`}
          <h2 id="q-h">Available updates</h2>
          <span class="sub">${q.data ? `${plural(ups.length, "plugin")} · ${plural(ups.reduce((a, u) => a + u.servers.length, 0), "install")}` : ""}</span>
        </div>
        ${q.error ? html`<div class="panel-body"><${ErrorState} error=${q.error} retry=${q.reload} /></div>`
          : q.loading ? html`<${SkelRows} n=${6} cols=${[4, 28, 20, 30]} />`
          : !ups.length ? html`<${Empty} ok icon="circle-check" title="All caught up" action=${html`<${Btn} size="sm" icon="refresh-cw" onClick=${checkUpdates}>Check again<//>`}>Every tracked plugin is on its latest compatible version for its server's Minecraft version.<//>`
          : html`<div>${ups.map(u => html`<div class="upd-group" key=${u.key}><div class="upd-row">
              <${Check} label=${`Select ${u.name}`} checked=${sel.has(u.key)} onChange=${v => setSel(s => { const n = new Set(s); v ? n.add(u.key) : n.delete(u.key); return n; })} />
              <div style="min-width:0">
                <div class="row" style="gap:8px"><b style="font-weight:620">${u.name}</b>
                  ${u.changelog_url && html`<a class="link small row" style="gap:3px" href=${u.changelog_url} target="_blank" rel="noopener">Changelog<${Icon} n="external-link" cls="i-xs" /></a>`}</div>
                <${VerArrow} from=${u.from_versions} to=${u.to_version} />
              </div>
              <${Chips} ids=${u.servers} />
              <${Btn} size="sm" onClick=${() => apply([u])} aria-label=${`Update ${u.name} on ${u.servers.length} servers`}>Update<//>
            </div></div>`)}</div>`}
        ${chosen.length > 0 && html`<div class="panel-foot" style="position:sticky;bottom:0;background:var(--surface-2);flex-wrap:wrap">
          <b class="small">${plural(chosen.length, "plugin")} · ${plural(installs, "install")}</b><span class="grow"></span>
          <label class="switch small"><input type="checkbox" checked=${dry} onChange=${e => setDry(e.currentTarget.checked)} />Dry run</label>
          <${Btn} kind="primary" icon=${dry ? "eye" : "circle-arrow-up"} onClick=${() => apply(chosen)}>${dry ? "Preview" : "Apply selected"}<//>
        </div>`}
      </section>
      <${Policy} au=${ov.data?.auto_update} />
    </div>`;
}

function Chips({ ids, max = 4 }) {
  const [open, setOpen] = useState(false);
  const shown = open ? ids : ids.slice(0, max);
  return html`<div class="upd-servers" aria-label=${`${ids.length} servers`}>${shown.map(s => html`<${ServerChip} id=${s} />`)}
    ${ids.length > max && html`<button type="button" class="srv-chip more" aria-expanded=${open ? "true" : "false"} onClick=${() => setOpen(!open)}>${open ? "less" : `+${ids.length - max} more`}</button>`}</div>`;
}

const MODES = [
  ["off", "Off", "Never check automatically. Use “Check now”."],
  ["notify", "Check & notify", "Check on a schedule and list updates here. Nothing is installed."],
  ["apply", "Check & apply", "Install updates inside the maintenance window. Every change is backed up and undoable."],
];

export function Policy({ au }) {
  const next = au?.next_run;
  const q = useQuery("/settings");
  const [p, setP] = useState(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (q.data) setP({ ...q.data.auto_update }); }, [q.data]);
  const dirty = p && q.data && JSON.stringify(p) !== JSON.stringify(q.data.auto_update);
  const save = async () => {
    setSaving(true);
    try { await put("/settings", { auto_update: { ...p, window: p.window || null } }); toast({ kind: "ok", title: "Auto-update policy saved" }); invalidate("/settings", "/overview"); }
    catch (e) { toast({ kind: "err", title: "Couldn't save policy", body: e.message }); }
    setSaving(false);
  };
  return html`<section class="panel" aria-labelledby="pol-h">
    <div class="panel-head"><${Icon} n="calendar-clock" cls="i-sm" /><h2 id="pol-h">Auto-update policy</h2></div>
    <div class="panel-body">
      ${q.error ? html`<${ErrorState} error=${q.error} retry=${q.reload} />`
        : !p ? html`<div class="stack"><${Skel} h=${52} /><${Skel} h=${52} /><${Skel} h=${52} /></div>`
        : html`<div class="policy">
          <div class="next-run"><${Icon} n="clock" cls="i-lg" />
            <div><b>${p.mode === "off" ? "No automatic runs" : next ? `Next run ${relTime(next)}` : "Scheduled after saving"}</b>${p.mode !== "off" && next ? absTime(next) : p.mode === "off" ? "Pick a mode below to schedule checks." : ""}
              ${au?.last_run && html`<span style="display:block;margin-top:4px">Last run ${relTime(au.last_run)}${au.last_result ? ` — ${au.last_result}` : ""}</span>`}</div></div>
          <div class="policy-mode" role="radiogroup" aria-label="Mode">
            ${MODES.map(([k, l, d]) => html`<label class="radio-card"><input type="radio" name="aumode" checked=${p.mode === k} onChange=${() => setP({ ...p, mode: k })} /><div><b>${l}</b><span>${d}</span></div></label>`)}
          </div>
          <div class="row wrap" style="gap:12px;align-items:flex-end">
            <div class="field grow" style="min-width:120px"><label for="au-int">Check every</label>
              <select id="au-int" class="select" disabled=${p.mode === "off"} value=${p.interval_hours} onChange=${e => setP({ ...p, interval_hours: +e.currentTarget.value })}>
                ${[1, 3, 6, 12, 24, 48].map(h => html`<option value=${h}>${h === 1 ? "hour" : h < 24 ? `${h} hours` : h === 24 ? "day" : "2 days"}</option>`)}</select></div>
            <div class="field grow" style="min-width:120px"><label for="au-win">Apply window</label>
              <input id="au-win" class="input mono" placeholder="04:00-06:00" disabled=${p.mode !== "apply"} value=${p.window || ""} onInput=${e => setP({ ...p, window: e.currentTarget.value })} /></div>
          </div>
          <label class="switch"><input type="checkbox" checked=${!!p.dry_run_first} disabled=${p.mode !== "apply"} onChange=${e => setP({ ...p, dry_run_first: e.currentTarget.checked })} />Dry run first, apply only if it succeeds</label>
          <p class="small muted">Pinned and ignored plugins are always skipped. Manage them per server or in <a class="link" href="#/settings/sources">Settings → Update sources</a>.</p>
        </div>`}
    </div>
    ${p && html`<div class="panel-foot"><span class="small muted grow">${dirty ? "Unsaved changes" : "Saved"}</span>
      <${Btn} disabled=${!dirty} onClick=${() => setP({ ...q.data.auto_update })}>Reset<//><${Btn} kind="primary" busy=${saving} disabled=${!dirty} onClick=${save}>Save policy<//></div>`}
  </section>`;
}
