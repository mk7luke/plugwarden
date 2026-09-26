// Entry: shell + router + global shortcuts.
import { html, render, useEffect } from "./lib.js";
import { useRoute, navigate } from "./router.js";
import { setState, getState, prefetch, peek, invalidate, useQuery } from "./store.js";
import { trackJob, JOB_TITLES } from "./jobs.js";
import { Sidebar, Topbar, Tabbar, Drawer } from "./components/shell.js";
import { Toasts, ConfirmHost, Palette, Dock, NAV } from "./components/overlays.js";
import { ChangesetHost } from "./components/changeset.js";
import { RemoveHost } from "./components/removedialog.js";
import { checkUpdates, openUpdateAll } from "./actions.js";
import { Dashboard } from "./views/dashboard.js";
import { ServersList, ServerDetail } from "./views/servers.js";
import { Matrix } from "./views/matrix.js";
import { Updates } from "./views/updates.js";
import { Deploy } from "./views/deploy.js";
import { Activity } from "./views/activity.js";
import { Settings } from "./views/settings.js";
import { Empty } from "./components/ui.js";
import { relTime } from "./fmt.js";

const TITLES = { dashboard: "Dashboard", servers: "Servers", plugins: "Plugins", updates: "Updates", deploy: "Deploy", activity: "Activity", settings: "Settings" };

// "Deploy · 5m ago" for a job id, when the job list is cached.
function jobCrumb(id) {
  const j = (peek("/jobs") || []).find(x => x.id === id);
  return j ? `${JOB_TITLES[j.kind] || j.kind} · ${relTime(j.started || j.created)}` : "Job";
}

function view(r) {
  switch (r.name) {
    case "dashboard": return [html`<${Dashboard} />`];
    case "servers": return r.parts[1]
      ? [html`<${ServerDetail} key=${r.parts[1]} id=${r.parts[1]} />`, [{ label: "Servers", href: "#/servers" }, { label: r.parts[1] }]]
      : [html`<${ServersList} />`];
    case "plugins": return [html`<${Matrix} query=${r.query} />`, null, true];
    case "updates": return [html`<${Updates} />`];
    case "deploy": return [html`<${Deploy} query=${r.query} />`, null, true];
    case "activity": return [html`<${Activity} id=${r.parts[1]} tab=${r.query.tab} />`, r.parts[1] ? [{ label: "Activity", href: "#/activity" }, { label: jobCrumb(r.parts[1]) }] : null];
    case "settings": return [html`<${Settings} tab=${r.parts[1] || "general"} />`];
    default: return [html`<${Empty} icon="triangle-alert" title="Page not found" action=${html`<a class="btn" href="#/dashboard">Back to dashboard</a>`}>Nothing lives at <code>#/${r.parts.join("/")}</code>.<//>`, [{ label: "Not found" }]];
  }
}

const PALETTE_ACTIONS = [
  { label: "Check for updates now", icon: "refresh-cw", run: checkUpdates, keywords: "refresh scan" },
  { label: "Review & update all…", icon: "circle-arrow-up", run: openUpdateAll, keywords: "update everything upgrade" },
  { label: "Push a file to servers…", icon: "rocket", run: () => navigate("#/deploy"), keywords: "deploy sync config" },
  { label: "Upload and roll out a jar…", icon: "upload", run: () => navigate("#/deploy?upload=1"), keywords: "install new plugin" },
];

function App() {
  const route = useRoute();
  // The job list feeds the Activity breadcrumb ("Deploy · 5m ago").
  useQuery(route.name === "activity" && route.parts[1] ? "/jobs" : null);
  const [content, crumbs, wide] = view(route);
  useEffect(() => {
    document.title = `${crumbs ? crumbs[crumbs.length - 1].label : TITLES[route.name] || "Not found"} · AMP Sync`;
    setState({ drawer: false });
    document.getElementById("main")?.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }, [route.name, route.parts.join("/")]);

  useEffect(() => {
    let g = 0;
    const onKey = (e) => {
      const k = e.key.toLowerCase();
      if ((e.metaKey || e.ctrlKey) && k === "k") { e.preventDefault(); setState({ palette: !getState().palette }); return; }
      const t = e.target;
      if (e.metaKey || e.ctrlKey || e.altKey || t.isContentEditable || /INPUT|TEXTAREA|SELECT/.test(t.tagName) || getState().palette || getState().confirm) return;
      // j/k walk the current view's list (tiles, job rows, server rows); Enter opens.
      if (k === "j" || k === "k") {
        const items = [...document.querySelectorAll("#main [data-nav]")].filter(el => el.offsetParent);
        if (items.length) {
          e.preventDefault();
          const i = items.indexOf(document.activeElement);
          const next = items[k === "j" ? Math.min(items.length - 1, i + 1) : Math.max(0, i < 0 ? 0 : i - 1)];
          next.focus(); next.scrollIntoView({ block: "nearest" });
        }
        return;
      }
      if (k === "g") { g = Date.now(); return; }
      if (Date.now() - g < 900) {
        const hit = NAV.find(n => n[3] === `g ${k}`);
        if (hit) { e.preventDefault(); navigate(hit[1]); }
        g = 0;
      } else if (k === "/") { e.preventDefault(); setState({ palette: true }); }
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, []);

  return html`<div class="app">
    <${Sidebar} route=${route} />
    <div class="main">
      <${Topbar} crumbs=${crumbs || [{ label: TITLES[route.name] || "Not found" }]} />
      <main id="main" class=${"content" + (wide ? " wide" : "")} tabindex="-1">${content}</main>
    </div>
    <${Tabbar} route=${route} />
    <${Drawer} route=${route} />
    <${Palette} actions=${PALETTE_ACTIONS} />
    <${ChangesetHost} />
    <${RemoveHost} />
    <${ConfirmHost} />
    <${Dock} />
    <${Toasts} />
  </div>`;
}

// Attach to jobs already running (started elsewhere or by the scheduler) so their logs stream into the dock.
prefetch("/overview").finally(() => {
  for (const j of peek("/overview")?.active_jobs || []) trackJob(j.id, { title: JOB_TITLES[j.kind] || "Job", quiet: true });
});

// Keep the overview (counts, last check, auto-update status) fresh while the tab is visible.
setInterval(() => { if (document.visibilityState === "visible") invalidate("/overview"); }, 60000);

if (!location.hash) history.replaceState(null, "", location.pathname + location.search + "#/dashboard");
render(html`<${App} />`, document.getElementById("app"));
