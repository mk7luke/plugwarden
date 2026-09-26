// Activity — job history, structured per-server results, full log, and undo.
import { html, useState, useMemo, useEffect } from "../lib.js";
import { useQuery, useStore, confirmDialog } from "../store.js";
import { post } from "../api.js";
import { runJob, JOB_TITLES, KIND_ICON, jobTone, isActive, jobSummary, jobTitle } from "../jobs.js";
import { navigate } from "../router.js";
import { LogView } from "../components/overlays.js";
import { StartupCheck } from "../components/health.js";
import { Icon, Btn, Tag, SkelRows, ErrorState, Empty, PageHead, Skel } from "../components/ui.js";
import { relTime, absTime, duration, plural } from "../fmt.js";

const KINDS = [["all", "All"], ["update-apply", "Updates"], ["deploy", "Deploys"], ["remove", "Removals"], ["update-check", "Checks"], ["undo", "Undos"]];
const STATUS_TAG = { done: ["ok", "Done"], failed: ["danger", "Failed"], interrupted: ["danger", "Interrupted"], running: ["accent", "Running"], queued: ["", "Queued"], undone: ["", "Reverted"] };
const reverted = (j) => j.status === "undone" || !!j.undone_by;
const pref = (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } };
const OUTCOME_TAG = { changed: "ok", error: "danger", skipped: "", unchanged: "" };

export function Activity({ id, tab }) {
  const q = useQuery("/jobs");
  // The Audit tab lives in the URL (#/activity?tab=audit) so it can be linked and survives Back.
  const isAudit = tab === "audit" || tab === "access";
  const [jobKind, setJobKind] = useState("all");
  const kind = isAudit ? "access" : jobKind;
  const setKind = (k) => {
    if (k === "access") navigate("#/activity?tab=audit");
    else { setJobKind(k); if (isAudit) navigate("#/activity"); }
  };
  const [hideDry, setHideDry] = useState(() => pref("amp.act.hidedry", "1") === "1");
  const setHD = (v) => { setHideDry(v); try { localStorage.setItem("amp.act.hidedry", v ? "1" : "0"); } catch {} };
  const base = useMemo(() => (q.data || []).filter(j => !hideDry || !j.dry_run), [q.data, hideDry]);
  const list = useMemo(() => base.filter(j => kind === "all" || j.kind === kind), [base, kind]);
  const count = (k) => k === "all" ? base.length : base.filter(j => j.kind === k).length;
  const selected = id || null;
  // Wide screens have room for the detail pane: open the newest job instead of an empty "Select a job".
  useEffect(() => {
    if (!id && kind !== "access" && list.length && matchMedia("(min-width: 1280px)").matches) navigate(`#/activity/${list[0].id}`, { replace: true });
  }, [id, list.length, kind]);

  return html`<${PageHead} title="Activity" sub="Every update, deploy and undo — who ran it, what changed, and the full log." />
    <div class="toolbar"><div class="seg" role="group" aria-label="Filter by kind">
      ${KINDS.map(([k, l]) => html`<button type="button" aria-pressed=${kind === k ? "true" : "false"} onClick=${() => setKind(k)}>${l}${q.data ? html` <span class="muted num">${count(k)}</span>` : ""}</button>`)}
      <button type="button" aria-pressed=${kind === "access" ? "true" : "false"} onClick=${() => setKind("access")} title="Who viewed config diffs and changed settings, pins, ignores, sources and uploads"><${Icon} n="eye" cls="i-xs" />Audit</button></div>
      <span class="spacer"></span>
      ${kind !== "access" && html`<label class="switch small"><input type="checkbox" checked=${hideDry} onChange=${e => setHD(e.currentTarget.checked)} />Hide dry runs</label>`}</div>
    ${kind === "access" ? html`<${AccessLog} />` : html`
    <div class=${"act-layout" + (selected ? " has-detail" : "") + (q.data && !q.data.length ? " is-empty" : "")}>
      <section class="panel job-list-panel" aria-label="Job history">
        ${q.error ? html`<div class="panel-body"><${ErrorState} error=${q.error} retry=${q.reload} /></div>`
          : q.loading ? html`<${SkelRows} n=${8} cols=${[4, 50, 12]} />`
          : !list.length ? html`<${Empty} icon="history" title=${kind === "all" ? "No activity yet" : "Nothing of this kind"}>Jobs appear here as soon as someone checks for updates, deploys, or undoes a change.<//>`
          : html`<div class="job-list" role="list">${list.map(j => html`<a class="job-row" role="listitem" data-nav href=${`#/activity/${j.id}`} aria-current=${selected === j.id ? "true" : undefined}>
              <span class=${"feed-icon " + jobTone(j)}><${Icon} n=${isActive(j.status) ? "loader-circle" : KIND_ICON[j.kind] || "terminal"} cls=${"i-xs" + (isActive(j.status) ? " spin" : "")} /></span>
              <div style="min-width:0"><div class=${"t" + (reverted(j) ? " is-reverted" : "")}>${jobTitle(j)}${j.dry_run ? html` <span class="tag" style="vertical-align:1px">dry run</span>` : ""}${reverted(j) ? html` <span class="tag" style="vertical-align:1px">Reverted</span>` : ""}</div>
                <div class="m"><span class="ellipsis">${jobSummary(j) || (isActive(j.status) ? "In progress…" : "—")}</span></div>
                <div class="m"><span class="ellipsis">${j.user || "system"}</span>${j.servers?.length ? html`·<span>${plural(j.servers.length, "server")}</span>` : ""}${!["done", "undone"].includes(j.status) ? html`·<span class=${jobTone(j) === "danger" ? "outcome-error" : ""}>${j.status}</span>` : ""}</div></div>
              <span class="when" title=${absTime(j.started || j.created)}>${relTime(j.started || j.created)}</span>
            </a>`)}</div>`}
      </section>
      <div class="job-detail">${selected ? html`<a class="btn btn-ghost btn-sm only-sm" href="#/activity" style="margin-bottom:8px"><${Icon} n="chevron-left" cls="i-sm" />All activity</a><${JobDetail} key=${selected} id=${selected} />`
        : html`<div class="panel"><${Empty} icon="scroll-text" title="Select a job">Pick a job to see per-server results, the full log, and undo.<//></div>`}</div>
    </div>`}`;
}

// Read-access log: who viewed which config diffs (secrets are redacted in the diff itself).
const ACTION_LABEL = { "canary-failed": "canary check failed", diff: "viewed diff", settings: "changed settings", pin: "pin", ignore: "ignore", source_map: "source mapping", upload: "uploaded jar", "plan-values": "saw server-specific values" };
// before → after for change entries, shortened to what differs.
function change(e) {
  if (e.before === undefined && e.after === undefined) return e.detail || "";
  const show = (v) => v == null ? "none" : typeof v === "object" ? JSON.stringify(v) : String(v);
  if (e.before && e.after && typeof e.before === "object" && typeof e.after === "object") {
    const keys = [...new Set([...Object.keys(e.before), ...Object.keys(e.after)])].filter(k => JSON.stringify(e.before[k]) !== JSON.stringify(e.after[k]));
    return keys.map(k => `${k}: ${show(e.before[k])} → ${show(e.after[k])}`).join("; ") || e.detail || "";
  }
  return `${show(e.before)} → ${show(e.after)}`;
}

function AccessLog() {
  const [f, setF] = useState({ user: "", server: "", path: "", action: "" });
  const [qs, setQs] = useState("limit=200");
  useEffect(() => {
    const t = setTimeout(() => setQs(new URLSearchParams(Object.entries({ limit: "200", ...f }).filter(([, v]) => v)).toString()), 250);
    return () => clearTimeout(t);
  }, [f.user, f.server, f.path, f.action]);
  const q = useQuery(`/access-log?${qs}`);
  const entries = q.data?.entries || [];
  const field = (k, label) => html`<div class="input-wrap" style="width:200px"><${Icon} n="filter" cls="i-sm" /><input class="input" type="search" aria-label=${`Filter by ${label}`} placeholder=${label} value=${f[k]} onInput=${e => setF({ ...f, [k]: e.currentTarget.value })} /></div>`;
  return html`<div class="toolbar">
      <select class="select" style="width:auto" aria-label="Filter by action" value=${f.action} onChange=${e => setF({ ...f, action: e.currentTarget.value })}>
        ${[["", "All actions"], ["diff", "Viewed diff"], ["plan-values", "Saw server-specific values"], ["settings", "Settings"], ["pin", "Pin"], ["ignore", "Ignore"], ["upload", "Upload"]].map(([v, l]) => html`<option value=${v}>${l}</option>`)}</select>
      ${field("user", "User")}${field("server", "Server")}${field("path", "Path")}<span class="spacer"></span>
      <span class="small muted">Viewing a diff is recorded (secrets redacted), as are settings, pin, ignore, source and upload changes.</span></div>
    <section class="panel" aria-label="Access log">
      ${q.error ? html`<div class="panel-body"><${ErrorState} error=${q.error} retry=${q.reload} /></div>`
        : q.loading ? html`<${SkelRows} n=${6} cols=${[12, 20, 30, 20]} />`
        : !entries.length ? html`<${Empty} icon="eye" title="Nothing recorded yet">${Object.values(f).some(Boolean) ? "Nothing matches these filters." : "Config diff views and settings, pin, ignore, source and upload changes are recorded here."}<//>`
        : html`<div class="tbl-wrap"><table class="tbl"><caption class="sr-only">Access log, newest first</caption>
          <thead><tr><th scope="col">When</th><th scope="col">User</th><th scope="col">What</th><th scope="col" class="hide-sm">Servers</th><th scope="col" class="hide-md">Detail</th></tr></thead>
          <tbody>${entries.map(e => html`<tr><td class="small muted" title=${absTime(e.last_seen || e.at)} style="white-space:nowrap">${relTime(e.last_seen || e.at)}</td><td>${e.user}</td>
            <td><div class="cell-name"><span class="mono small">${e.path || e.target || e.key || "—"}</span><span class="small muted">${ACTION_LABEL[e.action] || e.action}${e.count > 1 ? ` ×${e.count} · ${new Date(e.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}–${new Date(e.last_seen || e.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}</span></div></td>
            <td class="hide-sm small">${(e.servers || []).join(" → ")}</td><td class="hide-md small muted audit-change" title=${change(e)}>${change(e)}</td></tr>`)}</tbody></table></div>`}
    </section>`;
}

function JobDetail({ id }) {
  const q = useQuery(`/jobs/${encodeURIComponent(id)}`);
  const live = useStore(s => s.jobs.find(j => j.id === id));
  const [busy, setBusy] = useState(false);
  const [checkAll, setCheckAll] = useState(false);
  useEffect(() => { if (live && !isActive(live.status)) q.reload(); }, [live?.status]);
  if (q.error) return html`<div class="panel"><div class="panel-body"><${ErrorState} error=${q.error} retry=${q.reload} /></div></div>`;
  if (q.loading) return html`<div class="panel"><div class="panel-body stack"><${Skel} w="60%" h=${16} /><${Skel} w="40%" /><${Skel} w="50%" /><${Skel} h=${120} /></div></div>`;
  const j = q.data;
  if (!j) return html`<div class="panel"><${Empty} icon="scroll-text" title="Job not found">It may have been pruned from history.<//></div>`;
  const [tk, tl] = reverted(j) ? STATUS_TAG.undone : STATUS_TAG[j.status] || ["", j.status];
  const res = j.results || [];
  const tally = res.reduce((a, r) => (a[r.outcome] = (a[r.outcome] || 0) + 1, a), {});
  const servers = [...new Set(res.map(r => r.server))];
  const canUndo = !!j.undoable;
  const touched = j.restart_servers || j.changed_servers || [...new Set(res.filter(r => r.outcome === "changed").map(r => r.server))];
  const running = isActive(live?.status || j.status);
  const logLines = live && isActive(live.status) ? live.lines : (j.log || "").split("\n").filter((l, i, a) => l || i < a.length - 1);

  const undo = async () => {
    const ok = await confirmDialog({
      title: `Undo ${j.id}?`, confirmLabel: "Restore backups",
      body: `Files changed by this job will be restored from its backups on ${plural(servers.length, "server")}. The undo itself is recorded as a new job.`,
      list: servers, danger: servers.length > 1, typeToConfirm: servers.length > 3 ? `undo ${j.id}` : null,
    });
    if (!ok) return;
    setBusy(true);
    const r = await runJob(post(`/jobs/${encodeURIComponent(j.id)}/undo`), { title: `Undo ${j.id}` });
    setBusy(false);
    if (r?.id) navigate(`#/activity/${r.id}`);
  };

  return html`<article class=${"panel" + (reverted(j) ? " is-reverted" : "")} aria-labelledby="jd-h">
    ${(j.undo_failed_by || j.undo_error) && !reverted(j) && html`<div class="reverted-banner is-failed" role="alert"><${Icon} n="circle-x" cls="i-sm" />
      <span class="grow">Undo failed${j.undo_error ? ` — ${j.undo_error}` : ""}.</span>
      ${j.undo_failed_by && html`<a class="link" href=${`#/activity/${j.undo_failed_by}`}>View failed undo</a>`}
      ${j.undoable && html`<${Btn} size="sm" icon="undo-2" busy=${busy} onClick=${undo}>Retry undo<//>`}</div>`}
    ${reverted(j) && html`<div class="reverted-banner" role="status"><${Icon} n="undo-2" cls="i-sm" /><span>Reverted${j.undone_at ? ` ${relTime(j.undone_at)}` : ""} — the changes below were rolled back.</span>
      ${j.undone_by && html`<a class="link" href=${`#/activity/${j.undone_by}`}>View undo job</a>`}</div>`}
    <div class="panel-head" style="flex-wrap:wrap">
      <span class=${"feed-icon " + jobTone(j)}><${Icon} n=${KIND_ICON[j.kind] || "terminal"} cls="i-xs" /></span>
      <h2 id="jd-h" class="grow" style="min-width:160px">${JOB_TITLES[j.kind] || j.kind}${j.dry_run ? " · dry run" : ""}<span class="sub" style="display:block;font-weight:400;margin-top:1px">${jobSummary(j) || (running ? "In progress…" : "")}</span></h2>
      ${canUndo && html`<${Btn} size="sm" icon="undo-2" busy=${busy} onClick=${undo}>Undo<//>`}
    </div>
    <div class="panel-body">
      <dl class="kv">
        <dt>Status</dt><dd><${Tag} kind=${tk}>${tl}<//></dd>
        <dt>Job</dt><dd class="row" style="gap:6px"><span class="mono small">${j.id}</span><${Btn} size="sm" kind="ghost" icon="copy" aria-label="Copy job id" onClick=${() => navigator.clipboard?.writeText(j.id)} /></dd>
        <dt>Run by</dt><dd>${j.user || "system"}</dd>
        <dt>Started</dt><dd>${absTime(j.started || j.created)} <span class="muted">(${relTime(j.started || j.created)})</span></dd>
        ${j.undo_of && html`<dt>Undo of</dt><dd><a class="link mono small" href=${`#/activity/${j.undo_of}`}>${j.undo_of}</a></dd>`}
        ${j.undone_by && html`<dt>Undone by</dt><dd><a class="link mono small" href=${`#/activity/${j.undone_by}`}>${j.undone_by}</a></dd>`}
        ${j.dry_run && html`<dt>Mode</dt><dd>Dry run — nothing was changed</dd>`}
        ${j.finished && html`<dt>Duration</dt><dd>${duration(j.started, j.finished)}</dd>`}
        ${res.length > 0 && html`<dt>Outcome</dt><dd class="row wrap" style="gap:4px">${Object.entries(tally).map(([k, n]) => html`<${Tag} kind=${j.dry_run && k === "changed" ? "update" : OUTCOME_TAG[k] || ""}>${n} ${j.dry_run && k === "changed" ? "would change" : k}<//>`)}</dd>`}
      </dl>
    </div>
    ${!j.dry_run && ["update-apply", "deploy"].includes(j.kind) && touched.length > 0 && html`<div class="panel-body startup-block" style="border-top:1px solid var(--line)">
      <div class="row" style="margin-bottom:8px"><span class="field-label grow">Plugin startup after this job</span>
        ${touched.length > 1 && !checkAll && html`<${Btn} size="sm" icon="scroll-text" onClick=${() => setCheckAll(true)}>Check all ${touched.length}<//>`}</div>
      ${touched.map(s => html`<div class="startup-row"><b class="small">${s}</b><${StartupCheck} server=${s} since=${j.finished} auto=${checkAll} /></div>`)}</div>`}
    ${res.length > 0 && html`<div class="tbl-wrap" style="max-height:300px;border-top:1px solid var(--line)"><table class="tbl">
      <thead><tr><th scope="col">Server</th><th scope="col">Item</th><th scope="col">Outcome</th><th scope="col" class="hide-md">Detail</th></tr></thead>
      <tbody>${res.map(r => html`<tr><td class="strong" style="white-space:nowrap">${r.server}</td><td><span class="jar" style="max-width:200px" title=${r.item}>${r.item}</span></td>
        <td class=${"small outcome-" + (j.dry_run && r.outcome === "changed" ? "would_change" : r.outcome)} style="font-weight:600;white-space:nowrap">${r.reason_code === "changed_since_job" ? "changed since — kept" : r.skipped_by_choice ? "skipped (by choice)" : j.dry_run && r.outcome === "changed" ? "would change" : r.outcome}</td><td class="hide-md small muted">${r.detail}</td></tr>`)}</tbody></table></div>`}
    <div class="panel-head" style="border-top:1px solid var(--line);border-bottom:0"><${Icon} n="terminal" cls="i-sm" /><h3>Log</h3><span class="spacer"></span>
      <${Btn} size="sm" kind="ghost" icon="copy" onClick=${() => navigator.clipboard?.writeText(logLines.join("\n"))}>Copy<//></div>
    <${LogView} lines=${logLines} live=${running} empty="No log output." />
  </article>`;
}
