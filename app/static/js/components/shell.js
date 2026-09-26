// App shell: sidebar, top bar, mobile tab bar + drawer.
import { html, useRef } from "../lib.js";
import { useStore, setState, useQuery, setTheme } from "../store.js";
import { Icon, Btn, Kbd, modKey } from "./ui.js";
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

const MODE_LABEL = { off: "Auto-update off", notify: "Auto-check · notify", apply: "Auto-update on" };

export const Brand = () => html`<a class="brand" href="#/dashboard" aria-label="AMP Sync — dashboard">
  <svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="8" fill="var(--accent)"/>
    <path d="M9 11.5 16 8l7 3.5v9L16 24l-7-3.5zM9 11.5 16 15l7-3.5M16 15v9" fill="none" stroke="var(--accent-fg)" stroke-width="2" stroke-linejoin="round"/></svg>
  <div class="brand-text">AMP Sync<small>LGT network</small></div>
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
      <span title=${ov?.user && ov.user !== "local" ? `Signed in as ${ov.user}` : "No Cloudflare Access identity"}>${!ov ? "—" : ov.user && ov.user !== "local" ? ov.user : "Local session"}</span>
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
  const { data: ov } = useQuery("/overview");
  if (!open) return null;
  const close = () => setState({ drawer: false });
  return html`<div class="scrim" onClick=${close}></div>
  <aside class="drawer" role="dialog" aria-modal="true" aria-label="Navigation" onKeyDown=${e => e.key === "Escape" && close()}>
    <div class="row"><${Brand} /><span class="grow"></span><${Btn} kind="ghost" icon="x" aria-label="Close menu" onClick=${close} /></div>
    <${NavList} route=${route} ov=${ov} onNav=${close} />
    <${SideFoot} ov=${ov} route=${route} onNav=${close} />
  </aside>`;
}

export function Topbar({ crumbs }) {
  const { data: ov } = useQuery("/overview");
  const checkJob = useStore(s => s.jobs.find(j => isActive(j.status) && j.title === "Update check"));
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
      <span class="check-status" title="Last update check">${checking ? (pct != null ? `Checking… ${pct}%` : "Checking…") : ov ? (ov.last_check ? `Checked ${relTime(ov.last_check)}` : "Never checked") : ""}</span>
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
