// Background job tracking: start or attach to a job, stream its log into the dock, toast the result.
import { streamJob } from "./api.js";
import { setState, getState, toast, invalidate } from "./store.js";

const patch = (id, p) => setState(s => ({ jobs: s.jobs.map(j => j.id === id ? { ...j, ...p } : j) }));

export const JOB_TITLES = { "update-check": "Update check", "update-apply": "Apply updates", deploy: "Deploy", remove: "Remove", undo: "Undo" };
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
  return new Promise(resolve => {
    streamJob(id, {
      onLine: (l) => {
        const j = getState().jobs.find(x => x.id === id);
        if (j) patch(id, { lines: [...j.lines, l].slice(-800) });
      },
      onDone: (job) => {
        const st = job?.status || "done";
        patch(id, { status: st, job });
        const tone = jobTone(job || st);
        if (!quiet || tone !== "ok") toast({
          kind: tone === "ok" ? "ok" : "err",
          title: `${title || JOB_TITLES[job?.kind] || "Job"}${job?.dry_run ? " (dry run)" : ""} ${tone === "ok" ? "finished" : tone === "warn" ? "finished with errors" : st === "interrupted" ? "was interrupted" : "failed"}`,
          body: job?.summary,
          href: `#/activity/${id}`,
        });
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
