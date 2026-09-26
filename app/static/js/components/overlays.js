// Toasts, confirm dialog, command palette and job dock.
import { html, useState, useEffect, useRef, useMemo } from "../lib.js";
import { useStore, setState, dismissToast, peek, prefetch, setTheme, getState } from "../store.js";
import { searchFiles } from "../api.js";
import { openChangeset } from "./changeset.js";
import { openRemove } from "./removedialog.js";
import { navigate } from "../router.js";
import { Icon, Btn, Kbd, modKey } from "./ui.js";
import { dismissJob, toggleJobMin, isActive, jobTone } from "../jobs.js";
import { plural } from "../fmt.js";

// ---------- focus trap ----------
function useTrap(ref, onEscape) {
  useEffect(() => {
    const prev = document.activeElement;
    const el = ref.current;
    const sel = 'button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';
    const auto = el?.querySelector("[data-autofocus]") || el?.querySelector(sel);
    auto?.focus();
    const key = (e) => {
      if (e.key === "Escape") { e.preventDefault(); onEscape(); }
      if (e.key === "Tab" && el) {
        const f = [...el.querySelectorAll(sel)];
        if (!f.length) return;
        if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
        else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
      }
    };
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("keydown", key); prev?.focus?.(); };
  }, []);
}

// ---------- toasts ----------
export function Toasts() {
  const toasts = useStore(s => s.toasts);
  const docked = useStore(s => s.jobs.some(j => j.id !== s.inlineJob));
  const icon = { ok: ["circle-check", "t-ok"], err: ["circle-x", "t-err"], info: ["info", "t-info"] };
  return html`<div class=${"toasts" + (docked ? " shift" : "")} role="region" aria-label="Notifications" aria-live="polite">
    ${toasts.map(t => { const [n, c] = icon[t.kind] || icon.info; return html`<div class="toast" key=${t.id} role=${t.kind === "err" ? "alert" : "status"}>
      <${Icon} n=${n} cls=${c} />
      <div class="grow"><b>${t.title}</b>${t.body && html`<p>${t.body}</p>`}
        ${(t.href || t.action) && html`<div class="t-actions">
          ${t.href && html`<a class="btn btn-sm" href=${t.href} onClick=${() => dismissToast(t.id)}>View details</a>`}
          ${t.action && html`<${Btn} size="sm" onClick=${() => { t.action.run(); dismissToast(t.id); }}>${t.action.label}<//>`}</div>`}
      </div>
      <${Btn} kind="ghost" size="sm" icon="x" aria-label="Dismiss" onClick=${() => dismissToast(t.id)} />
    </div>`; })}
  </div>`;
}

// ---------- confirm dialog ----------
export function ConfirmHost() {
  const c = useStore(s => s.confirm);
  return c ? html`<${Confirm} key=${c.title} c=${c} />` : null;
}
function Confirm({ c }) {
  const [typed, setTyped] = useState("");
  const [checked, setChecked] = useState(!!c.check?.value);
  const ref = useRef();
  const close = (v) => { if (c.check) c.check.value = checked; setState({ confirm: null }); c.resolve(v); };
  useTrap(ref, () => close(false));
  const need = c.typeToConfirm;
  const ok = !need || typed.trim() === need;
  return html`<div class="scrim" onClick=${() => close(false)}></div>
  <div class="dialog" role="alertdialog" aria-modal="true" aria-labelledby="dlg-t" aria-describedby="dlg-d" ref=${ref}>
    <form onSubmit=${e => { e.preventDefault(); ok && close(true); }}>
      <div class="dialog-body">
        <h2 id="dlg-t">${c.danger && html`<${Icon} n="triangle-alert" />`}${c.title}</h2>
        <p id="dlg-d">${c.body}</p>
        ${c.list?.length && html`<div class="confirm-list">${c.list.map(l => html`<div>${l}</div>`)}</div>`}
        ${c.check && html`<label class="check"><input type="checkbox" checked=${checked} onChange=${e => setChecked(e.currentTarget.checked)} />${c.check.label}</label>`}
        ${need && html`<div class="field"><label for="dlg-in">Type <b class="mono">${need}</b> to confirm</label>
          <input id="dlg-in" class="input mono" autocomplete="off" spellcheck="false" data-autofocus value=${typed} onInput=${e => setTyped(e.currentTarget.value)} /></div>`}
      </div>
      <div class="dialog-foot">
        <button type="button" class="btn" onClick=${() => close(false)} data-autofocus=${need ? undefined : ""}>Cancel</button>
        <button type="submit" class=${"btn " + (c.danger ? "btn-danger-solid" : "btn-primary")} disabled=${!ok}>${c.confirmLabel || "Confirm"}</button>
      </div>
    </form>
  </div>`;
}

// ---------- command palette ----------
const NAV = [
  ["Dashboard", "#/dashboard", "layout-dashboard", "g d"],
  ["Servers", "#/servers", "server", "g s"],
  ["Plugins matrix", "#/plugins", "grid-3x3", "g p"],
  ["Updates", "#/updates", "circle-arrow-up", "g u"],
  ["Deploy", "#/deploy", "rocket", "g y"],
  ["Activity", "#/activity", "history", "g a"],
  ["Settings", "#/settings", "settings", "g ,"],
];
export { NAV };

export function Palette({ actions }) {
  const open = useStore(s => s.palette);
  return open ? html`<${PaletteInner} actions=${actions} />` : null;
}

// Prefix / word-start matching only: "core" finds CoreProtect, never "Switch to dark theme".
function score(q, text) {
  if (!q) return 1;
  const t = text.toLowerCase();
  if (t.startsWith(q)) return 100 - t.length / 100;
  const words = t.split(/[\s/._-]+/);
  if (words.some(w => w.startsWith(q))) return 70 - t.length / 100;
  if (q.length >= 3 && t.includes(q)) return 40 - t.length / 100;
  return -1;
}
// Every token must match somewhere in the text.
const tokScore = (tokens, text) => { let t = 0; for (const k of tokens) { const s = score(k, text); if (s < 0) return -1; t += s; } return t; };
const GROUPS = ["Actions", "Plugins", "Servers", "Files", "Go to"];
// Verb words a query may start or end with; "update core" = verb "update" + entity "core".
const VERBS = { update: "update", upgrade: "update", review: "update", replace: "replace", swap: "replace", remove: "remove", delete: "remove", del: "remove", rm: "remove",
  open: "open", go: "open", show: "open", push: "deploy", deploy: "deploy", sync: "deploy" };
const verbOf = (tok) => Object.keys(VERBS).find(v => tok.length >= 2 && v.startsWith(tok)) ? VERBS[Object.keys(VERBS).find(v => v.startsWith(tok))] : null;

function PaletteInner({ actions }) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const [files, setFiles] = useState([]);
  const ref = useRef(); const listRef = useRef();
  const close = () => setState({ palette: false });
  useTrap(ref, close);
  const [, force] = useState(0);
  useEffect(() => { for (const p of ["/matrix", "/overview", "/settings"]) prefetch(p).then(() => force(x => x + 1)); }, []);
  const qq = q.trim().toLowerCase();
  const tokens = qq ? qq.split(/\s+/) : [];
  // A leading or trailing verb narrows entity actions; the rest is the entity query.
  const verbs = tokens.length > 1 ? tokens.filter((t, i) => (i === 0 || i === tokens.length - 1) && verbOf(t)).map(verbOf) : [];
  const ent = tokens.filter(t => !(tokens.length > 1 && verbOf(t) && verbs.includes(verbOf(t)))).join(" ");

  // File results come from a recursive search of the default source server.
  useEffect(() => {
    const src = peek("/settings")?.default_source;
    const fq = verbs.includes("deploy") ? ent : qq;
    if (!src || fq.replace(/\s/g, "").length < 3) { setFiles([]); return; }
    let live = true;
    const t = setTimeout(() => searchFiles(src, fq, 60)
      .then(r => live && setFiles((r.results || []).filter(f => f.type !== "dir").slice(0, 5).map(f => ({ ...f, src }))), () => live && setFiles([])), 200);
    return () => { live = false; clearTimeout(t); };
  }, [qq]);

  const items = useMemo(() => {
    const ov = peek("/overview"); const mx = peek("/matrix"); const st = peek("/settings");
    const source = st?.default_source;
    const theme = getState().theme;
    const out = [];
    // Plain commands match on all tokens against label + keywords.
    const cmd = (group, label, icon, run, hint, keywords = "") => { const sc = tokScore(tokens, `${label} ${keywords}`); if (sc >= 0) out.push({ group, label, icon, run, hint, sc, rank: 0 }); };
    // Entity actions: entity query matches the name; a verb in the query must match the action's verb.
    const act = (group, verb, rank, name, label, icon, run, hint) => {
      if (verbs.length && !verbs.includes(verb)) return;
      if (verb === "remove" && !verbs.includes("remove")) return; // destructive verbs only on explicit request
      const sc = ent ? tokScore(ent.split(/\s+/), name) : (verbs.length ? 1 : -1);
      if (sc >= 0) out.push({ group, label, icon, run, hint, sc, rank });
    };
    for (const a of actions) cmd("Actions", a.label, a.icon, a.run, a.hint, a.keywords);
    cmd("Actions", "Theme: dark", "moon", () => setTheme("dark"), theme === "dark" ? "current" : null, "theme dark mode appearance night");
    cmd("Actions", "Theme: light", "sun", () => setTheme("light"), theme === "light" ? "current" : null, "theme light mode appearance day");
    if (qq) {
      for (const p of mx?.plugins || []) {
        const cells = Object.entries(p.cells);
        const outd = cells.filter(([, c]) => c.status === "outdated").length;
        const src = source && p.cells[source];
        act("Plugins", "open", 0, p.name, `Open ${p.name}`, "package", () => { navigate(`#/plugins?q=${encodeURIComponent(p.name)}`); setState({ pluginDrawer: p.key }); }, plural(cells.length, "server"));
        if (outd) act("Plugins", "update", 1, p.name, `Update ${p.name} on ${plural(outd, "server")}…`, "circle-arrow-up", () => openChangeset({ keys: [p.key] }, `Update ${p.name} everywhere`));
        if (src) act("Plugins", "replace", 2, p.name, `Replace ${p.name} jar from ${source}…`, "arrow-up-down", () => navigate(`#/deploy?action=replace&jar=${encodeURIComponent(src.jar)}`));
        act("Plugins", "remove", 3, p.name, `Remove ${p.name}…`, "trash-2", () => { navigate("#/plugins"); setTimeout(() => openRemove({ ...p }), 50); });
      }
      for (const s of ov?.servers || []) {
        act("Servers", "open", 0, s.id, `Go to ${s.id}`, "server", () => navigate(`#/servers/${encodeURIComponent(s.id)}`), s.platform);
        if (s.updates) act("Servers", "update", 1, s.id, `Review ${plural(s.updates, "update")} on ${s.id}…`, "circle-arrow-up", () => openChangeset({ server: s.id }, `Review updates on ${s.id}`));
        if (s.eligible_target) act("Servers", "deploy", 2, s.id, `Deploy to ${s.id}…`, "rocket", () => navigate(`#/deploy?targets=${encodeURIComponent(s.id)}`));
      }
      if (!verbs.length || verbs.includes("deploy")) for (const f of files) out.push({ group: "Files", label: `Push ${f.path}…`, icon: "file-code", hint: `from ${f.src}`, sc: 50, rank: 0, run: () => navigate(`#/deploy?paths=${encodeURIComponent(f.path)}`) });
    }
    for (const [label, href, icon, hint] of NAV) cmd("Go to", label, icon, () => navigate(href), hint);
    // Group first (fixed order), then best match, then safe-before-destructive.
    const sorted = out.sort((a, b) => (GROUPS.indexOf(a.group) - GROUPS.indexOf(b.group)) || (Math.round(b.sc) - Math.round(a.sc)) || (a.rank - b.rank));
    const per = {};
    return sorted.filter(it => (per[it.group] = (per[it.group] || 0) + 1) <= (it.group === "Plugins" ? 8 : 6));
  }, [qq, files, peek("/matrix"), peek("/overview")]);

  useEffect(() => setSel(0), [qq]);
  useEffect(() => { listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" }); }, [sel]);
  const run = (i) => { close(); setTimeout(() => i.run(), 0); };
  const onKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel(s => Math.min(items.length - 1, s + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel(s => Math.max(0, s - 1)); }
    else if (e.key === "Enter" && items[sel]) { e.preventDefault(); run(items[sel]); }
  };
  return html`<div class="scrim" style="z-index:60" onClick=${close}></div>
  <div class="palette" role="dialog" aria-modal="true" aria-label="Command palette" ref=${ref}>
    <div class="palette-input">
      <${Icon} n="search" cls="i-lg" />
      <input data-autofocus placeholder="Search plugins, servers, files, actions…" value=${q} onInput=${e => setQ(e.currentTarget.value)} onKeyDown=${onKey}
        role="combobox" aria-expanded="true" aria-controls="pal-list" aria-activedescendant=${items[sel] ? `pal-${sel}` : undefined} aria-autocomplete="list" />
      <${Kbd}>Esc<//>
    </div>
    <div class="palette-list" id="pal-list" role="listbox" ref=${listRef}>
      ${!items.length && html`<div class="empty" style="padding:28px"><p>No matches for “${q}”.</p></div>`}
      ${GROUPS.map(g => { const gi = items.map((it, idx) => [it, idx]).filter(([it]) => it.group === g); return gi.length ? html`<div role="group" aria-labelledby=${`pg-${g}`}>
        <div class="palette-group" id=${`pg-${g}`}>${g}</div>
        ${gi.map(([i, idx]) => html`<div class="palette-item" id=${`pal-${idx}`} role="option" aria-selected=${idx === sel ? "true" : "false"}
          onMouseMove=${() => idx !== sel && setSel(idx)} onClick=${() => run(i)}>
          <${Icon} n=${i.icon} /><span class="ellipsis">${i.label}</span>${i.hint && html`<span class="hint">${i.hint}</span>`}</div>`)}</div>` : null; })}
    </div>
    <div class="palette-foot"><span><${Kbd}>↑<//><${Kbd}>↓<//> navigate</span><span><${Kbd}>↵<//> run</span><span><${Kbd}>${modKey}<//><${Kbd}>K<//> toggle</span></div>
  </div>`;
}

// ---------- job dock ----------
function lineClass(l) {
  if (/^(\[[\d:]+\] )?\$ /.test(l)) return "l-cmd";
  if (/error|failed|denied/i.test(l)) return "l-err";
  if (/warn/i.test(l)) return "l-warn";
  if (/^(\[[\d:]+\] )?==>/.test(l)) return "l-head";
  if (/^(\[[\d:]+\] )?Finished\b|\[changed\]/.test(l)) return "l-ok";
  return "";
}
export const LogView = ({ lines, empty = "Waiting for output…", live }) => {
  const ref = useRef();
  useEffect(() => { const el = ref.current; if (el && live) el.scrollTop = el.scrollHeight; }, [lines.length]);
  return html`<pre class="log" ref=${ref} tabindex="0" aria-label="Job log" aria-live=${live ? "polite" : undefined}>${lines.length
    ? lines.map(l => { const m = /^(\[[\d:]+\] )(.*)$/s.exec(l); return html`<span class=${lineClass(l)}>${m ? html`<span class="l-ts">${m[1]}</span>${m[2]}` : l}${"\n"}</span>`; })
    : html`<span class="log-empty">${empty}</span>`}</pre>`;
};

export function Dock() {
  const jobs = useStore(s => s.jobs.filter(j => j.id !== s.inlineJob));
  const j = jobs[0];
  if (!j) return null;
  const running = isActive(j.status);
  const failed = !running && jobTone(j.job || j.status) !== "ok";
  return html`<section class=${"dock" + (j.min ? " min" : "")} aria-label="Running job">
    <div class="dock-head">
      ${running ? html`<${Icon} n="loader-circle" cls="spin t-info" /> ` : failed ? html`<${Icon} n="circle-x" cls="outcome-failed" />` : html`<${Icon} n="circle-check" cls="outcome-changed" />`}
      <div class="grow"><b>${j.title}</b><div class="sub">${running ? "Running…" : j.job?.summary || j.status} · <span class="mono">${j.id}</span>${jobs.length > 1 ? ` · +${jobs.length - 1} more` : ""}</div></div>
      <a class="btn btn-ghost btn-sm" href=${`#/activity/${j.id}`}>Details</a>
      <${Btn} kind="ghost" size="sm" icon=${j.min ? "chevron-down" : "minus"} aria-label=${j.min ? "Expand log" : "Minimise log"} onClick=${() => toggleJobMin(j.id)} />
      ${!running && html`<${Btn} kind="ghost" size="sm" icon="x" aria-label="Close" onClick=${() => dismissJob(j.id)} />`}
    </div>
    ${running && j.progress?.total
      ? html`<div class="progress is-det" role="progressbar" aria-label="Job progress" aria-valuemin="0" aria-valuemax=${j.progress.total} aria-valuenow=${j.progress.done}><span style=${`width:${100 * j.progress.done / j.progress.total}%`}></span></div>`
      : html`<div class=${"progress" + (running ? "" : failed ? " fail" : " done")} role="progressbar" aria-label="Job progress" aria-valuetext=${j.status}></div>`}
    <${LogView} lines=${j.lines} live=${running} />
  </section>`;
}

// "?" — keyboard shortcut sheet.
const KEYS = [
  [["Ctrl", "K"], "Command palette (also /)"],
  [["G", "D / S / P / U / Y / A"], "Go to Dashboard, Servers, Plugins, Updates, Deploy, Activity"],
  [["G", ","], "Go to Settings"],
  [["J", "K"], "Next / previous tile or list row"],
  [["Enter"], "Open the focused item"],
  [["←↑↓→"], "Matrix: move between cells"],
  [["Space"], "Matrix: select the focused cell"],
  [["U"], "Matrix: review updates for the selection"],
  [["Esc"], "Close a dialog, sheet or palette"],
  [["?"], "This sheet"],
];
export function Shortcuts() {
  const open = useStore(s => s.shortcuts);
  return open ? html`<${ShortcutsInner} />` : null;
}
function ShortcutsInner() {
  const ref = useRef();
  const close = () => setState({ shortcuts: false });
  useTrap(ref, close);
  return html`<div class="scrim" onClick=${close}></div>
  <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="kb-t" ref=${ref}>
    <div class="dialog-body"><h2 id="kb-t"><span class="sr-only">Keyboard </span>Shortcuts</h2>
      <dl class="kb-list">${KEYS.map(([ks, what]) => html`<dt>${ks.map((k, i) => html`${i ? " " : ""}<${Kbd}>${k}<//>`)}</dt><dd>${what}</dd>`)}</dl></div>
    <div class="dialog-foot"><button type="button" class="btn" data-autofocus onClick=${close}>Close</button></div>
  </div>`;
}
