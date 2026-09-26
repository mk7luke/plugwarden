// Updates — the queue of available updates, plus the auto-update schedule and policy.
import { html, useState, useEffect, useMemo } from "../lib.js";
import { useQuery, invalidate, toast, confirmDialog } from "../store.js";
import { put } from "../api.js";
import { Icon, Btn, Tag, VerArrow, SkelRows, ErrorState, Empty, PageHead, Check, ServerChip, Skel } from "../components/ui.js";
import { checkUpdates, openUpdateAll } from "../actions.js";
import { openChangeset, CompatChip, Changelog } from "../components/changeset.js";
import { updateCounts, checkLine, updatesOf, compatOf } from "../summary.js";
import { CanaryStatus, HealthTag } from "../components/health.js";
import { relTime, absTime, plural, safeUrl } from "../fmt.js";

export function Updates() {
  const q = useQuery("/updates");
  const ov = useQuery("/overview");
  const [sel, setSel] = useState(new Set());
  const ups = updatesOf(q.data);
  const mcOf = Object.fromEntries((useQuery("/servers").data || []).map(s => [s.id, s.mc_version]));
  useEffect(() => setSel(s => new Set([...s].filter(k => ups.some(u => u.key === k)))), [q.data]);

  const all = ups.length > 0 && ups.every(u => sel.has(u.key));
  const chosen = ups.filter(u => sel.has(u.key));
  const installs = chosen.reduce((a, u) => a + u.servers.length, 0);
  const c = updateCounts(ov.data, q.data);
  const review = (list) => openChangeset(list.length === ups.length ? "all" : { keys: list.map(u => u.key) },
    list.length === 1 ? `Update ${list[0].name}` : `Review ${plural(list.length, "plugin")}`);

  return html`
    <${PageHead} title="Updates" sub=${ov.data ? `${checkLine(ov.data)} · Modrinth by file hash, then mapped Hangar / Spiget / GitHub sources` : " "}>
      <${Btn} kind="primary" icon="circle-arrow-up" disabled=${!ups.length} onClick=${openUpdateAll}>Review & update all<//>
    <//>
    <div class="upd-layout">
      <section class="panel" aria-labelledby="q-h">
        <div class="panel-head">
          ${ups.length > 0 && html`<${Check} label="Select all" checked=${all} indeterminate=${!all && sel.size > 0} onChange=${v => setSel(v ? new Set(ups.map(u => u.key)) : new Set())} />`}
          <h2 id="q-h">Available updates</h2>
          <span class="sub">${q.data ? `${plural(c.plugins, "plugin")} · ${plural(ups.reduce((a, u) => a + u.servers.length, 0), "install")} · ${plural(c.servers, "server")}` : ""}</span>
        </div>
        ${q.error ? html`<div class="panel-body"><${ErrorState} error=${q.error} retry=${q.reload} /></div>`
          : q.loading ? html`<${SkelRows} n=${6} cols=${[4, 28, 20, 30]} />`
          : !ups.length && !q.data?.last_check ? html`<${Empty} icon="circle-dashed" title="No update check yet" action=${html`<${Btn} kind="primary" icon="refresh-cw" onClick=${checkUpdates}>Run first check<//>`}>A check looks up every jar on Modrinth by file hash (and mapped Hangar, Spiget or GitHub sources). It never installs anything.<//>`
          : !ups.length ? html`<${Empty} ok icon="circle-check" title="All caught up" action=${html`<${Btn} size="sm" icon="refresh-cw" onClick=${checkUpdates}>Check again<//>`}>Every tracked plugin is on its latest compatible version for its server's Minecraft version.<//>`
          : html`<div>${ups.map(u => html`<div class="upd-group" key=${u.key}><div class="upd-row">
              <${Check} label=${`Select ${u.name}`} checked=${sel.has(u.key)} onChange=${v => setSel(s => { const n = new Set(s); v ? n.add(u.key) : n.delete(u.key); return n; })} />
              <div style="min-width:0">
                <div class="row wrap" style="gap:8px"><b style="font-weight:620">${u.name}</b>
                  ${safeUrl(u.changelog_url) && html`<a class="link small" href=${safeUrl(u.changelog_url)} target="_blank" rel="noopener">Changelog<span class="sr-only"> for ${u.name} (opens in new tab)</span></a>`}</div>
                <${VerArrow} from=${u.from_versions} to=${u.to_version} />
                ${u.canary_health && html`<div style="margin-top:4px" class="row wrap"><span class="small muted">Canary ${u.canary_health.server || ""}:</span><${HealthTag} h=${u.canary_health} /></div>`}
                ${u.source?.overrides_modrinth && html`<div style="margin-top:4px"><${Tag} kind="warn" icon="triangle-alert">manual source overrides Modrinth (${u.source.overrides_modrinth.name || u.source.overrides_modrinth.slug})<//></div>`}
                ${u.source?.manual && !u.source?.auto_apply && html`<div class="small muted" style="margin-top:2px">Manual source — never applied automatically</div>`}
                <${CompatSummary} u=${u} mcOf=${mcOf} />
                <${Changelog} r=${u} />
              </div>
              <${Chips} ids=${u.servers} />
              <${Btn} size="sm" data-nav onClick=${() => review([u])} aria-label=${`Review ${u.name} update on ${plural(u.servers.length, "server")}`}>Review<//>
            </div></div>`)}</div>`}
        ${chosen.length > 0 && html`<div class="panel-foot" style="position:sticky;bottom:0;background:var(--surface-2);flex-wrap:wrap">
          <b class="small">${plural(chosen.length, "plugin")} · ${plural(installs, "install")} selected</b><span class="grow"></span>
          <${Btn} kind="ghost" onClick=${() => setSel(new Set())}>Clear<//>
          <${Btn} kind="primary" icon="circle-arrow-up" onClick=${() => review(chosen)}>Review ${plural(chosen.length, "plugin")}<//>
        </div>`}
      </section>
      <${Policy} au=${ov.data?.auto_update} />
    </div>`;
}

// One chip when every target server is covered; otherwise name the servers that aren't.
function CompatSummary({ u, mcOf }) {
  const ts = (u.targets || []).filter(t => t.compat);
  if (!ts.length) return null;
  const bad = ts.filter(t => !compatOf(t.compat, mcOf[t.server]).ok);
  const mcs = [...new Set(ts.map(t => mcOf[t.server]).filter(Boolean))];
  if (!mcs.length) return null;
  return html`<div style="margin-top:4px">${bad.length
    ? html`<${Tag} kind="warn" icon="triangle-alert">not listed for MC ${[...new Set(bad.map(t => mcOf[t.server]))].join(", ")} (${bad.map(t => t.server).join(", ")})<//>`
    : html`<${Tag} kind="ok" icon="check">supports MC ${mcs.join(", ")}<//>`}</div>`;
}

// What the scheduler did last time, and why the rest is waiting.
function LastRun({ au }) {
  const ls = au?.last_selection;
  const canary = au?.canary || [];
  if (!ls && !canary.length && !(au?.held || []).length) return null;
  const waiting = ls?.waiting || [];
  const reasons = [...waiting.reduce((m, w) => m.set(w.reason, (m.get(w.reason) || 0) + 1), new Map())];
  return html`<div class="last-run small">
    <${CanaryStatus} au=${au} />
    ${ls && html`<p><b>Last automatic run ${relTime(ls.at)}:</b> ${plural((ls.applied || []).length, "update")} applied${waiting.length ? `, ${waiting.length} waiting` : ""}.</p>`}
    ${reasons.length > 0 && html`<ul>${reasons.map(([r, n]) => html`<li>${n} × ${r}</li>`)}</ul>`}
  </div>`;
}

function Chips({ ids, max = 4 }) {
  const [open, setOpen] = useState(false);
  const shown = open ? ids : ids.slice(0, max);
  return html`<div class="upd-servers" aria-label=${`${ids.length} servers`}>${shown.map(s => html`<${ServerChip} id=${s} />`)}
    ${ids.length > max && html`<button type="button" class="srv-chip more" aria-expanded=${open ? "true" : "false"} onClick=${() => setOpen(!open)}>${open ? "less" : `+${ids.length - max} more`}</button>`}</div>`;
}

const MODES = [
  ["off", "Off", "Never check automatically. Use “Check updates” in the top bar."],
  ["notify", "Check & notify", "Check on a schedule and list updates here. Nothing is installed."],
  ["apply", "Check & apply", "Install updates inside the maintenance window. Every change is backed up and undoable."],
];

export function Policy({ au }) {
  const next = au?.next_run;
  const servers = (useQuery("/servers").data || []).filter(x => x.plugin_count > 0 && x.family !== "velocity").map(x => x.id);
  const q = useQuery("/settings");
  const defSrc = q.data?.default_source;
  const [p, setP] = useState(null);
  const [saving, setSaving] = useState(false);
  const [maxRaw, setMaxRaw] = useState("");        // text in the limit field, validated before it becomes a number
  const [serverErr, setServerErr] = useState({});  // 422 field errors from the API, keyed by field name
  useEffect(() => { if (q.data) { setP({ ...q.data.auto_update }); setMaxRaw(q.data.auto_update.max_changes_per_run ? String(q.data.auto_update.max_changes_per_run) : ""); setServerErr({}); } }, [q.data]);
  const dirty = p && q.data && JSON.stringify(p) !== JSON.stringify(q.data.auto_update);
  // Client-side checks mirror the API: limit is a whole number 1–500 (or off = no limit); window is HH:MM-HH:MM.
  const limitOn = p?.max_changes_per_run != null;
  const errs = !p ? {} : {
    max_changes_per_run: limitOn && !/^\d+$/.test(maxRaw) ? "Enter a whole number from 1 to 500"
      : limitOn && (+maxRaw < 1 || +maxRaw > 500) ? "Enter a number from 1 to 500" : null,
    window: p.window && !/^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$/.test(p.window) ? "Use HH:MM-HH:MM, e.g. 04:00-06:00" : null,
  };
  const errOf = (k) => errs[k] || serverErr[k] || serverErr[`auto_update.${k}`];
  const invalid = Object.values(errs).some(Boolean);
  const FieldErr = ({ k }) => errOf(k) ? html`<span class="field-err" id=${`err-${k}`} role="alert">${errOf(k)}</span>` : null;
  const save = async () => {
    // Turning on automatic installs needs an explicit confirmation (the API requires confirm_apply).
    const enablingApply = p.mode === "apply" && q.data.auto_update.mode !== "apply";
    if (enablingApply) {
      const ok = await confirmDialog({
        title: "Install updates automatically?",
        body: `Updates will be installed without review inside the window${p.window ? ` (${p.window})` : ""}, starting with the canary server. Everything is backed up and can be undone.`,
        confirmLabel: "Turn on automatic updates",
      });
      if (!ok) return;
    }
    setSaving(true); setServerErr({});
    try { await put("/settings", { auto_update: { ...p, window: p.window || null, max_changes_per_run: limitOn ? +maxRaw : null }, ...(enablingApply ? { confirm_apply: true } : {}) }); toast({ kind: "ok", title: "Auto-update policy saved" }); invalidate("/settings", "/overview"); }
    catch (e) {
      if (e.fields) setServerErr(e.fields);
      else toast({ kind: "err", title: "Couldn't save policy", body: e.message });
    }
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
              ${au?.last_run && html`<span style="display:block;margin-top:4px">Last automatic run ${relTime(au.last_run)}</span>`}</div></div>
          <div class="policy-mode" role="radiogroup" aria-label="Mode">
            ${MODES.map(([k, l, d]) => html`<label class="radio-card"><input type="radio" name="aumode" checked=${p.mode === k} onChange=${() => setP({ ...p, mode: k })} /><div><b>${l}</b><span>${d}</span></div></label>`)}
          </div>
          <div class="row wrap" style="gap:12px;align-items:flex-end">
            <div class="field grow" style="min-width:120px"><label for="au-int">Check every</label>
              <select id="au-int" class="select" disabled=${p.mode === "off"} value=${p.interval_hours} onChange=${e => setP({ ...p, interval_hours: +e.currentTarget.value })}>
                ${[1, 3, 6, 12, 24, 48].map(h => html`<option value=${h}>${h === 1 ? "hour" : h < 24 ? `${h} hours` : h === 24 ? "day" : "2 days"}</option>`)}</select></div>
            <div class="field grow" style="min-width:120px"><label for="au-win">Apply window</label>
              <input id="au-win" class=${"input mono" + (errOf("window") ? " is-invalid" : "")} placeholder="04:00-06:00" disabled=${p.mode !== "apply"} value=${p.window || ""}
                aria-invalid=${errOf("window") ? "true" : undefined} aria-describedby=${errOf("window") ? "err-window" : undefined} onInput=${e => setP({ ...p, window: e.currentTarget.value })} /><${FieldErr} k="window" /></div>
          </div>
          <label class="switch"><input type="checkbox" checked=${!!p.dry_run_first} disabled=${p.mode !== "apply"} onChange=${e => setP({ ...p, dry_run_first: e.currentTarget.checked })} />Dry run first, apply only if it succeeds</label>
          <fieldset class="policy-safety" disabled=${p.mode === "off"}>
            <legend>Safety</legend>
            <label class="switch"><input type="checkbox" checked=${p.skip_prereleases !== false} onChange=${e => setP({ ...p, skip_prereleases: e.currentTarget.checked })} />Skip pre-releases (alpha, beta, snapshot)</label>
            <div class="row wrap" style="gap:12px;align-items:flex-start">
              <div class="field grow" style="min-width:130px"><label for="au-age">Minimum release age</label>
                <select id="au-age" class="select" value=${p.min_release_age_hours ?? 0} onChange=${e => setP({ ...p, min_release_age_hours: +e.currentTarget.value })}>
                  ${[[0, "No minimum"], [12, "12 hours"], [24, "1 day"], [48, "2 days"], [72, "3 days"], [168, "1 week"]].map(([v, l]) => html`<option value=${v}>${l}</option>`)}</select></div>
              <div class="field grow" style="min-width:170px"><span class="field-label">Changes per run</span>
                <div class="row" style="gap:8px;min-height:32px">
                  <label class="switch small"><input type="checkbox" checked=${limitOn} onChange=${e => { const on = e.currentTarget.checked; setP({ ...p, max_changes_per_run: on ? (+maxRaw || 20) : null }); if (on && !maxRaw) setMaxRaw("20"); }} />Limit</label>
                  ${limitOn ? html`<input id="au-max" class=${"input" + (errOf("max_changes_per_run") ? " is-invalid" : "")} style="width:90px" inputmode="numeric" aria-label="Maximum changes per run (1–500)"
                      aria-invalid=${errOf("max_changes_per_run") ? "true" : undefined} aria-describedby=${errOf("max_changes_per_run") ? "err-max_changes_per_run" : undefined}
                      value=${maxRaw} onInput=${e => { const v = e.currentTarget.value.trim(); setMaxRaw(v); setP({ ...p, max_changes_per_run: /^\d+$/.test(v) ? +v : p.max_changes_per_run ?? 0 }); }} />`
                    : html`<span class="small muted">No limit</span>`}
                </div><${FieldErr} k="max_changes_per_run" /></div>
            </div>
            <div class="row wrap" style="gap:12px;align-items:flex-end">
              <div class="field grow" style="min-width:150px"><label for="au-can">Canary server</label>
                <select id="au-can" class="select" disabled=${p.mode !== "apply"} value=${p.canary_server || ""} onChange=${e => setP({ ...p, canary_server: e.currentTarget.value || null })}>
                  <option value="">Default (${defSrc || "source"})</option>${servers.map(sv => html`<option value=${sv}>${sv}</option>`)}</select></div>
              <div class="field grow" style="min-width:120px"><label for="au-soak">Soak before the rest</label>
                <select id="au-soak" class="select" disabled=${p.mode !== "apply"} value=${p.canary_soak_hours ?? 24} onChange=${e => setP({ ...p, canary_soak_hours: +e.currentTarget.value })}>
                  ${[[1, "1 hour"], [2, "2 hours"], [6, "6 hours"], [12, "12 hours"], [24, "1 day"]].map(([v, l]) => html`<option value=${v}>${l}</option>`)}</select></div>
            </div>
            <p class="small muted">Automatic updates go to ${p.canary_server || defSrc || "the canary"} first. The rest follow only after ${p.canary_server || defSrc || "it"} restarts, its log shows each updated plugin enabling cleanly, and ${p.canary_soak_hours ?? 24} h pass. A plugin that fails to enable there is held back. Manual sources are only auto-applied when their mapping allows it.</p>
          </fieldset>
          <${LastRun} au=${au} />
          <p class="small muted">Pinned and ignored plugins are always skipped. Manage them per server or in <a class="link" href="#/settings/sources">Settings → Update sources</a>.</p>
        </div>`}
    </div>
    ${p && html`<div class="panel-foot"><span class=${"small grow " + (invalid ? "" : "muted")} style=${invalid ? "color:var(--danger)" : ""}>${invalid ? "Fix the highlighted field to save" : dirty ? "Unsaved changes" : "Saved"}</span>
      <${Btn} disabled=${!dirty} onClick=${() => { setP({ ...q.data.auto_update }); setMaxRaw(q.data.auto_update.max_changes_per_run ? String(q.data.auto_update.max_changes_per_run) : ""); setServerErr({}); }}>Reset<//><${Btn} kind="primary" busy=${saving} disabled=${!dirty || invalid} onClick=${save}>Save policy<//></div>`}
  </section>`;
}
