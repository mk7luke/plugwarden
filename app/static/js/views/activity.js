// Activity — job history, structured per-server results, full log, and undo.
import { html, useState, useMemo, useEffect } from "../lib.js";
import { useQuery, useStore, confirmDialog } from "../store.js";
import { post } from "../api.js";
import { runJob, JOB_TITLES, KIND_ICON, jobTone, isActive } from "../jobs.js";
import { navigate } from "../router.js";
import { LogView } from "../components/overlays.js";
import { Icon, Btn, Tag, SkelRows, ErrorState, Empty, PageHead, Skel } from "../components/ui.js";
import { relTime, absTime, duration, plural } from "../fmt.js";

const KINDS = [["all", "All"], ["update-apply", "Updates"], ["deploy", "Deploys"], ["remove", "Removals"], ["update-check", "Checks"], ["undo", "Undos"]];
const STATUS_TAG = { done: ["ok", "Done"], failed: ["danger", "Failed"], interrupted: ["danger", "Interrupted"], running: ["accent", "Running"], queued: ["", "Queued"] };
const OUTCOME_TAG = { changed: "ok", error: "danger", skipped: "", unchanged: "" };

export function Activity({ id }) {
  const q = useQuery("/jobs");
  const [kind, setKind] = useState("all");
  const list = useMemo(() => (q.data || []).filter(j => kind === "all" || j.kind === kind), [q.data, kind]);
  const selected = id || null;

  return html`<${PageHead} title="Activity" sub="Every update, deploy and undo — who ran it, what changed, and the full log." />
    <div class="toolbar"><div class="seg" role="group" aria-label="Filter by kind">
      ${KINDS.map(([k, l]) => html`<button type="button" aria-pressed=${kind === k ? "true" : "false"} onClick=${() => setKind(k)}>${l}</button>`)}</div></div>
    <div class=${"act-layout" + (selected ? " has-detail" : "")}>
      <section class="panel job-list-panel" aria-label="Job history">
        ${q.error ? html`<div class="panel-body"><${ErrorState} error=${q.error} retry=${q.reload} /></div>`
          : q.loading ? html`<${SkelRows} n=${8} cols=${[4, 50, 12]} />`
          : !list.length ? html`<${Empty} icon="history" title=${kind === "all" ? "No activity yet" : "Nothing of this kind"}>Jobs appear here as soon as someone checks for updates, deploys, or undoes a change.<//>`
          : html`<div class="job-list" role="list">${list.map(j => html`<a class="job-row" role="listitem" href=${`#/activity/${j.id}`} aria-current=${selected === j.id ? "true" : undefined}>
              <span class=${"feed-icon " + jobTone(j)}><${Icon} n=${isActive(j.status) ? "loader-circle" : KIND_ICON[j.kind] || "terminal"} cls=${"i-xs" + (isActive(j.status) ? " spin" : "")} /></span>
              <div style="min-width:0"><div class="t">${JOB_TITLES[j.kind] || j.kind}${j.dry_run ? html` <span class="tag" style="vertical-align:1px">dry run</span>` : ""}${j.undone_by ? html` <span class="tag" style="vertical-align:1px">undone</span>` : ""}</div>
                <div class="m"><span class="ellipsis">${j.summary || (isActive(j.status) ? "In progress…" : "—")}</span></div>
                <div class="m"><span class="ellipsis">${j.user || "system"}</span>${j.servers?.length ? html`·<span>${plural(j.servers.length, "server")}</span>` : ""}${j.status !== "done" ? html`·<span class=${jobTone(j) === "danger" ? "outcome-error" : ""}>${j.status}</span>` : ""}</div></div>
              <span class="when" title=${absTime(j.started || j.created)}>${relTime(j.started || j.created)}</span>
            </a>`)}</div>`}
      </section>
      <div class="job-detail">${selected ? html`<a class="btn btn-ghost btn-sm only-sm" href="#/activity" style="margin-bottom:8px"><${Icon} n="chevron-left" cls="i-sm" />All activity</a><${JobDetail} key=${selected} id=${selected} />`
        : html`<div class="panel"><${Empty} icon="scroll-text" title="Select a job">Pick a job to see per-server results, the full log, and undo.<//></div>`}</div>
    </div>`;
}

function JobDetail({ id }) {
  const q = useQuery(`/jobs/${encodeURIComponent(id)}`);
  const live = useStore(s => s.jobs.find(j => j.id === id));
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (live && !isActive(live.status)) q.reload(); }, [live?.status]);
  if (q.error) return html`<div class="panel"><div class="panel-body"><${ErrorState} error=${q.error} retry=${q.reload} /></div></div>`;
  if (q.loading) return html`<div class="panel"><div class="panel-body stack"><${Skel} w="60%" h=${16} /><${Skel} w="40%" /><${Skel} w="50%" /><${Skel} h=${120} /></div></div>`;
  const j = q.data;
  if (!j) return html`<div class="panel"><${Empty} icon="scroll-text" title="Job not found">It may have been pruned from history.<//></div>`;
  const [tk, tl] = STATUS_TAG[j.status] || ["", j.status];
  const res = j.results || [];
  const tally = res.reduce((a, r) => (a[r.outcome] = (a[r.outcome] || 0) + 1, a), {});
  const servers = [...new Set(res.map(r => r.server))];
  const canUndo = !!j.undoable;
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

  return html`<article class="panel" aria-labelledby="jd-h">
    <div class="panel-head" style="flex-wrap:wrap">
      <span class=${"feed-icon " + jobTone(j)}><${Icon} n=${KIND_ICON[j.kind] || "terminal"} cls="i-xs" /></span>
      <h2 id="jd-h" class="grow" style="min-width:160px">${JOB_TITLES[j.kind] || j.kind}${j.dry_run ? " · dry run" : ""}<span class="sub" style="display:block;font-weight:400;margin-top:1px">${j.summary || (running ? "In progress…" : "")}</span></h2>
      ${canUndo && html`<${Btn} size="sm" icon="undo-2" busy=${busy} onClick=${undo}>Undo<//>`}
    </div>
    <div class="panel-body">
      <dl class="kv">
        <dt>Status</dt><dd><${Tag} kind=${tk}>${tl}<//></dd>
        <dt>Job</dt><dd class="mono small">${j.id} · ${JOB_TITLES[j.kind] || j.kind}</dd>
        <dt>Run by</dt><dd>${j.user || "system"}</dd>
        <dt>Started</dt><dd>${absTime(j.started || j.created)} <span class="muted">(${relTime(j.started || j.created)})</span></dd>
        ${j.undo_of && html`<dt>Undo of</dt><dd><a class="link mono small" href=${`#/activity/${j.undo_of}`}>${j.undo_of}</a></dd>`}
        ${j.undone_by && html`<dt>Undone by</dt><dd><a class="link mono small" href=${`#/activity/${j.undone_by}`}>${j.undone_by}</a></dd>`}
        ${j.dry_run && html`<dt>Mode</dt><dd>Dry run — nothing was changed</dd>`}
        ${j.finished && html`<dt>Duration</dt><dd>${duration(j.started, j.finished)}</dd>`}
        ${res.length > 0 && html`<dt>Outcome</dt><dd class="row wrap" style="gap:4px">${Object.entries(tally).map(([k, n]) => html`<${Tag} kind=${j.dry_run && k === "changed" ? "update" : OUTCOME_TAG[k] || ""}>${n} ${j.dry_run && k === "changed" ? "would change" : k}<//>`)}</dd>`}
      </dl>
    </div>
    ${res.length > 0 && html`<div class="tbl-wrap" style="max-height:300px;border-top:1px solid var(--line)"><table class="tbl">
      <thead><tr><th scope="col">Server</th><th scope="col">Item</th><th scope="col">Outcome</th><th scope="col" class="hide-md">Detail</th></tr></thead>
      <tbody>${res.map(r => html`<tr><td class="strong" style="white-space:nowrap">${r.server}</td><td><span class="jar" style="max-width:200px" title=${r.item}>${r.item}</span></td>
        <td class=${"small outcome-" + (j.dry_run && r.outcome === "changed" ? "would_change" : r.outcome)} style="font-weight:600;white-space:nowrap">${j.dry_run && r.outcome === "changed" ? "would change" : r.outcome}</td><td class="hide-md small muted">${r.detail}</td></tr>`)}</tbody></table></div>`}
    <div class="panel-head" style="border-top:1px solid var(--line);border-bottom:0"><${Icon} n="terminal" cls="i-sm" /><h3>Log</h3><span class="spacer"></span>
      <${Btn} size="sm" kind="ghost" icon="copy" onClick=${() => navigator.clipboard?.writeText(logLines.join("\n"))}>Copy<//></div>
    <${LogView} lines=${logLines} live=${running} empty="No log output." />
  </article>`;
}
