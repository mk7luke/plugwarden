// Background job tracking: start or attach to a job, stream its log into the dock, toast the result.
import { streamJob, get, post } from "./api.js";
import { setState, getState, toast, invalidate, confirmDialog } from "./store.js";
import { relTime, plural } from "./fmt.js";

const patch = (id, p) => setState(s => ({ jobs: s.jobs.map(j => j.id === id ? { ...j, ...p } : j) }));

export const JOB_TITLES = { "update-check": "Update check", "update-apply": "Apply updates", deploy: "Deploy", remove: "Remove", undo: "Undo" };
// Kinds this version has no title for still read as words: "config-sync" → "Config sync".
export const kindTitle = (k) => JOB_TITLES[k] || (k ? k[0].toUpperCase() + k.slice(1).replace(/-/g, " ") : "Job");
export const KIND_ICON = { "update-check": "refresh-cw", "update-apply": "circle-arrow-up", deploy: "rocket", remove: "trash-2", undo: "undo-2" };
export const ACTIVE = ["queued", "running"];
export const isActive = (st) => ACTIVE.includes(st);
// Tone for a job status: ok | danger | warn | run
export function jobTone(j) {
  const st = typeof j === "string" ? j : j?.status;
  if (isActive(st)) return "run";
  if (st === "failed" || st === "interrupted") return "danger";
  if (typeof j === "object" && (j.counts?.error || j.results?.some?.(r => r.outcome === "error"))) return "warn";
  return "ok";
}

export function jobVerb(j) {
  const t = jobTone(j);
  return t === "ok" ? (j.status === "undone" ? "was reverted" : "finished") : t === "warn" ? "finished with errors" : j.status === "interrupted" ? "was interrupted" : "failed";
}
// Reverted jobs: "Reverted 25m ago · was 1 changed" (the undo job id lives in the detail view).
export function jobSummary(j) {
  if (!j?.summary) return "";
  if (j.status === "undone" || j.undone_by) {
    const was = /was:\s*(.*)$/.exec(j.summary)?.[1];
    return `Reverted${j.undone_at ? " " + relTime(j.undone_at) : ""}${was ? ` · was ${was}` : ""}`;
  }
  return j.summary;
}
// "Apply updates · M5-kitpvp01" when a job touched exactly one server.
export const jobTitle = (j) => `${kindTitle(j.kind)}${j.servers?.length === 1 ? ` · ${j.servers[0]}` : ""}`;

export async function runJob(request, { title, onDone } = {}) {
  let res;
  try { res = await request; }
  catch (e) { toast({ kind: "err", title: `${title || "Job"} failed to start`, body: e.message }); return null; }
  return trackJob(res.job_id, { title, onDone });
}

export function trackJob(id, { title, onDone, quiet } = {}) {
  if (getState().jobs.some(j => j.id === id)) return null;
  const entry = { id, title: title || "Job", status: "running", lines: [], job: null, min: false };
  setState(s => ({ jobs: [entry, ...s.jobs.filter(j => isActive(j.status))].slice(0, 5) }));
  invalidate("/jobs", "/overview");
  // The stream carries log lines only; poll the job for progress {done, total} while it runs.
  const poll = setInterval(async () => {
    const cur = getState().jobs.find(j => j.id === id);
    if (!cur || !isActive(cur.status)) return clearInterval(poll);
    try { const j = await get(`/jobs/${encodeURIComponent(id)}`); if (j.progress) patch(id, { progress: j.progress }); } catch {}
  }, 1500);
  return new Promise(resolve => {
    streamJob(id, {
      onLine: (l) => {
        const j = getState().jobs.find(x => x.id === id);
        if (j) patch(id, { lines: [...j.lines, l].slice(-800) });
      },
      onDone: (job) => {
        clearInterval(poll);
        const st = job?.status || "done";
        patch(id, { status: st, job });
        const tone = jobTone(job || st);
        // The dock (or the inline execution panel) already shows the result; toast only when neither is visible.
        const shown = getState().jobs.some(j => j.id === id);
        if (!shown && (!quiet || tone !== "ok")) toast({
          kind: tone === "ok" ? "ok" : "err",
          title: `${title || kindTitle(job?.kind)}${job?.dry_run ? " (dry run)" : ""} ${jobVerb(job || { status: st })}`,
          body: job?.summary,
          href: `#/activity/${id}`,
        });
        // Most undos happen in the first minute: offer it for 10 s (also on "z") on the surface already showing the
        // result — the dock row, or the review sheet's own result panel — and only as a toast when neither is visible.
        if (UNDO_KINDS.has(job?.kind) && !job.dry_run && st === "done" && job.undoable) {
          if (shown) patch(id, { undoUntil: Date.now() + 10000 });
          else if (getState().inlineJob !== id) toast({
            kind: "ok", title: `${title || kindTitle(job.kind)} finished`, body: job.summary,
            timeout: 10000, countdown: 10, undo: true, href: `#/activity/${id}`,
            action: { label: "Undo", run: () => undoJob(job) },
          });
        }
        invalidate("/overview", "/servers", "/matrix", "/updates", "/jobs");
        onDone?.(job);
        resolve(job);
      },
      onError: (e) => { patch(id, { status: "failed" }); toast({ kind: "err", title: `${title}: lost connection`, body: e.message }); resolve(null); },
    });
  });
}

export const dismissJob = (id) => setState(s => ({ jobs: s.jobs.filter(j => j.id !== id) }));
export const toggleJobMin = (id) => setState(s => ({ jobs: s.jobs.map(j => j.id === id ? { ...j, min: !j.min } : j) }));

const UNDO_KINDS = new Set(["update-apply", "deploy", "remove"]);
// Restore a job's backups. Wide undos (more than 3 servers) still get the confirmation Activity uses.
export async function undoJob(job) {
  const servers = job.servers || [];
  if (servers.length > 3) {
    const ok = await confirmDialog({
      title: `Undo ${kindTitle(job.kind).toLowerCase()}?`, confirmLabel: "Restore backups", danger: true, list: servers,
      body: `Files changed by this job will be restored from its backups on ${plural(servers.length, "server")}. The undo itself is recorded as a new job.`,
    });
    if (!ok) return null;
  }
  return runJob(post(`/jobs/${encodeURIComponent(job.id)}/undo`), { title: `Undo ${jobTitle(job).replace(/^./, c => c.toLowerCase())}` });
}
