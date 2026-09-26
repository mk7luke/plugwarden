// Entry: shell + router + global shortcuts.
import { html, render, useEffect } from "./lib.js";
import { useRoute, navigate } from "./router.js";
import { setState, getState, prefetch, peek, invalidate } from "./store.js";
import { trackJob, JOB_TITLES } from "./jobs.js";
import { Sidebar, Topbar, Tabbar, Drawer } from "./components/shell.js";
import { Toasts, ConfirmHost, Palette, Dock, NAV } from "./components/overlays.js";
import { UpdateAllHost } from "./components/updateall.js";
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

const TITLES = { dashboard: "Dashboard", servers: "Servers", plugins: "Plugins", updates: "Updates", deploy: "Deploy", activity: "Activity", settings: "Settings" };

function view(r) {
  switch (r.name) {
    case "dashboard": return [html`<${Dashboard} />`];
    case "servers": return r.parts[1]
      ? [html`<${ServerDetail} key=${r.parts[1]} id=${r.parts[1]} />`, [{ label: "Servers", href: "#/servers" }, { label: r.parts[1] }]]
      : [html`<${ServersList} />`];
    case "plugins": return [html`<${Matrix} query=${r.query} />`, null, true];
    case "updates": return [html`<${Updates} />`];
    case "deploy": return [html`<${Deploy} query=${r.query} />`, null, true];
    case "activity": return [html`<${Activity} id=${r.parts[1]} />`, r.parts[1] ? [{ label: "Activity", href: "#/activity" }, { label: r.parts[1] }] : null];
    case "settings": return [html`<${Settings} tab=${r.parts[1] || "general"} />`];
    default: return [html`<${Empty} icon="triangle-alert" title="Page not found" action=${html`<a class="btn" href="#/dashboard">Back to dashboard</a>`}>Nothing lives at <code>#/${r.parts.join("/")}</code>.<//>`, [{ label: "Not found" }]];
  }
}

const PALETTE_ACTIONS = [
  { label: "Check for updates now", icon: "refresh-cw", run: checkUpdates },
  { label: "Update all…", icon: "zap", run: openUpdateAll },
  { label: "New deploy", icon: "rocket", run: () => navigate("#/deploy") },
  { label: "Upload a jar", icon: "upload", run: () => navigate("#/deploy?upload=1") },
];

function App() {
  const route = useRoute();
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
    <${UpdateAllHost} />
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
