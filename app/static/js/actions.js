// Global actions shared by the shell, palette and views.
import { post } from "./api.js";
import { runJob } from "./jobs.js";
import { setState, getState } from "./store.js";

export const checkUpdates = () => runJob(post("/updates/check"), { title: "Update check" });

export const openUpdateAll = () => setState({ updateAll: true });

export function applyUpdates(items, { dryRun = false, title } = {}) {
  return runJob(post("/updates/apply", { items, dry_run: dryRun }), { title: title || (dryRun ? "Dry run" : "Apply updates") });
}

export const isChecking = () => getState().jobs.some(j => j.status === "running" && j.title === "Update check");
