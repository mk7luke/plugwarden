// AMP integration: live server status, power actions, rolling restarts and a live console.
// Everything here renders nothing unless the backend reports AMP as configured (overview.amp.configured).
import { html, useState, useEffect, useRef } from "../lib.js";
import { useQuery, useStore, setState, getState, invalidate, toast, confirmDialog, peek } from "../store.js";
import { get, post, streamUrl } from "../api.js";
import { trackJob } from "../jobs.js";
import { Icon, Btn, Tag, Check } from "./ui.js";
import { plural } from "../fmt.js";

// ---------- status ----------
export const ampConfigured = (ov) => !!ov?.amp?.configured;

// Live status for all servers, polled while mounted (every 10 s; the status endpoint is cheap).
export function useAmpStatus() {
  const ov = useQuery("/overview").data;
  const on = ampConfigured(ov);
  const q = useQuery(on ? "/amp/status" : null);
  useEffect(() => {
    if (!on) return;
    const t = setInterval(() => document.visibilityState === "visible" && invalidate("/amp/status"), 10000);
    return () => clearInterval(t);
  }, [on]);
  const raw = on ? q.data?.servers || ov?.amp?.servers || {} : {};
  const servers = Object.fromEntries(Object.entries(raw).map(([id, a]) => [id, norm(a)]));
  const src = q.data || ov?.amp;
  const err = q.error || (src?.reachable === false || (q.data && q.data.error) ? { message: src.error || "AMP is unreachable." } : null);
  return { on, readonly: !!(q.data?.readonly ?? ov?.amp?.readonly), reachable: !err, error: err, servers };
}

// The backend's AMP status → the shape the UI uses.
function norm(a) {
  if (!a) return a;
  return { state: a.state, error: a.error, players: { online: a.players_online ?? 0, max: a.players_max, names: a.players || [] },
    cpu_pct: a.cpu_percent, mem_mb: a.memory_mb, mem_max_mb: a.memory_max_mb, uptime_s: a.uptime_seconds };
}

// AMP application states (the backend passes AMP's names through).
const STATE = {
  running: ["ok", "Running"], starting: ["update", "Starting"], prestart: ["update", "Starting"], configuring: ["update", "Starting"],
  restarting: ["update", "Restarting"], stopping: ["warn", "Stopping"], preparing_sleep: ["warn", "Stopping"],
  stopped: ["muted", "Stopped"], sleeping: ["muted", "Sleeping"], waiting: ["muted", "Waiting"],
  installing: ["update", "Installing"], updating: ["update", "Updating"], awaiting_input: ["warn", "Needs input"],
  failed: ["danger", "Failed"], suspended: ["muted", "Suspended"], maintenance: ["warn", "Maintenance"],
  instance_offline: ["muted", "AMP instance offline"], undefined: ["muted", "Unknown"], indeterminate: ["muted", "Unknown"], unknown: ["muted", "Unknown"],
};
const BUSY = ["starting", "prestart", "configuring", "restarting", "stopping", "preparing_sleep", "installing", "updating"];
export const stateLabel = (st) => (STATE[st] || STATE.unknown)[1];

// Small dot + players, for tiles and lists.
export function AmpBadge({ a }) {
  if (!a) return null;
  const [k, l] = STATE[a.state] || STATE.unknown;
  const busy = BUSY.includes(a.state);
  return html`<span class="amp-badge tip" tabindex="0" data-tip=${`${l}${a.state === "running" ? ` · ${a.players?.online ?? 0}/${a.players?.max ?? "?"} players · up ${uptime(a.uptime_s)}` : ""}`}>
    <i class=${`dot dot-${k === "muted" ? "muted" : k}${busy ? " pulse" : ""}`}></i>${a.state === "running" ? html`<${Icon} n="user" cls="i-xs" />${a.players?.online ?? 0}` : l}</span>`;
}

export function uptime(s) {
  if (!s && s !== 0) return "—";
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

// ---------- power ----------
export async function power(server, action) {
  if (action !== "start") {
    const a = getState().ampCache?.[server];
    const players = a?.players?.online || 0;
    const ok = await confirmDialog({
      danger: action === "stop", title: `${action === "stop" ? "Stop" : "Restart"} ${server}?`,
      body: `${players ? `${plural(players, "player")} online will be disconnected. ` : ""}${action === "restart" ? "Use Rolling restart for in-game warnings and a startup health check." : "The server stays offline until someone starts it."}`,
      confirmLabel: action === "stop" ? "Stop server" : "Restart now",
    });
    if (!ok) return;
  }
  try {
    const r = await post(`/servers/${encodeURIComponent(server)}/power`, { action });
    trackJob(r.job_id, { title: `${action[0].toUpperCase() + action.slice(1)} ${server}` });
    invalidate("/amp/status");
  } catch (e) { toast({ kind: "err", title: `Couldn't ${action} ${server}`, body: e.message }); }
}

// Server page panel: state, players, CPU/RAM, uptime, power buttons, console.
export function AmpPanel({ server }) {
  const amp = useAmpStatus();
  if (!amp.on) return null;
  const a = amp.servers[server];
  if (!amp.reachable) return html`<section class="amp-panel is-down" role="status"><${Icon} n="triangle-alert" cls="i-sm" />
    <span class="small">AMP is unreachable${amp.error?.message ? ` — ${amp.error.message}` : ""}. Live status and power controls are unavailable.</span></section>`;
  if (!a) return null;
  const [k, l] = STATE[a.state] || STATE.unknown;
  const running = a.state === "running";
  const busy = BUSY.includes(a.state);
  const offline = a.state === "instance_offline";
  const memPct = a.mem_max_mb ? Math.round(100 * a.mem_mb / a.mem_max_mb) : null;
  return html`<section class="amp-panel" aria-label=${`${server} live status`}>
    <div class="amp-state"><i class=${`dot dot-${k === "muted" ? "muted" : k}${busy ? " pulse" : ""}`}></i><b>${l}</b></div>
    <dl class="amp-stats">
      <div><dt>Players</dt><dd>${running ? `${a.players?.online ?? 0} / ${a.players?.max ?? "?"}` : "—"}</dd></div>
      <div><dt>CPU</dt><dd>${running && a.cpu_pct != null ? `${Math.round(a.cpu_pct)}%` : "—"}</dd></div>
      <div><dt>Memory</dt><dd>${running && a.mem_mb != null ? html`${(a.mem_mb / 1024).toFixed(1)} GB${memPct != null ? html` <span class="muted">(${memPct}%)</span>` : ""}` : "—"}</dd></div>
      <div><dt>Uptime</dt><dd>${running ? uptime(a.uptime_s) : "—"}</dd></div>
    </dl>
    <div class="amp-actions">
      ${offline ? html`<span class="small muted">The AMP instance itself is stopped — start it in AMP.</span>`
      : amp.readonly ? html`<${Btn} size="sm" icon="terminal" onClick=${() => openConsole(server)}>Console<//><span class="small muted">AMP is read-only here</span>`
      : running || busy ? html`
        <${Btn} size="sm" icon="terminal" onClick=${() => openConsole(server)}>Console<//>
        <${Btn} size="sm" icon="rotate-ccw" disabled=${busy} onClick=${() => openRolling([server])}>Restart…<//>
        <${Btn} size="sm" kind="ghost" icon="power" disabled=${busy} onClick=${() => power(server, "stop")} aria-label=${`Stop ${server}`}>Stop<//>`
      : html`<${Btn} size="sm" kind="primary" icon="play" onClick=${() => power(server, "start")}>Start<//>
        <${Btn} size="sm" icon="terminal" onClick=${() => openConsole(server)}>Console<//>`}
    </div>
    ${running && a.players?.names?.length > 0 && html`<p class="small muted amp-names">Online: ${a.players.names.slice(0, 12).join(", ")}${a.players.names.length > 12 ? ` +${a.players.names.length - 12}` : ""}</p>`}
  </section>`;
}

// ---------- rolling restart ----------
export const openRolling = (servers) => setState({ rolling: { servers: [...servers], n: Date.now() } });

export function RollingHost() {
  const r = useStore(s => s.rolling);
  return r ? html`<${Rolling} key=${r.n} init=${r.servers} />` : null;
}

function Rolling({ init }) {
  const ov = useQuery("/overview").data;
  const amp = useAmpStatus();
  const eligible = (ov?.servers || []).filter(s => s.family !== "fabric" && amp.servers[s.id]);
  const [sel, setSel] = useState(new Set(init));
  const [warn, setWarn] = useState(true);
  const [empty, setEmpty] = useState(false);
  const [maxWait, setMaxWait] = useState(10);
  const [health, setHealth] = useState(true);
  const [busy, setBusy] = useState(false);
  const ref = useRef();
  const close = () => setState({ rolling: null });
  useEffect(() => {
    const prev = document.activeElement;
    ref.current?.querySelector("[data-autofocus]")?.focus();
    const k = (e) => e.key === "Escape" && !getState().confirm && close();
    document.addEventListener("keydown", k);
    return () => { document.removeEventListener("keydown", k); prev?.focus?.(); };
  }, []);
  const offline = (id) => amp.servers[id]?.state === "instance_offline";
  const chosen = eligible.filter(s => sel.has(s.id) && !offline(s.id));
  const players = chosen.reduce((a, s) => a + (amp.servers[s.id]?.players?.online || 0), 0);
  const go = async () => {
    setBusy(true);
    try {
      const r = await post("/restarts/rolling", {
        servers: chosen.map(s => s.id), warn_seconds: warn ? [60, 30, 10] : [],
        wait_for_empty: empty, max_wait_min: maxWait, run_health_check: health, stop_on_failure: true,
      });
      close();
      trackJob(r.job_id, { title: `Rolling restart · ${plural(chosen.length, "server")}` });
    } catch (e) { toast({ kind: "err", title: "Couldn't start the rolling restart", body: e.message }); }
    setBusy(false);
  };
  return html`<div class="scrim" onClick=${close}></div>
  <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="rr-t" ref=${ref} style="width:min(560px, calc(100vw - 32px))">
    <div class="dialog-body">
      <h2 id="rr-t" style="gap:8px"><${Icon} n="rotate-ccw" cls="i-sm" style="color:var(--fg-2)" />Rolling restart</h2>
      <p>Servers restart one at a time. The run stops at the first server whose plugins don't start cleanly; the rest are left running.</p>
      <div class="targets" role="group" aria-label="Servers to restart">
        ${eligible.map(s => { const on = sel.has(s.id); const a = amp.servers[s.id]; return html`<button type="button" class="target" aria-pressed=${on && !offline(s.id) ? "true" : "false"} disabled=${offline(s.id)}
          onClick=${() => setSel(x => { const n = new Set(x); on ? n.delete(s.id) : n.add(s.id); return n; })}>
          <span class="tick">${on && !offline(s.id) && html`<${Icon} n="check" />`}</span>${s.id}${a?.state === "running" ? html`<span class="target-why">· ${a.players?.online ? `${a.players.online} online` : "empty"}</span>` : html`<span class="target-why">· ${stateLabel(a?.state)}</span>`}</button>`; })}
      </div>
      <div class="stack" style="gap:8px">
        <${Check} checked=${warn} onChange=${setWarn}>Warn players in-game at 60 s, 30 s and 10 s<//>
        <div class="row wrap" style="gap:8px"><${Check} checked=${empty} onChange=${setEmpty}>Wait until the server is empty, up to<//>
          <select class="select" style="width:auto;height:30px" aria-label="Maximum wait" disabled=${!empty} value=${maxWait} onChange=${e => setMaxWait(+e.currentTarget.value)}>
            ${[5, 10, 15, 30, 60].map(m => html`<option value=${m}>${m} min</option>`)}</select></div>
        <${Check} checked=${health} onChange=${setHealth}>Check plugin startup after each restart, and stop on a failure<//>
      </div>
    </div>
    <div class="dialog-foot">
      <span class="small muted grow">${chosen.length ? `${plural(chosen.length, "server")}${players ? ` · ${plural(players, "player")} online` : ""}` : "Pick at least one server"}</span>
      <button type="button" class="btn" onClick=${close}>Cancel</button>
      <${Btn} kind="primary" icon="rotate-ccw" busy=${busy} disabled=${!chosen.length} onClick=${go} data-autofocus>Restart ${plural(chosen.length, "server")}<//>
    </div>
  </div>`;
}

// "Restart N servers now" for a restart checklist: only when AMP can do it.
export function RestartNow({ servers, label }) {
  const amp = useAmpStatus();
  const can = amp.on && amp.reachable && !amp.readonly && servers.some(s => amp.servers[s]);
  if (!can) return null;
  const ids = servers.filter(s => amp.servers[s]);
  return html`<${Btn} size="sm" icon="rotate-ccw" onClick=${() => openRolling(ids)}>${label || `Restart ${plural(ids.length, "server")} now…`}<//>`;
}

// ---------- console ----------
export const openConsole = (server) => setState({ console: { server, n: Date.now() } });

export function ConsoleHost() {
  const c = useStore(s => s.console);
  return c ? html`<${Console} key=${c.n} server=${c.server} />` : null;
}

function Console({ server }) {
  const ro = useAmpStatus().readonly;
  const [lines, setLines] = useState([]);
  const [cmd, setCmd] = useState("");
  const [hist, setHist] = useState([]);
  const [hi, setHi] = useState(-1);
  const [status, setStatus] = useState("connecting"); // connecting | live | closed | error
  const [follow, setFollow] = useState(true);
  const logRef = useRef(); const inRef = useRef(); const ref = useRef();
  const close = () => setState({ console: null });
  useEffect(() => {
    const prev = document.activeElement;
    setTimeout(() => inRef.current?.focus(), 0);
    const k = (e) => e.key === "Escape" && !getState().confirm && close();
    document.addEventListener("keydown", k);
    const stop = streamUrl(`/servers/${encodeURIComponent(server)}/console/stream`, {
      onOpen: () => setStatus("live"),
      onLine: (l) => setLines(x => [...x, l].slice(-2000)),
      onError: () => setStatus("error"),
    });
    return () => { stop(); document.removeEventListener("keydown", k); prev?.focus?.(); };
  }, []);
  useEffect(() => { if (follow && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [lines.length, follow]);
  const send = async (e, confirm = false) => {
    e?.preventDefault();
    const c = cmd.trim(); if (!c) return;
    try {
      await post(`/servers/${encodeURIComponent(server)}/console`, { command: c, ...(confirm ? { confirm: true } : {}) });
      setHist(h => [c, ...h.filter(x => x !== c)].slice(0, 50)); setHi(-1); setCmd("");
    } catch (err) {
      if (err.code === "confirm_required") {
        const ok = await confirmDialog({ danger: true, title: `Run “${c}” on ${server}?`, body: `${err.matched ? `“${err.matched}” is on the protected-command list` : "This is a protected command"} — it can disconnect players, stop the server or change permissions. The command is logged in the audit trail.`, confirmLabel: "Run command" });
        if (ok) return send(null, true);
      } else toast({ kind: "err", title: "Command not sent", body: err.message });
    }
  };
  const onKey = (e) => {
    if (e.key === "ArrowUp" && hist.length) { e.preventDefault(); const i = Math.min(hist.length - 1, hi + 1); setHi(i); setCmd(hist[i]); }
    else if (e.key === "ArrowDown" && hi >= 0) { e.preventDefault(); const i = hi - 1; setHi(i); setCmd(i < 0 ? "" : hist[i]); }
  };
  const cls = (l) => /\b(ERROR|SEVERE)\b|Exception/.test(l) ? "l-err" : /\bWARN/.test(l) ? "l-warn" : /^> /.test(l) ? "l-cmd" : "";
  return html`<div class="scrim" onClick=${close}></div>
  <aside class="sheet console-sheet" role="dialog" aria-modal="true" aria-labelledby="con-t" ref=${ref}>
    <header class="sheet-head"><div class="grow"><h2 id="con-t">${server} console</h2>
      <p class="small muted row" style="gap:6px"><i class=${`dot ${status === "live" ? "dot-ok pulse" : status === "error" ? "dot-danger" : "dot-muted"}`}></i>
        ${status === "live" ? "Live" : status === "error" ? "Disconnected — AMP unreachable or the stream ended" : "Connecting…"} · commands are recorded in Activity → Audit</p></div>
      <label class="switch small"><input type="checkbox" checked=${follow} onChange=${e => setFollow(e.currentTarget.checked)} />Follow</label>
      <${Btn} kind="ghost" icon="x" aria-label="Close console" onClick=${close} /></header>
    <pre class="log console-log" ref=${logRef} tabindex="0" aria-live="off" aria-label=${`${server} console output`}>${lines.length ? lines.map(l => html`<span class=${cls(l)}>${l + "\n"}</span>`) : html`<span class="log-empty">Waiting for output…</span>`}</pre>
    ${ro ? html`<p class="console-input small muted">AMP is read-only in this environment — the console is view-only.</p>` : html`<form class="console-input" onSubmit=${send}>
      <span class="mono muted" aria-hidden="true">›</span>
      <input ref=${inRef} class="input mono" aria-label=${`Command for ${server}`} placeholder="Type a command, e.g. list or say Restarting in 5 minutes" autocomplete="off" spellcheck="false"
        value=${cmd} onInput=${e => { setCmd(e.currentTarget.value); setHi(-1); }} onKeyDown=${onKey} />
      <${Btn} type="submit" kind="primary" icon="corner-down-left" disabled=${!cmd.trim()}>Send<//>
    </form>`}
  </aside>`;
}

// Keep a copy of the latest status for confirm dialogs outside components.
export function AmpCacheSync() {
  const amp = useAmpStatus();
  useEffect(() => { setState({ ampCache: amp.servers }); }, [amp.servers]);
  return null;
}
