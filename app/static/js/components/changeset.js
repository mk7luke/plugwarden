// Review Changeset sheet — the one update flow. Every "Update" entry point opens this sheet with a
// scope; it builds an exact plan (POST /updates/plan), lets the user exclude rows, applies that
// plan_id, then shows per-row results and a restart checklist.
import { html, useState, useEffect, useRef, useMemo } from "../lib.js";
import { useStore, setState, getState, invalidate, toast, confirmDialog } from "../store.js";
import { get, post } from "../api.js";
import { updatesOf, compatOf } from "../summary.js";
import { trackJob, isActive, jobTone } from "../jobs.js";
import { LogView } from "./overlays.js";
import { Icon, Btn, Tag, Skel, ErrorState, Empty, Check } from "./ui.js";
import { plural, bytes } from "../fmt.js";

// scope: "all" | {server} | {keys:[key]} | {items:[{key, servers?}]} — translated to the API's {items}.
export const openChangeset = (scope, title) => setState({ changeset: { scope, title: title || "Review updates", n: Date.now() } });

export function ChangesetHost() {
  const cs = useStore(s => s.changeset);
  return cs ? html`<${Changeset} key=${cs.n} cs=${cs} />` : null;
}

export function CompatChip({ c }) {
  if (!c) return null;
  if (!c.mc) return null;
  const range = c.supported?.length > 1 ? `${c.supported[0]}–${c.supported[c.supported.length - 1]}` : c.supported?.[0];
  return html`<span class="row" style="gap:4px;flex-wrap:wrap">
    ${c.ok ? html`<${Tag} kind="ok" icon="check" title=${range ? `Release declares Minecraft ${range}` : "Release declares no version list"}>works on MC ${c.mc}<//>`
      : html`<${Tag} kind="warn" icon="triangle-alert" title=${`Server runs ${c.mc}; release lists ${c.supported?.join(", ") || "no versions"}`}>not listed for ${c.mc}<//>`}
  </span>`;
}

function useSheetFocus(ref, onClose) {
  useEffect(() => {
    const prev = document.activeElement;
    ref.current?.querySelector("[data-autofocus]")?.focus() || ref.current?.focus();
    const k = (e) => { if (e.key === "Escape" && !getState().confirm) onClose(); };
    document.addEventListener("keydown", k);
    return () => { document.removeEventListener("keydown", k); prev?.focus?.(); };
  }, []);
}

function Changeset({ cs }) {
  const [plan, setPlan] = useState(null);
  const [err, setErr] = useState(null);
  const [excluded, setExcluded] = useState(new Set());
  const [jobId, setJobId] = useState(null);
  const [applyErr, setApplyErr] = useState(null);
  const [n, setN] = useState(0); // re-plan counter
  const ref = useRef();
  const live = useStore(s => s.jobs.find(j => j.id === jobId));

  const close = () => { setState({ changeset: null, inlineJob: getState().inlineJob === jobId ? null : getState().inlineJob }); };
  useSheetFocus(ref, close);

  useEffect(() => {
    setPlan(null); setErr(null); setApplyErr(null);
    (async () => {
      const p = await post("/updates/plan", { items: await scopeItems(cs.scope) });
      const rows = (p.rows || []).map(r => ({ ...r, row_id: `${r.server}|${r.key}`, compat: compatOf(r.compat) }));
      setPlan({ ...p, rows });
      setExcluded(new Set());
    })().catch(setErr);
  }, [n]);

  const rows = plan?.rows || [];
  const groups = useMemo(() => {
    const m = new Map();
    for (const r of rows) (m.get(r.server) || m.set(r.server, []).get(r.server)).push(r);
    return [...m];
  }, [plan]);
  const included = rows.filter(r => !excluded.has(r.row_id));
  const tot = {
    changes: included.length,
    plugins: new Set(included.map(r => r.key)).size,
    servers: new Set(included.map(r => r.server)).size,
    bytes: included.reduce((a, r) => a + (r.size || 0), 0),
    compatWarn: included.filter(r => r.compat && !r.compat.ok).length,
  };
  const skipped = plan?.skipped || [];
  const toggle = (ids, on) => setExcluded(s => { const x = new Set(s); ids.forEach(id => on ? x.delete(id) : x.add(id)); return x; });

  const apply = async () => {
    if (tot.compatWarn) {
      const ok = await confirmDialog({ title: `Apply ${plural(tot.compatWarn, "update")} not listed for this Minecraft version?`, body: "Those releases don't declare support for the server's Minecraft version. They may still work, but check the changelog first.", confirmLabel: `Apply ${plural(tot.changes, "change")}` });
      if (!ok) return;
    }
    setApplyErr(null);
    try {
      const exclude = rows.filter(r => excluded.has(r.row_id)).map(r => [r.server, r.key]);
      const r = await post("/updates/apply", { plan_id: plan.plan_id, exclude });
      setJobId(r.job_id);
      setState({ inlineJob: r.job_id });
      trackJob(r.job_id, { title: "Apply updates" });
    } catch (e) { setApplyErr(e); }
  };

  const running = live && isActive(live.status);
  const job = live?.job;
  const done = jobId && live && !running;

  return html`<div class="scrim" onClick=${close}></div>
  <aside class="sheet" role="dialog" aria-modal="true" aria-labelledby="cs-t" ref=${ref} tabindex="-1">
    <header class="sheet-head">
      <div class="grow"><h2 id="cs-t">${done ? (jobTone(job) === "ok" ? "Updates applied" : "Update finished with problems") : running ? "Applying updates…" : cs.title}</h2>
        <p class="small muted">${jobId ? html`<a class="link" href=${`#/activity/${jobId}`} onClick=${close}>Job details</a>` : "Exactly these changes will be applied — nothing else."}</p></div>
      <${Btn} kind="ghost" icon="x" aria-label="Close" onClick=${close} />
    </header>
    ${jobId ? html`<${Result} live=${live} job=${job} running=${running} rows=${rows} onClose=${close} />`
      : html`<div class="sheet-body">
      ${err ? html`<div style="padding:16px"><${ErrorState} title="Couldn't build the changeset" error=${err} retry=${() => setN(n + 1)} /></div>`
        : !plan ? html`<div class="stack" style="padding:16px" aria-busy="true"><span class="small muted">Building plan — resolving downloads and compatibility…</span>${Array.from({ length: 6 }, () => html`<${Skel} h=${28} />`)}</div>`
        : !rows.length ? html`<${Empty} ok icon="circle-check" title="Nothing to update">Every plugin in this scope is on its latest compatible version.<//>`
        : html`${(plan.warnings || []).map(w => html`<div class="plan-warn"><${Icon} n="triangle-alert" cls="i-sm" />${w}</div>`)}
          ${skipped.length > 0 && html`<details class="cs-skipped"><summary>${plural(skipped.length, "update")} left out of this plan</summary>
            <ul>${skipped.map(k => html`<li><b>${k.name || k.key}</b>${k.server ? ` on ${k.server}` : ""} — <span class="muted">${k.reason || k.detail || "skipped"}</span></li>`)}</ul></details>`}
          <table class="tbl cs-tbl">
            <caption class="sr-only">Planned plugin updates by server</caption>
            <thead><tr><th class="col-check"><${Check} label="Include all" checked=${!excluded.size} indeterminate=${excluded.size > 0 && included.length > 0} onChange=${v => toggle(rows.map(r => r.row_id), v)} /></th>
              <th scope="col">Plugin</th><th scope="col">Change</th><th scope="col" class="hide-sm">Compatibility</th><th scope="col" class="num hide-sm">Size</th></tr></thead>
            ${groups.map(([server, rs]) => { const inc = rs.filter(r => !excluded.has(r.row_id)).length; return html`<tbody>
              <tr class="cs-group"><th class="col-check"><${Check} label=${`Include all on ${server}`} checked=${inc === rs.length} indeterminate=${inc > 0 && inc < rs.length} onChange=${v => toggle(rs.map(r => r.row_id), v)} /></th>
                <th colspan="4" scope="rowgroup"><span class="row" style="gap:8px">${server}<span class="small muted" style="font-weight:500">${inc} of ${plural(rs.length, "change")}</span></span></th></tr>
              ${rs.map(r => { const off = excluded.has(r.row_id); return html`<tr class=${off ? "is-off" : ""}>
                <td class="col-check"><${Check} label=${`Include ${r.name} on ${server}`} checked=${!off} onChange=${v => toggle([r.row_id], v)} /></td>
                <td><div class="cell-name"><b>${r.name}</b>${r.changelog_url && html`<a class="link small" href=${r.changelog_url} target="_blank" rel="noopener">Changelog<span class="sr-only"> for ${r.name} (opens in new tab)</span></a>`}</div></td>
                <td><div class="jar-swap"><span class="old" title=${r.from_jar}>${r.from_jar || r.from_version}</span><span class="new" title=${r.to_jar}><${Icon} n="arrow-right" cls="i-xs" />${r.to_jar || r.to_version}</span>
                  ${r.also_removes?.length > 0 && html`<span class="small muted" style="font-family:var(--font-sans)">also removes ${r.also_removes.join(", ")}</span>`}
                  <span class="only-sm" style="margin-top:4px"><${CompatChip} c=${r.compat} /></span>
</div></td>
                <td class="hide-sm"><${CompatChip} c=${r.compat} />${r.verified && html`<span class="small muted row" style="gap:3px;margin-top:3px"><${Icon} n="shield" cls="i-xs" />hash verified</span>`}</td>
                <td class="num hide-sm small muted">${r.size ? bytes(r.size) : "—"}</td>
              </tr>`; })}
            </tbody>`; })}
          </table>`}
    </div>
    <footer class="sheet-foot">
      ${applyErr && html`<div class="error-box" style="width:100%;padding:10px 12px" role="alert"><${Icon} n="triangle-alert" />
        <div class="grow"><b>${applyErr.status === 409 ? "This plan is out of date" : "Couldn't apply"}</b><p>${applyErr.message}</p>
          ${applyErr.conflicts?.length > 0 && html`<ul class="small" style="margin:-4px 0 8px">${applyErr.conflicts.map(c => html`<li>${c.server} · ${c.from_jar || c.key}: ${c.reason}</li>`)}</ul>`}
          <${Btn} size="sm" icon="refresh-cw" onClick=${() => setN(n + 1)}>Re-plan<//></div></div>`}
      <div class="grow small">${plan && rows.length ? html`<b>${plural(tot.changes, "change")}</b><span class="muted"> · ${plural(tot.plugins, "plugin")} · ${plural(tot.servers, "server")}${tot.bytes ? ` · ${bytes(tot.bytes)} download` : ""}</span>
        ${tot.compatWarn > 0 && html`<div style="color:var(--warn)">${plural(tot.compatWarn, "change")} not listed for the server's MC version</div>`}` : ""}</div>
      <${Btn} onClick=${close}>Cancel<//>
      <${Btn} kind="primary" icon="circle-arrow-up" disabled=${!plan || !tot.changes} onClick=${apply} data-autofocus>Apply ${plural(tot.changes, "change")}<//>
    </footer>`}
  </aside>`;
}

async function scopeItems(scope) {
  if (scope === "all" || !scope) return "all";
  if (scope.items) return scope.items;
  if (scope.keys) return scope.keys.map(key => ({ key }));
  if (scope.server) {
    const ups = updatesOf(await get("/updates"));
    return ups.filter(u => u.servers.includes(scope.server)).map(u => ({ key: u.key, servers: [scope.server] }));
  }
  return "all";
}

function Result({ live, job, running, rows, onClose }) {
  const [restarted, setRestarted] = useState(new Set());
  const [undoing, setUndoing] = useState(false);
  const res = job?.results || [];
  const byOutcome = res.reduce((a, r) => (a[r.outcome] = (a[r.outcome] || 0) + 1, a), {});
  const restart = job?.restart_servers || [...new Set(res.filter(r => r.outcome === "changed").map(r => r.server))];
  const nameOf = (r) => r.item;
  const mark = async (s) => {
    try { await post(`/servers/${encodeURIComponent(s)}/restarted`); setRestarted(x => new Set([...x, s])); invalidate("/servers", "/overview"); }
    catch (e) { toast({ kind: "err", title: `Couldn't mark ${s}`, body: e.message }); }
  };
  const undo = async () => {
    const ok = await confirmDialog({ title: "Undo these updates?", body: `The previous jars are restored on ${plural(new Set(res.map(r => r.server)).size, "server")}. The undo is recorded as its own job.`, confirmLabel: "Restore previous jars" });
    if (!ok) return;
    setUndoing(true);
    try { const r = await post(`/jobs/${encodeURIComponent(job.id)}/undo`); trackJob(r.job_id, { title: "Undo updates" }); onClose(); }
    catch (e) { toast({ kind: "err", title: "Undo refused", body: e.message }); }
    setUndoing(false);
  };
  return html`<div class="sheet-body">
    <div class=${"progress" + (running ? "" : jobTone(job) === "ok" ? " done" : " fail")} role="progressbar" aria-label="Apply progress" aria-valuetext=${live?.status}></div>
    ${!running && job && html`<div class="cs-result">
      <div class="row wrap" style="gap:6px">${Object.entries(byOutcome).map(([k, v]) => html`<${Tag} kind=${k === "changed" ? "ok" : k === "error" ? "danger" : ""}>${v} ${k === "changed" ? "updated" : k}<//>`)}
</div>
      ${restart.length > 0 && html`<section class="restart-list" aria-labelledby="rs-h">
        <h3 id="rs-h"><${Icon} n="rotate-ccw" cls="i-sm" />Restart checklist <span class="muted small" style="font-weight:500">${restart.length - restarted.size} of ${plural(restart.length, "server")} still need${restart.length - restarted.size === 1 ? "s" : ""} a restart</span></h3>
        <ul>${restart.map(s => { const d = restarted.has(s); return html`<li class=${d ? "is-done" : ""}>
          <span class="tick">${d ? html`<${Icon} n="check" cls="i-xs" />` : ""}</span><b>${s}</b>
          <span class="small muted grow">${res.filter(r => r.server === s && r.outcome === "changed").map(nameOf).join(", ")}</span>
          ${d ? html`<span class="small muted">Marked restarted</span>` : html`<${Btn} size="sm" onClick=${() => mark(s)}>Mark restarted<//>`}</li>`; })}</ul>
      </section>`}
      ${res.length > 0 && html`<table class="tbl"><caption class="sr-only">Per-change results</caption><thead><tr><th scope="col">Server</th><th scope="col">Plugin</th><th scope="col">Outcome</th><th scope="col" class="hide-sm">Detail</th></tr></thead>
        <tbody>${res.map(r => html`<tr><td class="strong">${r.server}</td><td>${nameOf(r)}</td><td class=${"small outcome-" + r.outcome} style="font-weight:600">${r.outcome === "changed" ? "updated" : r.outcome}</td><td class="hide-sm small muted">${r.detail}</td></tr>`)}</tbody></table>`}
    </div>`}
    <${LogView} lines=${live?.lines || []} live=${running} />
  </div>
  <footer class="sheet-foot">
    <span class="grow"></span>
    ${!running && job?.undoable && html`<${Btn} icon="undo-2" busy=${undoing} onClick=${undo}>Undo<//>`}
    <${Btn} kind=${running ? "" : "primary"} onClick=${onClose}>${running ? "Run in background" : "Done"}<//>
  </footer>`;
}
