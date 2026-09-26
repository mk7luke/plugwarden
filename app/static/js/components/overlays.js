// Toasts, confirm dialog, command palette and job dock.
import { html, useState, useEffect, useRef, useMemo } from "../lib.js";
import { useStore, setState, dismissToast, peek, prefetch, setTheme, getState } from "../store.js";
import { navigate } from "../router.js";
import { Icon, Btn, Kbd, modKey } from "./ui.js";
import { dismissJob, toggleJobMin, isActive, jobTone } from "../jobs.js";
import { fuzzy } from "../fmt.js";

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
function PaletteInner({ actions }) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const ref = useRef(); const listRef = useRef();
  const close = () => setState({ palette: false });
  useTrap(ref, close);
  const [, force] = useState(0);
  useEffect(() => { prefetch("/matrix").then(() => force(x => x + 1)); prefetch("/overview").then(() => force(x => x + 1)); }, []);

  const items = useMemo(() => {
    const ov = peek("/overview"); const mx = peek("/matrix");
    const all = [
      ...actions.map(a => ({ group: "Actions", label: a.label, icon: a.icon, run: a.run, hint: a.hint })),
      { group: "Actions", label: `Switch to ${getState().theme === "dark" ? "light" : "dark"} theme`, icon: getState().theme === "dark" ? "sun" : "moon", run: () => setTheme(getState().theme === "dark" ? "light" : "dark") },
      ...NAV.map(([label, href, icon, hint]) => ({ group: "Go to", label, icon, hint, run: () => navigate(href) })),
      ...(ov?.servers || []).map(s => ({ group: "Servers", label: s.id, icon: "server", hint: s.platform, run: () => navigate(`#/servers/${encodeURIComponent(s.id)}`) })),
      ...(mx?.plugins || []).map(p => ({ group: "Plugins", label: p.name, icon: "package", hint: `${Object.keys(p.cells).length} servers`, run: () => navigate(`#/plugins?q=${encodeURIComponent(p.name)}`) })),
    ];
    if (!q) return all.filter(i => i.group !== "Plugins").slice(0, 40);
    return all.map(i => ({ ...i, score: fuzzy(q, i.label) })).filter(i => i.score >= 0).sort((a, b) => b.score - a.score).slice(0, 40);
  }, [q, peek("/matrix"), peek("/overview")]);

  useEffect(() => setSel(0), [q]);
  useEffect(() => { listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" }); }, [sel]);
  const run = (i) => { close(); setTimeout(() => i.run(), 0); };
  const onKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel(s => Math.min(items.length - 1, s + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel(s => Math.max(0, s - 1)); }
    else if (e.key === "Enter" && items[sel]) { e.preventDefault(); run(items[sel]); }
  };
  let lastGroup = null;
  return html`<div class="scrim" style="z-index:60" onClick=${close}></div>
  <div class="palette" role="dialog" aria-modal="true" aria-label="Command palette" ref=${ref}>
    <div class="palette-input">
      <${Icon} n="search" cls="i-lg" />
      <input data-autofocus placeholder="Search servers, plugins, actions…" value=${q} onInput=${e => setQ(e.currentTarget.value)} onKeyDown=${onKey}
        role="combobox" aria-expanded="true" aria-controls="pal-list" aria-activedescendant=${items[sel] ? `pal-${sel}` : undefined} aria-autocomplete="list" />
      <${Kbd}>Esc<//>
    </div>
    <div class="palette-list" id="pal-list" role="listbox" ref=${listRef}>
      ${!items.length && html`<div class="empty" style="padding:28px"><p>No matches for “${q}”.</p></div>`}
      ${items.map((i, idx) => {
        const head = i.group !== lastGroup ? (lastGroup = i.group, html`<div class="palette-group" role="presentation">${i.group}</div>`) : null;
        return html`${head}<div class="palette-item" id=${`pal-${idx}`} role="option" aria-selected=${idx === sel ? "true" : "false"}
          onMouseMove=${() => idx !== sel && setSel(idx)} onClick=${() => run(i)}>
          <${Icon} n=${i.icon} /><span>${i.label}</span>${i.hint && html`<span class="hint">${i.hint}</span>`}</div>`;
      })}
    </div>
    <div class="palette-foot"><span><${Kbd}>↑<//><${Kbd}>↓<//> navigate</span><span><${Kbd}>↵<//> open</span><span><${Kbd}>${modKey}<//><${Kbd}>K<//> toggle</span></div>
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
    <div class=${"progress" + (running ? "" : failed ? " fail" : " done")} role="progressbar" aria-label="Job progress" aria-valuetext=${j.status}></div>
    <${LogView} lines=${j.lines} live=${running} />
  </section>`;
}
