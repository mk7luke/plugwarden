// Fixture layer mirroring the /api/v2 contract. Enabled with ?fixtures=1.
// Data is modelled on the sandbox network so layouts get exercised with realistic names.

const now = Date.now();
const iso = (msAgo) => new Date(now - msAgo).toISOString();
const MIN = 60e3, HOUR = 3600e3, DAY = 86400e3;

const SERVERS = [
  ["elChapo01", "purpur", "1.21.6"],
  ["M0-proxy01", "velocity", null],
  ["M1-hub01", "purpur", "1.21.6"],
  ["M3-hunger01", "purpur", "1.21.6"],
  ["M4-skyblock01", "purpur", "1.21.6"],
  ["M5-kitpvp01", "paper", "1.21.6"],
  ["M6-creative01", "purpur", "1.21.6"],
  ["M7-bending01", "purpur", "1.21.6"],
  ["M8-lifesteal01", "purpur", "1.21.6"],
  ["M9-aerons-server01", "paper", "1.21.4"],
  ["M9-homestead01", "fabric", "1.21.1"],
];

// key: [name, latest, source kind, source id]
const CATALOG = {
  coreprotect: ["CoreProtect", "23.4", "modrinth", "coreprotect"],
  essentialsx: ["EssentialsX", "2.22.0", "github", "EssentialsX/Essentials"],
  essentialsxchat: ["EssentialsXChat", "2.22.0", "github", "EssentialsX/Essentials"],
  essentialsxspawn: ["EssentialsXSpawn", "2.22.0", "github", "EssentialsX/Essentials"],
  luckperms: ["LuckPerms", "5.5.23", "modrinth", "luckperms"],
  viaversion: ["ViaVersion", "5.12.0", "modrinth", "viaversion"],
  viabackwards: ["ViaBackwards", "5.12.0", "modrinth", "viabackwards"],
  placeholderapi: ["PlaceholderAPI", "2.12.3", "hangar", "HelpChat/PlaceholderAPI"],
  decentholograms: ["DecentHolograms", "2.10.2", "modrinth", "decentholograms"],
  fastasyncworldedit: ["FastAsyncWorldEdit", "2.14.3-SNAPSHOT-1234", "modrinth", "fastasyncworldedit"],
  gsit: ["GSit", "3.6.0", "modrinth", "gsit"],
  voicechat: ["Simple Voice Chat", "2.6.8", "modrinth", "simple-voice-chat"],
  plan: ["Plan", "5.8-build-3638", "github", "plan-player-analytics/Plan"],
  worldguard: ["WorldGuard", "7.0.14", "modrinth", "worldguard"],
  discordsrv: ["DiscordSRV", "1.30.5", "modrinth", "discordsrv"],
  chestsort: ["ChestSort", "14.2.0", "spiget", "59773"],
  papiproxybridge: ["PAPIProxyBridge", "1.8.4", "modrinth", "papiproxybridge"],
  interactivechat: ["InteractiveChat", "2026.1.1.0", "spiget", "75870"],
  plugmanx: ["PlugManX", "3.0.3", "modrinth", "plugmanx"],
  protocollib: ["ProtocolLib", null, null, null],
  vault: ["Vault", null, null, null],
  jarvis: ["Jarvis", null, null, null],
  citizens: ["Citizens", null, null, null],
  vivecraft: ["Vivecraft-Spigot-Extension", "1.3.15-1", "modrinth", "vivecraft"],
  bentobox: ["BentoBox", "3.10.2", "modrinth", "bentobox"],
  plotsquared: ["PlotSquared", "7.5.11", null, null],
  hungergames: ["HungerGames", "5.2.8", "spiget", "65942"],
  combatlogx: ["CombatLogX", null, null, null],
  lifesteal: ["LifeSteal", null, null, null],
  geyser: ["Geyser", "2.8.3", "github", "GeyserMC/Geyser"],
  floodgate: ["floodgate", "2.2.4", "github", "GeyserMC/Floodgate"],
  skinsrestorer: ["SkinsRestorer", "15.7.2", "modrinth", "skinsrestorer"],
  viarewind: ["ViaRewind", "4.1.2", "modrinth", "viarewind"],
  litebans: ["LiteBans", null, null, null],
  maintenance: ["Maintenance", "4.3.0", "modrinth", "maintenance"],
  velocitab: ["Velocitab", "1.7.6", "modrinth", "velocitab"],
  serverlistplus: ["ServerListPlus", "3.5.0", null, null],
  nuvotifier: ["NuVotifier", null, null, null],
  axgraves: ["AxGraves", "1.26.0", "modrinth", "axgraves"],
  axiompaper: ["AxiomPaper", "5.0.1", "modrinth", "axiom"],
  imageframe: ["ImageFrame", "1.9.0.0", "spiget", "106031"],
};

const COMMON = [
  ["chestsort", "ChestSort-14.2.0.jar", "14.2.0"],
  ["citizens", "Citizens.jar", "2.0.37"],
  ["coreprotect", "CoreProtect-CE-23.1.jar", "23.1"],
  ["decentholograms", "DecentHolograms-2.10.1.jar", "2.10.1"],
  ["discordsrv", "DiscordSRV-Build-1.30.5.jar", "1.30.5"],
  ["essentialsx", "EssentialsX-2.22.0.jar", "2.22.0"],
  ["essentialsxchat", "EssentialsXChat-2.22.0.jar", "2.22.0"],
  ["essentialsxspawn", "EssentialsXSpawn-2.22.0.jar", "2.22.0"],
  ["fastasyncworldedit", "FastAsyncWorldEdit-Paper-2.14.3-SNAPSHOT-1231.jar", "2.14.3-SNAPSHOT-1231"],
  ["gsit", "GSit-3.5.1.jar", "3.5.1"],
  ["interactivechat", "InteractiveChat-2026.1.1.0.jar", "2026.1.1.0"],
  ["jarvis", "Jarvis-1.1.1.jar", "1.1.1"],
  ["luckperms", "LuckPerms-Bukkit-5.5.21.jar", "5.5.21"],
  ["papiproxybridge", "PAPIProxyBridge-Bukkit-1.8.4.jar", "1.8.4"],
  ["placeholderapi", "PlaceholderAPI-2.12.3.jar", "2.12.3"],
  ["plan", "Plan-5.8-build-3638.jar", "5.8-build-3638"],
  ["plugmanx", "PlugManX-3.0.2.jar", "3.0.2"],
  ["protocollib", "ProtocolLib.jar", "5.4.0"],
  ["vault", "Vault.jar", "1.7.3"],
  ["viabackwards", "ViaBackwards-5.11.0.jar", "5.11.0"],
  ["viaversion", "ViaVersion-5.11.0.jar", "5.11.0"],
  ["vivecraft", "Vivecraft-Spigot-Extension-1.3.15-1.jar", "1.3.15-1"],
  ["voicechat", "voicechat-bukkit-2.6.6.jar", "2.6.6"],
  ["worldguard", "worldguard-bukkit-7.0.14-dist.jar", "7.0.14"],
];
const without = (...keys) => COMMON.filter(p => !keys.includes(p[0]));

const INSTALLED = {
  "elChapo01": [
    ...COMMON.map(p => p[0] === "coreprotect" ? ["coreprotect", "CoreProtect-23.4b.jar", "23.4"]
      : p[0] === "fastasyncworldedit" ? ["fastasyncworldedit", "FastAsyncWorldEdit-Paper-2.14.3-SNAPSHOT-1234.jar", "2.14.3-SNAPSHOT-1234"] : p),
    ["axgraves", "AxGraves-1.25.0.jar", "1.25.0"], ["axiompaper", "AxiomPaper-5.0.1-for-MC1.21.5.jar", "5.0.1"],
    ["combatlogx", "CombatLogX.jar", "11.5.0"], ["imageframe", "ImageFrame-1.9.0.0.jar", "1.9.0.0"],
  ],
  "M0-proxy01": [
    ["floodgate", "floodgate-velocity.jar", "2.2.4"], ["geyser", "Geyser-Velocity.jar", "2.8.2"],
    ["litebans", "LiteBans.jar", "2.17.1"], ["luckperms", "LuckPerms-Velocity-5.5.21.jar", "5.5.21"],
    ["maintenance", "Maintenance-Velocity-4.3.0.jar", "4.3.0"], ["nuvotifier", "nuvotifier.jar", "2.7.3"],
    ["papiproxybridge", "PAPIProxyBridge-Velocity-1.8.4.jar", "1.8.4"], ["plan", "Plan-5.8-build-3638.jar", "5.8-build-3638"],
    ["serverlistplus", "ServerListPlus-3.5.0-Universal.jar", "3.5.0"], ["velocitab", "Velocitab-1.7.5.jar", "1.7.5"],
  ],
  "M1-hub01": COMMON,
  "M3-hunger01": [...without("citizens"), ["hungergames", "HungerGames-5.2.8.jar", "5.2.8"], ["nuvotifier", "nuvotifier.jar", "2.7.3"]],
  "M4-skyblock01": [...COMMON, ["bentobox", "BentoBox-3.10.1.jar", "3.10.1"]],
  "M5-kitpvp01": without("citizens"),
  "M6-creative01": [...without("citizens"), ["plotsquared", "plotsquared-bukkit-7.5.11-Premium.jar", "7.5.11"]],
  "M7-bending01": without("citizens", "chestsort", "coreprotect", "decentholograms", "gsit", "jarvis", "worldguard"),
  "M8-lifesteal01": [...without("worldguard"), ["combatlogx", "CombatLogX.jar", "11.5.0"], ["lifesteal", "LifeSteal-3.jar", "3"]],
  "M9-aerons-server01": [
    ["essentialsx", "EssentialsX-2.22.0.jar", "2.22.0"], ["essentialsxchat", "EssentialsXChat-2.21.2.jar", "2.21.2"],
    ["floodgate", "floodgate-spigot.jar", "2.2.4"], ["geyser", "Geyser-Spigot.jar", "2.8.2"],
    ["luckperms", "LuckPerms-Bukkit-5.5.21.jar", "5.5.21"], ["protocollib", "ProtocolLib.jar", "5.4.0"],
    ["skinsrestorer", "SkinsRestorer.jar", "15.7.2"], ["vault", "Vault.jar", "1.7.3"],
    ["viabackwards", "ViaBackwards-5.10.0.jar", "5.10.0"], ["viarewind", "ViaRewind-4.1.2.jar", "4.1.2"],
    ["viaversion", "ViaVersion-5.10.0.jar", "5.10.0"],
  ],
  "M9-homestead01": [],
};

const settings = {
  groups: {
    "All game servers": SERVERS.filter(s => !["velocity", "fabric"].includes(s[1]) && s[0] !== "elChapo01").map(s => s[0]),
    "M1–M8": ["M1-hub01", "M3-hunger01", "M4-skyblock01", "M5-kitpvp01", "M6-creative01", "M7-bending01", "M8-lifesteal01"],
    "PvP": ["M5-kitpvp01", "M8-lifesteal01"],
  },
  default_source: "elChapo01",
  auto_update: { mode: "notify", interval_hours: 6, window: "04:00-06:00", dry_run_first: true },
  pins: { worldguard: { servers: "*", version: "7.0.14" } },
  ignores: { jarvis: { servers: "*" } },
  source_map: { chestsort: { kind: "spiget", id: "59773" }, interactivechat: { kind: "spiget", id: "75870" } },
};

function hash(s) { let h = 2166136261; for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return (h >>> 0).toString(16).padStart(8, "0"); }

const holds = (h, server) => !!h && (h.servers === "*" || h.servers.includes(server));
function statusOf(key, version, server) {
  if (holds(settings.ignores[key], server)) return "ignored";
  if (holds(settings.pins[key], server)) return "pinned";
  const c = CATALOG[key];
  if (!c || !c[1]) return "unknown";
  return c[1] === version ? "current" : "outdated";
}

function pluginsFor(id) {
  return (INSTALLED[id] || []).map(([key, jar, version]) => {
    const c = CATALOG[key] || [key, null];
    const st = statusOf(key, version, id);
    return {
      key, name: c[0], jar, version, sha1: hash(jar) + hash(jar + "x") + hash(jar + "y"),
      folder: c[0].replace(/\s/g, ""), size: 40000 + (parseInt(hash(jar), 16) % 9000000),
      mtime: iso((parseInt(hash(jar), 16) % 90) * DAY),
      source: c[2] ? { kind: c[2], id: c[3], url: `https://${c[2]}.example/${c[3]}` } : null,
      latest: c[1] ? { version: c[1], url: "#", download_url: "#", published: iso(3 * DAY), changelog_url: `https://example.org/${key}/changelog` } : null,
      status: st, pin_scope: settings.pins[key]?.servers ?? null, ignore_scope: settings.ignores[key]?.servers ?? null,
      pinned_version: holds(settings.pins[key], id) ? settings.pins[key].version : null,
    };
  });
}

function driftKeys() {
  const vers = {};
  for (const [id] of SERVERS) for (const [key, , v] of INSTALLED[id]) (vers[key] ||= new Set()).add(v);
  return new Set(Object.keys(vers).filter(k => vers[k].size > 1));
}

function server(id, platform, mc) {
  const ps = pluginsFor(id); const dk = driftKeys();
  return {
    id, platform, mc_version: mc, plugin_count: ps.length,
    updates: ps.filter(p => p.status === "outdated").length,
    drift: ps.filter(p => dk.has(p.key)).length,
    pending_restart: pendingRestart.has(id),
    eligible_target: !["velocity", "fabric"].includes(platform),
  };
}
const servers = () => SERVERS.map(s => server(...s));

function updates() {
  const by = {};
  for (const [id] of SERVERS) for (const p of pluginsFor(id)) {
    if (p.status !== "outdated") continue;
    const u = by[p.key] ||= { key: p.key, name: p.name, from_versions: [], to_version: p.latest.version, servers: [], targets: [], changelog_url: p.latest.changelog_url, download_url: "#" };
    if (!u.from_versions.includes(p.version)) u.from_versions.push(p.version);
    u.servers.push(id);
    u.targets.push({ server: id, jar: p.jar, from: p.version, to: p.latest.version, compat: { mc_versions: p.key === "plugmanx" ? ["1.21.4", "1.21.5"] : ["1.21.4", "1.21.5", "1.21.6", "1.21.8"] } });
  }
  return Object.values(by).sort((a, b) => b.servers.length - a.servers.length);
}

function matrix() {
  const dk = driftKeys();
  const rows = {};
  for (const [id] of SERVERS) for (const p of pluginsFor(id)) {
    const r = rows[p.key] ||= { key: p.key, name: p.name, cells: {} };
    r.cells[id] = { version: p.version, jar: p.jar, status: p.status === "current" && dk.has(p.key) ? "drift" : p.status };
  }
  return { servers: SERVERS.map(s => s[0]), plugins: Object.values(rows).sort((a, b) => a.name.localeCompare(b.name)) };
}

let jobSeq = 40;
const jobs = [
  { id: "j-39", kind: "update-apply", status: "done", user: "luke@interactep.com", started: iso(2 * HOUR + 3 * MIN), finished: iso(2 * HOUR), summary: "Updated LuckPerms 5.5.20 → 5.5.21 on 9 servers", undoable: true,
    results: ["M1-hub01", "M3-hunger01", "M4-skyblock01", "M5-kitpvp01", "M6-creative01", "M7-bending01", "M8-lifesteal01", "M9-aerons-server01", "M0-proxy01"].map(s => ({ server: s, item: "LuckPerms", action: "replace", outcome: "changed", detail: "5.5.20 → 5.5.21" })),
    log: "$ update LuckPerms --to 5.5.21\n[stage] downloaded LuckPerms-Bukkit-5.5.21.jar (sha1 ok)\n[M1-hub01] backup → backups/j-39/M1-hub01/LuckPerms-Bukkit-5.5.20.jar\n[M1-hub01] replaced LuckPerms-Bukkit-5.5.20.jar → LuckPerms-Bukkit-5.5.21.jar\n...\ndone: 9 changed, 0 failed\n" },
  { id: "j-38", kind: "deploy", status: "done", user: "mod.ops@interactep.com", started: iso(20 * HOUR), finished: iso(20 * HOUR - 40e3), summary: "Synced Essentials/config.yml to 7 servers (1 failed)", undoable: true,
    results: [["M1-hub01", "changed"], ["M3-hunger01", "changed"], ["M4-skyblock01", "changed"], ["M5-kitpvp01", "changed"], ["M6-creative01", "failed"], ["M7-bending01", "changed"], ["M8-lifesteal01", "changed"]].map(([s, o]) => ({ server: s, item: "Essentials/config.yml", action: "sync", outcome: o === "failed" ? "error" : o, detail: o === "failed" ? "permission denied" : "updated (4.1 KB)" })),
    log: "$ rsync -a --itemize-changes elChapo01/Essentials/config.yml → 7 targets\n>f.st...... Essentials/config.yml  (M1-hub01)\n...\nERROR M6-creative01: rsync: open \"Essentials/config.yml\": Permission denied (13)\n" },
  { id: "j-37", kind: "update-check", status: "done", user: "auto", started: iso(26 * HOUR), finished: iso(26 * HOUR - 12e3), summary: "Checked 61 plugins — 9 updates available", undoable: false, results: [], log: "modrinth: 44 hashes resolved\nhangar: 1 mapped\nspiget: 2 mapped\ngithub: 3 mapped\n9 updates available\n" },
  { id: "j-36", kind: "remove", status: "done", user: "luke@interactep.com", started: iso(3 * DAY), finished: iso(3 * DAY - 9e3), summary: "Removed TreeCuter from M4-skyblock01", undoable: true,
    results: [{ server: "M4-skyblock01", item: "TreeCuter-v2.0.5.jar", action: "delete", outcome: "changed", detail: "removed jar + folder" }], log: "deleting TreeCuter-v2.0.5.jar\ndeleting TreeCuter/\n" },
  { id: "j-35", kind: "undo", status: "failed", user: "luke@interactep.com", started: iso(4 * DAY), finished: iso(4 * DAY - 2e3), summary: "Undo of j-33 failed — backups expired", undoable: false,
    results: [], log: "ERROR: backup set j-33 not found in STATE_DIR/backups\n" },
];

const TREE = {
  "": [
    ...INSTALLED.elChapo01.map(([k, jar]) => ({ name: jar, type: "file", jar: true, size: 40000 + (parseInt(hash(jar), 16) % 9000000), mtime: iso(5 * DAY) })),
    ...["Essentials", "LuckPerms", "CoreProtect", "DiscordSRV", "WorldGuard", "PlaceholderAPI", "DecentHolograms", "ViaVersion", "GSit", "Plan", "voicechat", "bStats"].map(n => ({ name: n, type: "dir", size: null, mtime: iso(2 * DAY) })),
  ],
  "Essentials": [["config.yml", 48211], ["worth.yml", 22019], ["kits.yml", 3412], ["messages_en.properties", 61022], ["userdata", null], ["spawn.yml", 322]].map(([n, s]) => ({ name: n, type: s == null ? "dir" : "file", size: s, mtime: iso(DAY) })),
  "LuckPerms": [["config.yml", 31077], ["libs", null], ["translations", null]].map(([n, s]) => ({ name: n, type: s == null ? "dir" : "file", size: s, mtime: iso(DAY) })),
};

function delay(v, ms = 280) { return new Promise(r => setTimeout(() => r(structuredClone(v)), ms)); }

function newJob(kind, summary, results = []) {
  const id = `j-${++jobSeq}`;
  const j = { id, kind, status: "running", user: "luke@interactep.com", created: new Date().toISOString(), started: new Date().toISOString(), finished: null, summary, results, log: "", undoable: false, dry_run: false, servers: [...new Set(results.map(r => r.server))] };
  jobs.unshift(j);
  return j;
}

function plan(body) {
  const items = [...(body.items?.jars || []), ...(body.items?.folders || []).map(f => f + "/"), ...(body.items?.paths || []), ...(body.items?.uploads || []).map(u => uploads[u]?.name || u)];
  if (!items.length) throw Object.assign(new Error("select at least one item"), { status: 400 });
  if ((body.targets || []).includes("M0-proxy01")) throw Object.assign(new Error("M0-proxy01 is a velocity server; it cannot receive items from elChapo01 (purpur)"), { status: 400 });
  const results = [];
  for (const t of body.targets || []) {
    const inst = new Set((INSTALLED[t] || []).map(p => p[1]));
    items.forEach((it, i) => {
      const isJar = it.endsWith(".jar");
      let outcome = "changed", detail = "";
      if (body.action === "sync" && isJar && !inst.has(it) && !body.options?.install) { outcome = "skipped"; detail = "not installed on target"; }
      else if (body.action === "delete") { outcome = inst.has(it) || !isJar ? "changed" : "skipped"; detail = outcome === "changed" ? "would delete" : "not present"; }
      else if (body.action === "replace") detail = `would replace ${it.replace(/[-_]v?\d[\w.\-]*\.jar$/, "-*.jar")}`;
      else if ((i + t.length) % 3 === 0) { outcome = "unchanged"; detail = "identical"; }
      else detail = isJar ? "would update jar" : "would update (4.1 KB)";
      results.push({ server: t, item: it, action: body.action, outcome, detail });
    });
  }
  const summary = results.reduce((a, r) => (a[r.outcome] = (a[r.outcome] || 0) + 1, a), { changed: 0, unchanged: 0, skipped: 0, error: 0 });
  return { action: body.action, source: body.source, targets: body.targets, results, summary };
}

const uploads = {};
const plans = {};
const pendingRestart = new Set(["M1-hub01", "M4-skyblock01"]);

function updatePlan(items) {
  const want = (key, server) => items === "all" || !items || items.some(i => i.key === key && (!i.servers || i.servers.includes(server)));
  const rows = [];
  for (const [id, , mc] of SERVERS) for (const p of pluginsFor(id)) {
    if (p.status !== "outdated" || !want(p.key, id)) continue;
    const to = p.latest.version;
    rows.push({
      row: rows.length, server: id, key: p.key, name: p.name, action: "update", from_version: p.version, to_version: to,
      from_jar: p.jar, to_jar: p.jar.includes(p.version) ? p.jar.replace(p.version, to) : `${p.name.replace(/\s/g, "")}-${to}.jar`,
      size: p.size, verified: true, changelog_url: p.latest.changelog_url, published: p.latest.published,
      compat: { mc_versions: p.key === "plugmanx" ? ["1.21.4", "1.21.5"] : ["1.21.4", "1.21.5", "1.21.6", "1.21.8"], loaders: ["paper", "purpur"] },
    });
  }
  const plan_id = "pl" + (++jobSeq);
  plans[plan_id] = { rows };
  return { plan_id, created: new Date().toISOString(), expires: new Date(Date.now() + 30 * MIN).toISOString(), rows, skipped: [],
    summary: { rows: rows.length, plugins: new Set(rows.map(r => r.key)).size, servers: new Set(rows.map(r => r.server)).size, download_bytes: rows.reduce((a, r) => a + r.size, 0), unverified: 0 } };
}

function search(q) {
  q = q.toLowerCase();
  const out = [];
  for (const [dir, entries] of Object.entries(TREE)) for (const e of entries) {
    const path = dir ? `${dir}/${e.name}` : e.name;
    if (path.toLowerCase().includes(q)) out.push({ path, name: e.name, type: e.type, jar: e.jar, size: e.size });
  }
  for (const d of ["DiscordSRV", "WorldGuard", "PlaceholderAPI", "CoreProtect", "GSit", "Plan"]) if (`${d}/config.yml`.toLowerCase().includes(q)) out.push({ path: `${d}/config.yml`, name: "config.yml", type: "file", size: 9000 });
  return { results: out.slice(0, 100) };
}

export async function handle(method, path, body) {
  const [p, qs = ""] = path.split("?");
  const q = new URLSearchParams(qs);
  let m;
  if (method === "GET") {
    if (p === "/overview") {
      const ss = servers(); const m = matrix();
      const u = updates();
      return delay({ servers: ss, totals: { servers: ss.length, plugins: m.plugins.length, updates: { plugins: u.length, installs: u.reduce((a, x) => a + x.servers.length, 0), servers: new Set(u.flatMap(x => x.servers)).size }, drift: [...driftKeys()].length },
        last_check: iso(26 * HOUR), check_summary: `${u.length} plugins outdated · 46 of 78 jars identified`,
        ...{},
      restart_checklist: [...pendingRestart].map(sv => ({ server: sv, since: iso(2 * HOUR), jobs: [{ job_id: "j-39", kind: "update-apply", summary: "Updated LuckPerms", at: iso(2 * HOUR) }] })), auto_update: { mode: "apply", next_run: new Date(now + 3 * HOUR + 12 * MIN).toISOString(), effective_canary: "elChapo01",
          canary: [{ key: "bukkit:coreprotect", name: "CoreProtect", version: "23.4", server: "elChapo01", soak_hours_left: 9, canary_health: { status: "healthy", reason: "Enabling CoreProtect v23.4 logged", excerpt: [], log: "latest.log", checked_at: iso(20 * MIN) } }],
          held: [{ key: "bukkit:plugmanx", name: "PlugManX", version: "3.0.3", server: "elChapo01", at: iso(3 * HOUR), reason: "Error occurred while enabling PlugManX v3.0.3", excerpt: ["[12:04:11 ERROR]: Error occurred while enabling PlugManX v3.0.3 (Is it up to date?)", "java.lang.NoClassDefFoundError: com/…/PaperPluginManager"] }] }, user: "luke@interactep.com" });
    }
    if (p === "/servers") return delay(servers());
    if ((m = p.match(/^\/servers\/([^/]+)\/plugins$/))) {
      const id = decodeURIComponent(m[1]);
      if (!INSTALLED[id]) throw Object.assign(new Error("Unknown server"), { status: 404 });
      return delay(pluginsFor(id));
    }
    if ((m = p.match(/^\/servers\/([^/]+)\/tree$/))) {
      const path = q.get("path") || "";
      return delay({ path, entries: TREE[path] || [{ name: "config.yml", type: "file", size: 1200, mtime: iso(DAY) }] }, 160);
    }
    if (p === "/matrix") return delay(matrix());
    if ((m = p.match(/^\/servers\/([^/]+)\/search$/))) return delay(search(q.get("q") || ""), 200);
    if (p === "/diff") return delay({ path: q.get("path"), source: q.get("source"), target: q.get("target"), source_exists: true, target_exists: true, identical: false, binary: false, too_large: false,
      diff: "--- a\n+++ b\n@@ -12,5 +12,5 @@\n # Essentials config\n ops-name-color: '4'\n-nickname-prefix: '~'\n+nickname-prefix: ''\n max-nick-length: 15\n@@ -88,3 +88,4 @@\n teleport-cooldown: 0\n-teleport-delay: 3\n+teleport-delay: 0\n+teleport-safety: true" }, 250);
    if (p === "/updates") { const u = updates(); return delay({ updates: u, counts: { plugins: u.length, installs: u.reduce((a, x) => a + x.servers.length, 0), servers: new Set(u.flatMap(x => x.servers)).size }, last_check: iso(26 * HOUR), check_summary: `${u.length} plugins outdated · 46 of 78 jars identified` }); }
    if (p === "/jobs") return delay(jobs.map(({ log, results, ...j }) => ({ ...j, servers: [...new Set(results.map(r => r.server))], counts: results.reduce((a, r) => (a[r.outcome] = (a[r.outcome] || 0) + 1, a), {}) })));
    if ((m = p.match(/^\/jobs\/([^/]+)$/))) return delay(jobs.find(j => j.id === m[1]), 120);
    if (p === "/settings") return delay(settings);
    if ((m = p.match(/^\/servers\/([^/]+)\/health$/))) return delay({ server: decodeURIComponent(m[1]), since: q.get("since"), checked_at: iso(0), logs: ["latest.log"], startup_complete: true, restarted: true, restarted_at: (now - 20 * MIN) / 1000,
      counts: { healthy: 1, failed: 1, unknown: 0 },
      plugins: [{ key: "bukkit:plugmanx", name: "PlugManX", version: "3.2.1", status: "failed", reason: "Error occurred while enabling PlugManX v3.2.1", excerpt: ["[ERROR] Error occurred while enabling PlugManX v3.2.1 (Is it up to date?)", "java.lang.NoSuchMethodError: 'void org.bukkit…'"], log: "latest.log" },
        { key: "bukkit:coreprotect", name: "CoreProtect", version: "24.1", status: "healthy", reason: "Enabling CoreProtect v24.1", excerpt: [], log: "latest.log" }],
      preexisting_errors: [{ key: "bukkit:voicechat", name: "voicechat", reason: "Failed to bind UDP port 24454 (address already in use)", excerpt: ["[WARN] [voicechat] Failed to bind to 0.0.0.0:24454 — java.net.BindException: Address already in use"], log: "latest.log", seen_in_runs: 4 }] }, 400);
    if (p === "/access-log") return delay({ entries: [{ at: iso(3 * MIN), user: "luke@interactep.com", action: "diff", servers: ["elChapo01", "M1-hub01"], path: "LuckPerms/config.yml", detail: "2 value(s) redacted" }] });
  }
  if (method === "PUT" && p === "/settings") { Object.assign(settings, body); return delay(settings); }
  if (method === "POST") {
    if (p === "/deploy/plan") { const pl = plan(body); const plan_id = "dp" + (++jobSeq); plans[plan_id] = { deploy: body, pl }; return delay({ plan_id, ...pl }, 420); }
    if (p === "/updates/plan") return delay(updatePlan(body.items), 500);
    if (p === "/updates/apply" && body.plan_id) {
      const pl = plans[body.plan_id];
      if (!pl) throw Object.assign(new Error("plan expired — build a new one"), { status: 409 });
      const ex = new Set((body.exclude || []).map(([sv, k]) => `${sv}|${k}`));
      const rows = pl.rows.filter(r => !ex.has(`${r.server}|${r.key}`));
      const j = newJob("update-apply", `${rows.length} changed`, rows.map(r => ({ server: r.server, key: r.key, item: r.to_jar, action: "update", outcome: "changed", detail: `${r.from_jar} → ${r.to_jar}` })));
      j.restart_servers = [...new Set(rows.map(r => r.server))];
      j.restart_servers.forEach(sv => pendingRestart.add(sv));
      return delay({ job_id: j.id });
    }
    if (p === "/updates/check") return delay({ job_id: newJob("update-check", "Checking for updates…").id });
    if (p === "/updates/apply") {
      const items = body.items === "all" ? updates().map(u => ({ key: u.key, servers: u.servers })) : body.items;
      const n = items.reduce((a, i) => a + i.servers.length, 0);
      const j = newJob("update-apply", `${body.dry_run ? "Dry run: " : ""}Update ${items.length} plugin(s) across ${n} install(s)`,
        items.flatMap(i => i.servers.map(s => ({ server: s, item: CATALOG[i.key]?.[0] || i.key, action: "replace", outcome: "changed", detail: `→ ${CATALOG[i.key]?.[1]}` }))));
      j.dry_run = !!body.dry_run;
      return delay({ job_id: j.id });
    }
    if (p === "/deploy") {
      if (!plans[body.plan_id]?.deploy) throw Object.assign(new Error("plan expired — preview again"), { status: 409 });
      body = plans[body.plan_id].deploy;
      const pl = plan(body);
      const j = newJob("deploy", `${pl.summary.changed} changed, ${pl.summary.unchanged} unchanged`,
        pl.results.map(r => ({ ...r, detail: r.detail.replace(/^would /, "") })));
      return delay({ job_id: j.id });
    }
    if (p === "/upload") {
      const f = body.get("file"); const id = "u" + (++jobSeq);
      const name = f?.name || "Plugin.jar";
      const mm = name.replace(/\.jar$/, "").match(/^(.*?)[-_]v?(\d[\w.\-]*)$/);
      uploads[id] = { upload_id: id, name, plugin_name: mm ? mm[1] : name.replace(/\.jar$/, ""), version: mm ? mm[2] : null };
      return delay(uploads[id], 600);
    }
    if ((m = p.match(/^\/plugins\/([^/]+)\/pin$/))) { const k = decodeURIComponent(m[1]); body.version ? settings.pins[k] = body.version : delete settings.pins[k]; return delay(settings); }
    if ((m = p.match(/^\/plugins\/([^/]+)\/ignore$/))) { const k = decodeURIComponent(m[1]); settings.ignores = body.ignored ? [...new Set([...settings.ignores, k])] : settings.ignores.filter(x => x !== k); return delay(settings); }
    if ((m = p.match(/^\/plugins\/([^/]+)\/remove$/))) return delay({ job_id: newJob("remove", `Remove ${decodeURIComponent(m[1])}`, (body.servers || []).map(sv => ({ server: sv, item: decodeURIComponent(m[1]), action: "delete", outcome: "changed", detail: "removed" }))).id });
    if ((m = p.match(/^\/servers\/([^/]+)\/restarted$/))) { pendingRestart.delete(decodeURIComponent(m[1])); return delay({ ok: true }); }
    if ((m = p.match(/^\/jobs\/([^/]+)\/undo$/))) return delay({ job_id: newJob("undo", `Undo ${m[1]}`).id });
  }
  const e = new Error(`Fixture: no handler for ${method} ${path}`); e.status = 404; throw e;
}

export function stream(id, onLine, onDone) {
  const j = jobs.find(x => x.id === id);
  const lines = j.results.length
    ? [`$ job ${id} (${j.kind})`, ...j.results.map(r => `[${r.server}] ${r.action} ${r.item}: ${r.outcome}${r.detail ? " — " + r.detail : ""}`), `done: ${j.results.length} result(s)`]
    : [`$ job ${id} (${j.kind})`, "modrinth: resolving 61 hashes…", "modrinth: 44 matched", "hangar/spiget/github: 5 mapped", "9 updates available", "done"];
  let i = 0;
  const tick = () => {
    if (i < lines.length) { j.log += lines[i] + "\n"; onLine(lines[i++]); setTimeout(tick, 220); }
    else { j.status = "done"; j.finished = new Date().toISOString(); j.undoable = !j.dry_run && j.kind !== "update-check"; if (j.kind === "update-check") j.summary = "Checked 61 plugins — 9 updates available"; onDone(structuredClone(j)); }
  };
  setTimeout(tick, 200);
}
