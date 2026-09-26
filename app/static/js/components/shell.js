// App shell: sidebar, top bar, mobile tab bar + drawer.
import { html, useRef, useEffect } from "../lib.js";
import { useStore, setState, useQuery, setTheme, invalidate } from "../store.js";
import { Icon, Btn, Kbd, modKey, trapTab, restoreFocus } from "./ui.js";
import { checkUpdates } from "../actions.js";
import { isActive } from "../jobs.js";
import { relTime, initials } from "../fmt.js";
import { FIXTURES } from "../api.js";
import { updateCounts } from "../summary.js";

const ITEMS = [
  { id: "dashboard", label: "Dashboard", icon: "layout-dashboard", href: "#/dashboard" },
  { id: "servers", label: "Servers", icon: "server", href: "#/servers" },
  { id: "plugins", label: "Plugins", icon: "grid-3x3", href: "#/plugins" },
  { id: "updates", label: "Updates", icon: "circle-arrow-up", href: "#/updates", count: "updates" },
  { id: "deploy", label: "Deploy", icon: "rocket", href: "#/deploy" },
  { id: "activity", label: "Activity", icon: "history", href: "#/activity" },
];

// A verified Cloudflare Access identity; auth "none" (dev) reports "local".
const signedIn = (ov) => ov?.user && ov.user !== "local" && ov.auth !== "none";

const MODE_LABEL = { off: "Auto-update off", notify: "Auto-check · notify", apply: "Auto-update on" };

// Mark: a warden's shield with a plug — "keeps your plugins in line".
export const Brand = () => html`<a class="brand" href="#/dashboard" aria-label="PlugWarden — dashboard">
  <svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true"><path d="M16 3.5 26 7v8.2c0 6-4.1 10.9-10 13.3C10.1 26.1 6 21.2 6 15.2V7z" fill="var(--accent)"/>
    <path d="M12.5 9.5v4M19.5 9.5v4M10.5 13.5h11v2.5a5.5 5.5 0 0 1-11 0zM16 21.5v3" fill="none" stroke="var(--accent-fg)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
  <div class="brand-text">PlugWarden<small>LGT Network</small></div>
</a>`;

function NavList({ route, ov, onNav }) {
  const counts = { updates: ov ? updateCounts(ov).plugins : 0 };
  const link = (it) => {
    const n = it.count && counts[it.count];
    return html`<a class="nav-item" href=${it.href} aria-current=${route.name === it.id ? "page" : undefined} onClick=${onNav} title=${it.label}>
      <${Icon} n=${it.icon} /><span>${it.label}</span>${n ? html`<span class="count hot" aria-label=${`${n} available`}>${n}</span>` : null}</a>`;
  };
  return html`<nav class="nav" aria-label="Primary">
    <div class="nav-label">Network</div>
    ${ITEMS.slice(0, 3).map(link)}
    <div class="nav-label">Operate</div>
    ${ITEMS.slice(3).map(link)}
  </nav>`;
}

function SideFoot({ ov, route, onNav }) {
  const theme = useStore(s => s.theme);
  const mode = ov?.auto_update?.mode;
  return html`<div class="sidebar-foot">
    <a class="auto-chip" href="#/updates" onClick=${onNav} title="Auto-update policy">
      <span class=${"dot " + (mode === "apply" ? "dot-ok pulse" : mode === "notify" ? "dot-update" : "dot-muted")}></span>
      <div>${mode ? html`<b>${MODE_LABEL[mode] || mode}</b>${ov.auto_update.next_run ? `Next run ${relTime(ov.auto_update.next_run)}` : "Not scheduled"}` : html`<b>Auto-update</b>—`}</div>
    </a>
    <a class="nav-item" href="#/settings" aria-current=${route.name === "settings" ? "page" : undefined} onClick=${onNav} title="Settings"><${Icon} n="settings" /><span>Settings</span></a>
    <div class="user-row">
      <span class="avatar" aria-hidden="true">${initials(ov?.user)}</span>
      <span title=${signedIn(ov) ? `Signed in as ${ov.user} (Cloudflare Access)` : "No Cloudflare Access identity — development mode"}>${!ov ? "—" : signedIn(ov) ? ov.user : "local (dev)"}</span>
      <${Btn} kind="ghost" size="sm" icon=${theme === "dark" ? "sun" : "moon"} aria-label=${`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
        onClick=${() => setTheme(theme === "dark" ? "light" : "dark")} cls="hide-rail" />
    </div>
  </div>`;
}

export function Sidebar({ route }) {
  const { data: ov } = useQuery("/overview");
  return html`<aside class="sidebar" aria-label="Sidebar">
    <${Brand} />
    <${NavList} route=${route} ov=${ov} />
    <${SideFoot} ov=${ov} route=${route} />
  </aside>`;
}

export function Drawer({ route }) {
  const open = useStore(s => s.drawer);
  return open ? html`<${DrawerPanel} route=${route} />` : null;
}

function DrawerPanel({ route }) {
  const { data: ov } = useQuery("/overview");
  const ref = useRef();
  const close = () => setState({ drawer: false });
  useEffect(() => {
    const prev = document.activeElement;
    ref.current?.querySelector("[data-autofocus]")?.focus();
    const k = (e) => { if (e.key === "Escape") close(); trapTab(e, ref.current); };
    document.addEventListener("keydown", k);
    return () => { document.removeEventListener("keydown", k); restoreFocus(prev); };
  }, []);
  return html`<div class="scrim" onClick=${close}></div>
  <aside class="drawer" role="dialog" aria-modal="true" aria-label="Navigation" ref=${ref}>
    <div class="row"><${Brand} /><span class="grow"></span><${Btn} kind="ghost" icon="x" aria-label="Close menu" onClick=${close} data-autofocus /></div>
    <${NavList} route=${route} ov=${ov} onNav=${close} />
    <${SideFoot} ov=${ov} route=${route} onNav=${close} />
  </aside>`;
}

export function Topbar({ crumbs }) {
  const { data: ov } = useQuery("/overview");
  const checkJob = useStore(s => s.jobs.find(j => isActive(j.status) && j.title === "Update check"));
  // Cold start: the backend is still hashing jars. Poll the (fast) overview, then refresh everything once done.
  const indexing = ov?.indexing;
  const wasIndexing = useRef(false);
  useEffect(() => {
    if (indexing) { wasIndexing.current = true; const t = setTimeout(() => invalidate("/overview"), 2000); return () => clearTimeout(t); }
    if (wasIndexing.current) { wasIndexing.current = false; invalidate(); }
  }, [indexing?.done, !!indexing]);
  const checking = !!checkJob;
  const pct = checkJob?.progress?.total ? Math.round(100 * checkJob.progress.done / checkJob.progress.total) : null;
  return html`<header class="topbar">
    <${Btn} kind="ghost" icon="menu" cls="menu-btn" aria-label="Open menu" onClick=${() => setState({ drawer: true })} />
    <nav class="crumbs" aria-label="Breadcrumb">
      ${crumbs.map((c, i) => i < crumbs.length - 1
        ? html`<a href=${c.href}>${c.label}</a><span class="sep" aria-hidden="true">/</span>`
        : html`<span class="here" aria-current="page">${c.label}</span>`)}
    </nav>
    ${FIXTURES && html`<span class="tag tag-warn" title="Using local fixture data (?fixtures=1)">fixtures</span>`}
    <button class="search-trigger" type="button" onClick=${() => setState({ palette: true })} aria-label="Search and commands" aria-keyshortcuts="Control+K Meta+K">
      <${Icon} n="search" cls="i-sm" /><span class="label">Search or run a command…</span>
      <span class="kbds"><${Kbd}>${modKey}<//><${Kbd}>K<//></span>
    </button>
    <div class="top-actions">
      <span class="check-status" title=${indexing ? "Reading plugin jars after a restart — results are provisional until this finishes" : "Last update check"}>${indexing ? html`<${Icon} n="loader-circle" cls="i-xs spin" /> Indexing plugins… ${indexing.done}/${indexing.total}` : checking ? (pct != null ? `Checking… ${pct}%` : "Checking…") : ov ? (ov.last_check ? `Checked ${relTime(ov.last_check)}` : "Never checked") : ""}</span>
      <${Btn} icon="refresh-cw" busy=${checking} onClick=${checkUpdates} aria-label="Check for updates"><span class="hide-sm">Check updates</span><//>
    </div>
  </header>`;
}

export function Tabbar({ route }) {
  const { data: ov } = useQuery("/overview");
  // Servers is the #1 entry point on a phone; Deploy lives in the menu drawer.
  const tabs = [ITEMS[0], ITEMS[1], ITEMS[2], ITEMS[3], ITEMS[5]];
  return html`<nav class="tabbar" aria-label="Primary">
    ${tabs.map(t => html`<a href=${t.href} aria-current=${route.name === t.id ? "page" : undefined}>
      <${Icon} n=${t.icon} cls="i-lg" />${t.label}
      ${t.count && ov && updateCounts(ov).plugins ? html`<span class="badge" aria-label=${`${updateCounts(ov).plugins} updates`}>${updateCounts(ov).plugins}</span>` : null}</a>`)}
  </nav>`;
}
