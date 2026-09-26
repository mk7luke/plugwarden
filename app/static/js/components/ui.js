// Shared UI primitives.
import { html } from "../lib.js";
import { compactVer } from "../fmt.js";

export const Icon = ({ n, cls = "", label, style }) => html`
  <svg class=${"i " + cls} style=${style} aria-hidden=${label ? undefined : "true"} role=${label ? "img" : undefined} aria-label=${label}>
    <use href=${"/static/icons.svg#i-" + n} />
  </svg>`;

export const Kbd = ({ children }) => html`<kbd class="kbd">${children}</kbd>`;
export const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
export const modKey = isMac ? "⌘" : "Ctrl";

export function Btn({ kind = "", size = "", icon, iconRight, children, cls = "", busy, ...rest }) {
  const c = ["btn", kind && `btn-${kind}`, size && `btn-${size}`, !children && "btn-icon", cls].filter(Boolean).join(" ");
  return html`<button type="button" class=${c} aria-busy=${busy ? "true" : undefined} ...${rest} disabled=${rest.disabled || busy}>
    ${busy ? html`<${Icon} n="loader-circle" cls="i-sm spin" />` : icon && html`<${Icon} n=${icon} cls="i-sm" />`}
    ${children}
    ${iconRight && html`<${Icon} n=${iconRight} cls="i-sm" />`}
  </button>`;
}

export const Tag = ({ kind = "", icon, children, title }) =>
  html`<span class=${"tag" + (kind ? " tag-" + kind : "")} title=${title}>${icon && html`<${Icon} n=${icon} />`}${children}</span>`;

const STATUS = {
  current: ["ok", "check", "Up to date"],
  outdated: ["update", "circle-arrow-up", "Update available"],
  drift: ["drift", "git-compare-arrows", "Drift"],
  unknown: ["", "circle-dashed", "Unknown source"],
  pinned: ["plain", "pin", "Pinned"],
  ignored: ["plain", "eye-off", "Ignored"],
  unreadable: ["danger", "file-code", "unreadable plugin.yml"],
};
export const StatusTag = ({ status, label }) => {
  const [k, i, l] = STATUS[status] || STATUS.unknown;
  return html`<${Tag} kind=${k} icon=${i}>${label || l}<//>`;
};

const PF = { purpur: ["Pu", "Purpur"], paper: ["Pa", "Paper"], velocity: ["V", "Velocity"], fabric: ["Fa", "Fabric"], unknown: ["?", "Unknown"] };
export const platformName = (p) => (PF[p] || PF.unknown)[1];
export const Platform = ({ p, mc, short }) => {
  const [g, n] = PF[p] || PF.unknown;
  return html`<span class="platform" title=${short ? `${n} ${mc || ""}` : undefined}><span class="pf-glyph" aria-hidden="true">${g}</span>${short ? html`<span class="sr-only">${n}</span>` : n}${mc && html`<span class="mono muted">${mc}</span>`}</span>`;
};

// compact: shorten build suffixes (full value in the tooltip); both sides are always shown in full otherwise.
export const VerArrow = ({ from, to, compact }) => {
  const f = Array.isArray(from) ? from.join(", ") : from;
  return html`<span class="ver-arrow" title=${compact ? `${f} → ${to}` : undefined}>
  <span class="from">${compact ? compactVer(f) : f}</span><${Icon} n="arrow-right" cls="i-xs" /><span class="to">${compact ? compactVer(to) : to}</span></span>`;
};

export const Skel = ({ w = "100%", h = 10, r, style = "" }) =>
  html`<span class="skel" style=${`display:block;width:${w};height:${h}px;${r ? `border-radius:${r}px;` : ""}${style}`} aria-hidden="true"></span>`;

export const SkelRows = ({ n = 6, cols = [40, 22, 16, 12] }) => html`<div aria-busy="true" aria-label="Loading">
  ${Array.from({ length: n }, (_, i) => html`<div class="row" style="padding:12px 16px;border-top:${i ? "1px solid var(--line)" : "0"};gap:24px">
    ${cols.map((c, j) => html`<${Skel} w=${`${c + ((i * 7 + j * 3) % 9)}%`} />`)}</div>`)}
</div>`;

export const Empty = ({ icon = "box", title, children, action, ok }) => html`<div class=${"empty" + (ok ? " ok" : "")}>
  <div class="em-icon"><${Icon} n=${icon} cls="i-lg" /></div>
  <h3>${title}</h3>${children && html`<p>${children}</p>`}${action}
</div>`;

export const ErrorState = ({ error, retry, title = "Couldn't load this" }) => html`<div class="error-box" role="alert">
  <${Icon} n="triangle-alert" />
  <div class="grow"><b>${title}</b><p>${error?.message || String(error)}</p>
    ${error?.path && html`<code>GET /api/v2${error.path}${error.status ? ` → ${error.status}` : ""}</code>`}
    ${retry && html`<div style="margin-top:10px"><${Btn} size="sm" icon="refresh-cw" onClick=${retry}>Retry<//></div>`}
  </div></div>`;

export function PageHead({ title, sub, children }) {
  return html`<header class="page-head"><div><h1>${title}</h1>${sub && html`<p>${sub}</p>`}</div>
    ${children && html`<div class="actions">${children}</div>`}</header>`;
}

export const ServerChip = ({ id }) => html`<a class="srv-chip" href=${"#/servers/" + encodeURIComponent(id)}>${id}</a>`;

export function Check({ checked, indeterminate, onChange, label, children, disabled }) {
  return html`<label class="check"><input type="checkbox" checked=${!!checked} aria-label=${label} disabled=${!!disabled}
    ref=${el => el && (el.indeterminate = !!indeterminate)} onChange=${e => onChange(e.currentTarget.checked)} />${children}</label>`;
}

// ---------- focus trap ----------
// Tab / Shift+Tab wrap inside `el`. Only real focus targets count (not SVG <use href> or hidden elements).
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';
export const focusables = (el) => [...el.querySelectorAll(FOCUSABLE)].filter(x => x.offsetParent !== null || x === document.activeElement);
export function trapTab(e, el) {
  if (e.key !== "Tab" || !el) return;
  const a = document.activeElement;
  // A dialog stacked above (e.g. a confirm over a sheet) handles its own focus.
  if (a && a !== document.body && !el.contains(a)) return;
  const f = focusables(el);
  if (!f.length) { e.preventDefault(); return; }
  const i = f.indexOf(a);
  if (e.shiftKey && (i <= 0)) { e.preventDefault(); f[f.length - 1].focus(); }
  else if (!e.shiftKey && (i === -1 || i === f.length - 1)) { e.preventDefault(); f[0].focus(); }
}

// Return focus when a modal closes. If another modal took over (drawer → Remove dialog), don't steal focus from it:
// remember the original opener instead, and give focus back to it when the last modal in the chain closes.
let handoff = null;
export function restoreFocus(prev) {
  setTimeout(() => {
    const live = prev && prev !== document.body && document.contains(prev) && !prev.closest('[aria-modal="true"]');
    if (document.querySelector('[aria-modal="true"]')) { if (live) handoff = prev; return; }
    const t = live ? prev : handoff && document.contains(handoff) ? handoff : null;
    handoff = null;
    t?.focus?.();
  }, 0);
}
