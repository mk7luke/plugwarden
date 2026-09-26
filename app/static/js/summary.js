// One vocabulary for counts everywhere: plugins (unique), installs (server × plugin), servers.
import { plural, relTime } from "./fmt.js";

// /updates returns {updates, counts, last_check, check_summary}.
export const updatesOf = (d) => d?.updates || [];

// Does a release declare support for the server's Minecraft version?
export function compatOf(c, mc) {
  if (!c) return null;
  const list = c.mc_versions || [];
  const m = c.mc ?? mc;
  return { mc: m, supported: list, ok: c.ok ?? (!m || !list.length || list.includes(m)) };
}

// {plugins, installs, servers} of pending updates.
export const updateCounts = (ov, upsData) => upsData?.counts || ov?.totals?.updates || { plugins: 0, installs: 0, servers: 0 };

export const updateHeadline = (c) => c.plugins
  ? `${plural(c.plugins, "plugin")} ${c.plugins === 1 ? "has an update" : "have updates"}`
  : "Everything is up to date";

export const updateDetail = (c) => c.plugins ? `${plural(c.installs, "install")} on ${plural(c.servers, "server")}` : "";

// Servers needing a restart: [{server, since?, causes?}]
export const restartList = (ov) => ov?.restart_checklist || [];

// "46 of 78 jars identified · checked 4m ago" — the counts half of check_summary is shown by the headline.
export function checkLine(ov) {
  const identified = (ov?.check_summary || "").split(" · ").slice(1).join(" · ");
  return [identified, ov?.last_check ? `checked ${relTime(ov.last_check)}` : "never checked"].filter(Boolean).join(" · ");
}
