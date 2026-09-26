// Global actions shared by the shell, palette and views.
import { post } from "./api.js";
import { runJob } from "./jobs.js";
import { openChangeset } from "./components/changeset.js";

export const checkUpdates = () => runJob(post("/updates/check"), { title: "Update check" });

export const openUpdateAll = () => openChangeset("all", "Review: update everything");
