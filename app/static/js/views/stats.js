// Stats: how fresh the network is, how fast updates land, and where the plugins come from.
// Every chart is a small SVG drawn at its real pixel size (crisp text, no scaling) from /stats.
// A section the backend can't compute honestly comes back null and its chart is hidden, never faked.
import { html, useState, useEffect, useRef, useLayoutEffect, useMemo } from "../lib.js";
import { useQuery, useStore } from "../store.js";
import { Icon, Btn, Skel, ErrorState, Empty, PageHead } from "../components/ui.js";
import { checkUpdates } from "../actions.js";
import { isActive } from "../jobs.js";
import { relTime, absTime, plural } from "../fmt.js";

const REDUCED = matchMedia("(prefers-reduced-motion: reduce)");
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const sum = (xs) => xs.reduce((a, b) => a + b, 0);
const n0 = (v) => Math.round(v).toLocaleString();
const pct = (a, b) => b ? Math.round(1000 * a / b) / 10 : 0;
// Days: tenths below 10 ("8.5"), whole days from 10 up ("361").
const dd = (x) => x == null ? "–" : x >= 10 ? Math.round(x) : Math.round(10 * x) / 10;
const gb = (b) => b == null ? "–" : b >= 1e9 ? `${(b / 1e9).toFixed(2)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(b >= 1e8 ? 0 : 1)} MB` : `${Math.round(b / 1e3)} KB`;
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const dayLabel = (d) => d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
// "YYYY-MM-DD" from the server is already a local calendar date (read it as-is); full timestamps show in local time.
const shortDate = (s) => s.length === 10
  ? new Date(s + "T00:00:00Z").toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" })
  : new Date(s).toLocaleDateString(undefined, { month: "short", day: "numeric" });

// Axis ticks on a 1/2/2.5/5 grid; counts never get fractional steps.
function ticks(max, n = 4) {
  if (!(max > 0)) return [0, 1];
  const raw = max / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = Math.max(1, [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw));
  return Array.from({ length: Math.ceil(max / step) + 1 }, (_, i) => i * step);
}

// Monotone cubic through points (no overshoot below zero between weeks).
function monotone(p) {
  if (p.length < 3) return "M" + p.map(q => q.join(",")).join("L");
  const n = p.length, dx = [], m = [], t = [];
  for (let i = 0; i < n - 1; i++) { dx[i] = p[i + 1][0] - p[i][0]; m[i] = (p[i + 1][1] - p[i][1]) / dx[i]; }
  t[0] = m[0]; t[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : 3 * (dx[i - 1] + dx[i]) / ((2 * dx[i] + dx[i - 1]) / m[i - 1] + (dx[i] + 2 * dx[i - 1]) / m[i]);
  let d = `M${p[0][0]},${p[0][1]}`;
  for (let i = 0; i < n - 1; i++) { const h = dx[i] / 3; d += `C${p[i][0] + h},${p[i][1] + h * t[i]} ${p[i + 1][0] - h},${p[i + 1][1] - h * t[i + 1]} ${p[i + 1][0]},${p[i + 1][1]}`; }
  return d;
}

// Arc from angle a to b (degrees, 0 = 12 o'clock, clockwise).
function arc(cx, cy, r, a, b) {
  const pt = (deg) => { const t = (deg - 90) * Math.PI / 180; return [cx + r * Math.cos(t), cy + r * Math.sin(t)]; };
  const [x1, y1] = pt(a), [x2, y2] = pt(b);
  return `M${x1.toFixed(2)},${y1.toFixed(2)}A${r},${r} 0 ${b - a > 180 ? 1 : 0} 1 ${x2.toFixed(2)},${y2.toFixed(2)}`;
}

// Delay (ms) for an animated mark, relative to its card's reveal.
const o = (ms) => `--o:${Math.round(ms)}ms`;

function useWidth() {
  const ref = useRef();
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    setW(el.clientWidth);
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

// Numbers count up once their figure scrolls into view. The final value reserves the width (no layout shift)
// and is what screen readers get.
// `linear` lets a number track a mark drawn at constant speed (the gauge arc).
function CountUp({ value, decimals = 0, delay = 0, dur = 1400, linear = false }) {
  const ref = useRef();
  const [v, setV] = useState(REDUCED.matches ? value : 0);
  useEffect(() => {
    if (REDUCED.matches) { setV(value); return; }
    let raf, t0, timer;
    const run = () => { timer = setTimeout(() => { const f = (t) => { t0 ??= t; const k = clamp((t - t0) / dur, 0, 1); setV(value * (linear ? k : 1 - (1 - k) ** 3)); if (k < 1) raf = requestAnimationFrame(f); }; raf = requestAnimationFrame(f); }, delay); };
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { io.disconnect(); run(); } });
    io.observe(ref.current);
    const safety = setTimeout(() => { io.disconnect(); setV(value); }, 6000);
    return () => { io.disconnect(); cancelAnimationFrame(raf); clearTimeout(timer); clearTimeout(safety); };
  }, [value]);
  const f = (x) => x.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  return html`<span class="cu" ref=${ref}><span class="cu-ghost" aria-hidden="true">${f(value)}</span><span class="cu-live" aria-hidden="true">${f(v)}</span><span class="sr-only">${f(value)}</span></span>`;
}

// One tab stop per chart: arrow keys step through the data, hover shows the same tooltip, and the
// value is announced. `draw(w, act, setAct)` returns { svg, at(i) → [x, y] }.
function Plot({ h, label, count, keys = { x: 1, y: 0 }, draw, tip, minW = 0, scrollEnd, scrollHint, cls = "" }) {
  const [ref, w] = useWidth();
  const [act, setAct] = useState(null);
  const scroller = useRef();
  const cw = Math.max(w, minW);
  useLayoutEffect(() => { if (scrollEnd && scroller.current) scroller.current.scrollLeft = 1e6; }, [w > 0]);
  const g = w ? draw(cw, act, setAct) : null;
  const step = (d) => setAct(i => clamp(i == null ? (d > 0 ? 0 : count - 1) : i + d, 0, count - 1));
  const onKey = (e) => {
    const d = { ArrowRight: keys.x, ArrowLeft: -keys.x, ArrowDown: keys.y, ArrowUp: -keys.y }[e.key];
    if (d) { e.preventDefault(); step(d); }
    else if (e.key === "Home") { e.preventDefault(); setAct(0); }
    else if (e.key === "End") { e.preventDefault(); setAct(count - 1); }
    else if (e.key === "Escape" && act != null) { e.stopPropagation(); setAct(null); }
  };
  useEffect(() => {
    // Keep the keyboard-selected mark visible inside a sideways-scrolling chart.
    if (act == null || !g || !scroller.current || !minW) return;
    const [x] = g.at(act), sc = scroller.current;
    if (x < sc.scrollLeft + 40 || x > sc.scrollLeft + sc.clientWidth - 40) sc.scrollLeft = x - sc.clientWidth / 2;
  }, [act]);
  // Announce the tooltip as one phrase ("Week of Sep 21, 15 jars changed, …").
  const tipRef = useRef(), liveRef = useRef();
  useEffect(() => { if (liveRef.current) liveRef.current.textContent = tipRef.current ? [...tipRef.current.children].map(c => c.textContent.trim()).join(", ") : ""; });
  const [x, y] = act != null && g ? g.at(act) : [0, 0];
  const sl = scroller.current?.scrollLeft || 0;
  const tx = clamp(x - sl, 80, Math.max(80, w - 80));
  const below = y < 64;
  return html`<div class=${"plot " + cls} ref=${ref} style=${`min-height:${h}px`} tabindex="0" role="group"
      aria-label=${`${label}. Arrow keys step through the values.`}
      onKeyDown=${onKey} onFocus=${e => act == null && e.currentTarget.matches(":focus-visible") && setAct(count - 1)}
      onBlur=${() => setAct(null)} onPointerLeave=${() => setAct(null)}>
    <div class="plot-scroll" ref=${scroller} style=${minW && w < minW ? "overflow-x:auto" : ""} onScroll=${() => act != null && setAct(null)}>
      ${g ? g.svg : null}
    </div>
    ${act != null && g && html`<div class=${"plot-tip" + (below ? " below" : "")} ref=${tipRef} style=${`left:${tx}px;top:${below ? y + 14 : y - 12}px`} aria-hidden="true">${tip(act)}</div>`}
    <div class="sr-only" aria-live="polite" ref=${liveRef}></div>
    ${scrollHint && minW && w && w < minW ? html`<p class="plot-hint" aria-hidden="true"><${Icon} n="chevron-left" cls="i-xs" />${scrollHint}</p>` : null}
  </div>`;
}

const Swatch = ({ c, line }) => html`<i class=${"sw" + (line ? " sw-line" : "")} style=${`--c:${c}`} aria-hidden="true"></i>`;
const Legend = ({ items }) => html`<div class="st-legend">${items.map(([c, label, line]) => html`<span><${Swatch} c=${c} line=${line} />${label}</span>`)}</div>`;

function DataTable({ caption, cols, rows }) {
  return html`<details class="st-data"><summary><${Icon} n="chevron-right" cls="i-xs" />Data table</summary>
    <div class="tbl-wrap"><table class="tbl"><caption class="sr-only">${caption}</caption>
      <thead><tr>${cols.map((c, i) => html`<th scope="col" class=${i ? "num" : ""}>${c}</th>`)}</tr></thead>
      <tbody>${rows.map(r => html`<tr>${r.map((v, i) => i ? html`<td class="num">${v}</td>` : html`<th scope="row">${v}</th>`)}</tr>`)}</tbody>
    </table></div></details>`;
}

function Card({ id, title, sub, span = 4, delay = 0, legend, table, children, cls = "" }) {
  return html`<section class=${`panel st-card span-${span} ${cls}`} aria-labelledby=${id + "-h"} style=${`--d:${delay}ms`} data-reveal>
    <header class="st-head"><div class="grow"><h2 id=${id + "-h"}>${title}</h2>${sub && html`<p>${sub}</p>`}</div>${legend}</header>
    <div class="st-body">${children}</div>
    ${table}
  </section>`;
}

// Update checks closer than an hour apart are one moment: keep the latest of each cluster. A trend needs
// two or more such moments spanning at least an hour, otherwise there is nothing to plot.
const HOUR_MS = 36e5;
function trendPoints(raw) {
  const kept = [];
  for (const t of [...raw].sort((a, b) => Date.parse(a.at) - Date.parse(b.at))) {
    if (kept.length && Date.parse(t.at) - Date.parse(kept[kept.length - 1].at) < HOUR_MS) kept[kept.length - 1] = t;
    else kept.push(t);
  }
  return kept.length >= 2 && Date.parse(kept[kept.length - 1].at) - Date.parse(kept[0].at) >= HOUR_MS ? kept : [];
}

// The /stats payload, reshaped for the charts. Sections the server can't fill honestly become null.
function shape(r) {
  const inv = r.inventory || { totals: {}, servers: [] }, fr = r.freshness, h = r.history, lag = r.lag, fp = r.footprint;
  const other = (c) => (c.unknown || 0) + (c.pinned || 0) + (c.ignored || 0);
  // Servers with plugins, per platform family ("bukkit": 9), so "9 of 9" means every server that can run it.
  const family = {};
  for (const s of inv.servers || []) if (s.jars) family[s.family] = (family[s.family] || 0) + 1;
  const health = Object.fromEntries((r.health?.per_server || []).map(x => [x.id, x]));
  return {
    generated_at: r.generated_at, indexing: r.indexing, last_check: fr?.last_check ?? null,
    totals: { servers: inv.totals.plugin_servers ?? inv.totals.servers, installs: inv.totals.jars, plugins: inv.totals.plugins, bytes: inv.totals.bytes },
    family,
    freshness: fr && fr.score != null && fr.last_check && fr.counts ? {
      score: fr.score, current: fr.counts.current, outdated: fr.counts.outdated, untracked: other(fr.counts),
      servers: (fr.servers || []).map(x => ({ id: x.id, current: x.current, outdated: x.outdated, untracked: other(x), health: health[x.id] })),
    } : null,
    sources: r.sources,
    spread: inv.sharing?.length ? { buckets: inv.sharing, top: inv.most_shared || [] } : null,
    // The server always sends every week and day; total 0 means there is no history yet.
    weeks: h?.total && h.weekly?.length ? h.weekly.map(w => ({ start: w.week_start, count: w.count, jobs: w.from_jobs || 0 })) : null,
    heatmap: h?.total && h.daily?.length ? { start: h.daily[0].date, days: h.daily.map(x => x.count) } : null,
    recent: h?.total ? h.recent_30d || null : null, history_note: h?.note,
    lag: lag?.samples ? { n: lag.samples, median_days: lag.median_days, p75_days: lag.p75_days, bins: lag.histogram } : null,
    behind: lag?.behind?.installs ? { n: lag.behind.installs, median_days: lag.behind.median_days, max_days: lag.behind.max_days, bins: lag.behind.histogram, plugins: lag.behind.plugins || [] } : null,
    trend: trendPoints(r.trend || []),
    footprint: fp ? {
      servers: (fp.servers || []).filter(x => x.bytes).map(x => ({ id: x.id, bytes: x.bytes })).sort((a, b) => b.bytes - a.bytes),
      jar_bytes: fp.jar_bytes, backups_bytes: fp.backups_bytes ?? null, backups: fp.backups,
      jobs: fp.jobs != null ? { total: fp.jobs, failed: fp.jobs_failed || 0, by_kind: fp.jobs_by_kind || {} } : null,
    } : null,
  };
}

// ---------------------------------------------------------------- hero

const GAUGE_MS = 1300;

function Gauge({ f, trend }) {
  const tracked = f.current + f.outdated, share = tracked ? f.current / tracked : 0;
  const A0 = -135, SWEEP = 270, gap = 1.6;
  const cut = A0 + SWEEP * share;
  const S = 216, c = S / 2, r = 88;
  // The number counts at the same constant speed as the green arc and lands with it.
  const curMs = Math.max(200, GAUGE_MS * share), outMs = Math.max(200, GAUGE_MS * (1 - share));
  return html`<div class="gauge">
    <svg viewBox=${`0 0 ${S} ${S - 24}`} width=${S} height=${S - 24} role="img"
      aria-label=${`Freshness ${f.score}%: ${f.current} of ${tracked} tracked installs are current and ${f.outdated} are outdated. ${f.untracked} installs have no update source, or are pinned or ignored, and are not counted.`}>
      <path d=${arc(c, c, r, A0, A0 + SWEEP)} class="g-track" />
      ${[0, 25, 50, 75, 100].map(t => { const a = (A0 + SWEEP * t / 100 - 90) * Math.PI / 180;
        return html`<line x1=${c + (r + 12) * Math.cos(a)} y1=${c + (r + 12) * Math.sin(a)} x2=${c + (r + 16) * Math.cos(a)} y2=${c + (r + 16) * Math.sin(a)} class="g-tick" />`; })}
      ${f.current > 0 && html`<path d=${arc(c, c, r, A0, cut - (f.outdated ? gap / 2 : 0))} pathLength="1" class="g-cur st-anim a-draw" style=${`${o(80)};--dur:${curMs}ms`} />`}
      ${f.outdated > 0 && html`<path d=${arc(c, c, r, cut + gap / 2, A0 + SWEEP)} pathLength="1" class="g-out st-anim a-draw" style=${`${o(80 + curMs)};--dur:${outMs}ms`} />`}
      <text x=${c - r * 0.72} y=${S - 30} class="g-end">0</text><text x=${c + r * 0.72} y=${S - 30} class="g-end" text-anchor="end">100</text>
    </svg>
    <div class="g-center"><div class="g-val"><${CountUp} value=${f.score} decimals=${f.score % 1 ? 1 : 0} delay=${80} dur=${curMs} linear=${true} /><small>%</small></div>
      <div class="g-cap">of tracked installs<br />are current</div></div>
  </div>
  <ul class="g-legend">
    <li><${Swatch} c="var(--ok)" /><b>${n0(f.current)}</b> current</li>
    <li><${Swatch} c="var(--update)" /><b>${n0(f.outdated)}</b> outdated</li>
    <li class="tip" tabindex="0" data-tip="No update source is known for these installs, or they're pinned or ignored, so they're left out of the score."><${Swatch} c="var(--fg-4)" /><b>${n0(f.untracked)}</b> installs not tracked</li>
  </ul>
  ${trend && trend.length >= 2 && html`<${TrendLine} trend=${trend} />`}`;
}

// Freshness % per update check when the server reports it, otherwise outdated installs per check.
function TrendLine({ trend: all }) {
  // Checks recorded before the server added score have none; with two or more scored checks, plot those.
  const scored = all.filter(t => t.score != null), pctMode = scored.length >= 2;
  const trend = pctMode ? scored : all;
  const vals = trend.map(t => pctMode ? t.score : t.outdated_installs);
  const W = 104, H = 30, lo = Math.min(...vals), hi = Math.max(...vals);
  const span = Math.max(pctMode ? 4 : 2, hi - lo), base = lo - span * 0.15;
  const x = (i) => 3 + i * (W - 6) / (vals.length - 1), y = (v) => H - 4 - (v - base) / (span * 1.3) * (H - 8);
  const pts = vals.map((v, i) => [x(i), y(v)]);
  const dv = Math.round(10 * (vals[vals.length - 1] - vals[0])) / 10;
  const good = pctMode ? dv >= 0 : dv <= 0;
  const since = `over ${plural(trend.length, "check")} since ${shortDate(trend[0].at)}`;
  return html`<div class="g-trend">
    <svg viewBox=${`0 0 ${W} ${H}`} width=${W} height=${H} role="img" aria-label=${pctMode ? `Freshness across the last ${trend.length} update checks: ${vals.map(v => v + "%").join(", ")}` : `Outdated installs across the last ${trend.length} update checks: ${vals.join(", ")}`}>
      <path d=${monotone(pts)} pathLength="1" class=${"tl-line st-anim a-draw" + (!dv ? " flat" : good ? "" : " bad")} style=${o(GAUGE_MS)} />
      <circle cx=${pts[pts.length - 1][0]} cy=${pts[pts.length - 1][1]} r="3.5" class=${"tl-dot st-anim a-pop" + (!dv ? " flat" : good ? "" : " bad")} style=${o(GAUGE_MS + 1300)} />
    </svg>
    <span>${!dv ? html`<b class="flat">No change</b><br />${since}` : pctMode ? html`<b class=${good ? "up" : "down"}>${dv > 0 ? "+" : "−"}${Math.abs(dv)} pts</b><br />${since}`
      : html`Outdated installs <b class=${good ? "up" : "down"}>${vals[0]} → ${vals[vals.length - 1]}</b><br />${since}`}</span>
  </div>`;
}

function Kpi({ label, value, unit, decimals = 0, sub, i, children, tip }) {
  return html`<div class="kpi" style=${o(120 + i * 90)}>
    <div class="kpi-label">${label}${tip && html` <span class="tip kpi-q" tabindex="0" data-tip=${tip} aria-label=${tip}><${Icon} n="info" cls="i-xs" /></span>`}</div>
    <div class="kpi-val"><${CountUp} value=${value} decimals=${decimals} delay=${120 + i * 90} />${unit && html`<small>${unit}</small>`}</div>
    ${sub && html`<div class="kpi-sub">${sub}</div>`}
    ${children}
  </div>`;
}

// Who made the last 30 days of changes: PlugWarden jobs vs everything else (same colors as the weekly chart).
function SplitBar({ jobs, other }) {
  const t = jobs + other || 1;
  return html`<div class="kpi-split" aria-hidden="true">
    <div class="st-stack st-stack-lg st-anim a-grow-x" style=${o(420)}>
      ${jobs > 0 && html`<span style=${`flex:${jobs} 1 0;background:${C_JOBS}`}></span>`}${other > 0 && html`<span style=${`flex:${other} 1 0;background:${C_OTHER}`}></span>`}</div>
    <div class="st-legend"><span><${Swatch} c=${C_JOBS} />${Math.round(100 * jobs / t)}% by PlugWarden</span><span><${Swatch} c=${C_OTHER} />${Math.round(100 * other / t)}% other</span></div>
  </div>`;
}

// Where the median and the 3-in-4 mark fall (a one-line box plot).
function LagRange({ median, p75 }) {
  const top = Math.max(p75 * 1.35, 1);
  return html`<div class="kpi-range" aria-hidden="true">
    <span class="kr-bar st-anim a-grow-x" style=${`width:${100 * p75 / top}%;${o(500)}`}></span>
    <span class="kr-med st-anim a-fade" style=${`left:${100 * median / top}%;${o(1100)}`}></span>
  </div>
  <div class="kpi-range-lab" aria-hidden="true"><span>0</span><span style=${`left:${100 * median / top}%`}>median</span><span style=${`left:${100 * p75 / top}%`}>3 in 4</span></div>`;
}

// The hero adds "Shared plugins" only when fewer than four regular figures exist (before the first check).
const heroShowsShared = (d) => !!d.spread && [d.recent, d.lag, d.behind, true].filter(Boolean).length < 4;

function Hero({ d }) {
  const checking = useStore(s => s.jobs.some(j => isActive(j.status) && j.title === "Update check"));
  const f = d.freshness, t = d.totals;
  const shared = d.spread ? sum(d.spread.buckets.filter(b => b.servers >= 2).map(b => b.plugins)) : null;
  const kpis = [
    d.recent && ((i) => html`<${Kpi} i=${i} label="Jar changes · 30 days" value=${d.recent.changes}
      tip="Plugin jars added or replaced on any server in the last 30 days, whoever made the change."
      sub=${html`<b>${n0(d.recent.from_jobs)}</b> made by PlugWarden jobs`}>
      ${d.recent.changes > 0 && html`<${SplitBar} jobs=${d.recent.from_jobs} other=${d.recent.changes - d.recent.from_jobs} />`}<//>`),
    d.lag && ((i) => html`<${Kpi} i=${i} label="Median update lag" value=${dd(d.lag.median_days)} decimals=${dd(d.lag.median_days) % 1 ? 1 : 0} unit=${d.lag.median_days === 1 ? "day" : "days"}
      tip="Days from a version's release to the day it landed on a server."
      sub=${html`3 in 4 landed within <b>${dd(d.lag.p75_days)} days</b> · ${plural(d.lag.n, "update")} measured`}><${LagRange} median=${d.lag.median_days} p75=${d.lag.p75_days} /><//>`),
    d.behind && ((i) => html`<${Kpi} i=${i} label="Waiting updates · median age" value=${dd(d.behind.median_days)} decimals=${dd(d.behind.median_days) % 1 ? 1 : 0} unit="days"
      tip="How long the newer version has been out, for installs that are still outdated."
      sub=${html`oldest waiting <b>${dd(d.behind.max_days)} days</b> · ${plural(d.behind.n, "install")}`} />`),
    (i) => html`<${Kpi} i=${i} label="Network" value=${t.installs} unit="plugin installs"
      sub=${html`<b>${n0(t.plugins)}</b> distinct plugins on <b>${plural(t.servers, "server")}</b>`} />`,
    // Before the first check there is no lag or waiting data; this real figure keeps the band from looking empty.
    heroShowsShared(d) && ((i) => html`<${Kpi} i=${i} label="Shared plugins" value=${shared} unit="plugins"
      sub=${html`run on two or more servers · <b>${t.plugins - shared}</b> on just one`} />`),
  ].filter(Boolean).slice(0, 4);
  return html`<section class="st-hero" aria-label="Headline figures" data-reveal>
    <div class="st-gauge">
      ${f ? html`<${Gauge} f=${f} trend=${d.trend} />`
        : html`<div class="g-none"><${Icon} n="circle-dashed" cls="i-xl" /><b>No update check yet</b>
            <p>Freshness needs one check against Modrinth, Hangar, Spiget and GitHub. Checking never installs anything.</p>
            <${Btn} kind="primary" icon="refresh-cw" busy=${checking} onClick=${checkUpdates}>${checking ? "Checking…" : "Run first check"}<//></div>`}
    </div>
    <div class=${`st-kpis n${kpis.length}`}>${kpis.map((k, i) => k(i))}</div>
  </section>`;
}

// ---------------------------------------------------------------- charts

const C_JOBS = "var(--cat-1)", C_OTHER = "var(--accent)";

// Freshness by server: one 100% bar per server (current + outdated = its tracked installs), so the green
// length is exactly the % beside it. Worst first. Untracked installs are a count, not part of the bar.
function FreshBy({ servers }) {
  const rows = servers.map(s => ({ ...s, tracked: s.current + s.outdated }))
    .sort((a, b) => (!a.tracked - !b.tracked) || (a.current / a.tracked - b.current / b.tracked) || a.id.localeCompare(b.id));
  return html`<ol class="fbs">${rows.map((s, i) => { const h = s.health; const p = s.tracked ? Math.round(100 * s.current / s.tracked) : null;
    return html`<li>
      <a class="fbs-name" href=${`#/servers/${encodeURIComponent(s.id)}`}>${s.id}</a>
      ${s.tracked ? html`<span class="fbs-bar st-stack st-anim a-grow-x" aria-hidden="true" style=${o(120 + i * 40)}>
          ${s.current > 0 && html`<span style=${`flex:${s.current} 1 0;background:var(--ok)`}></span>`}${s.outdated > 0 && html`<span class="fbs-out" style=${`flex:${s.outdated} 1 0`}></span>`}</span>
        <b class="fbs-pct">${p}%</b>
        <span class="fbs-meta num">${s.current}/${s.tracked}${s.untracked ? html`<span class="hide-sm"> · +${s.untracked} not tracked</span>` : ""}</span>`
        : html`<span class="fbs-none">Nothing tracked yet</span>`}
      <span class="fbs-flags">${h?.failed > 0 ? html`<span class="tag tag-danger" title=${`${plural(h.failed, "plugin")} not running after the last start`}><${Icon} n="circle-x" />${h.failed} not running</span>`
        : h?.status === "not_running" ? html`<span class="tag tag-danger"><${Icon} n="circle-x" />not running</span>` : ""}
        ${h?.warnings > 0 && html`<span class="tag tag-warn" title=${`${plural(h.warnings, "plugin")} logged warnings at the last start`}><${Icon} n="triangle-alert" />${plural(h.warnings, "warning")}</span>`}</span>
      <span class="sr-only">${p != null ? `${p}% current: ${s.current} of ${s.tracked} tracked installs, ${s.outdated} outdated` : "no tracked installs"}${s.untracked ? `, ${s.untracked} not tracked` : ""}.</span>
    </li>`; })}</ol>`;
}

function Weekly({ weeks }) {
  const max = Math.max(1, ...weeks.map(w => w.count));
  const tk = ticks(max, 3), top = tk[tk.length - 1];
  const total = sum(weeks.map(w => w.count)), jobs = sum(weeks.map(w => w.jobs));
  // Label the two busiest weeks with their count and date only.
  const peaks = weeks.map((w, i) => i).filter(i => weeks[i].count > 0).sort((a, b) => weeks[b].count - weeks[a].count).slice(0, 2);
  const H = 226;
  const draw = (w, act, setAct) => {
    const L = 34, R = 8, T = 24, B = 22, iw = w - L - R, ih = H - T - B, slot = iw / weeks.length, bw = Math.max(2, slot - Math.max(1.5, slot * 0.28));
    const x = (i) => L + i * slot + (slot - bw) / 2, y = (v) => T + ih - v / top * ih;
    // Label each month where its first week starts. The first bar's month (usually a partial one) is
    // labelled only when it doesn't collide with the next month's label.
    const starts = weeks.map((wk, i) => i).filter(i => !i || weeks[i].start.slice(5, 7) !== weeks[i - 1].start.slice(5, 7));
    const months = starts.filter((i, k) => i || starts[1] == null || x(starts[1]) - x(0) >= 30)
      .map(i => html`<text x=${x(i)} y=${H - 6} class="ax">${MON[+weeks[i].start.slice(5, 7) - 1]}</text>`);
    const labs = peaks.filter((p, k) => k === 0 || Math.abs(x(p) - x(peaks[0])) > 90);
    return {
      at: (i) => [x(i) + bw / 2, y(weeks[i].count)],
      svg: html`<svg width=${w} height=${H} role="img" aria-label=${`Plugin jars changed per week over ${weeks.length} weeks: ${total} in all, ${jobs} made by PlugWarden jobs.${peaks.length ? ` Busiest: ${peaks.map(p => `${weeks[p].count} in the week of ${shortDate(weeks[p].start)}`).join(", ")}.` : ""}`}>
        ${tk.map(t => html`<line x1=${L} x2=${w - R} y1=${y(t)} y2=${y(t)} class=${t ? "grid" : "base"} /><text x=${L - 8} y=${y(t) + 4} class="ax" text-anchor="end">${t}</text>`)}
        ${months}
        ${weeks.map((wk, i) => wk.count > 0 && html`<g class=${"st-anim a-grow-y" + (act != null && act !== i ? " dim" : "")} style=${o(60 + i * 14)}>
          ${wk.jobs > 0 && html`<rect x=${x(i)} y=${y(wk.jobs)} width=${bw} height=${y(0) - y(wk.jobs)} rx=${Math.min(2, bw / 2)} style=${`fill:${C_JOBS}`} />`}
          ${wk.count - wk.jobs > 0 && html`<rect x=${x(i)} y=${y(wk.count)} width=${bw} height=${Math.max(1, y(wk.jobs) - y(wk.count) - (wk.jobs ? 1.5 : 0))} rx=${Math.min(2, bw / 2)} style=${`fill:${C_OTHER}`} />`}</g>`)}
        ${labs.map(p => html`<text x=${clamp(x(p) + bw / 2, L + 34, w - R - 34)} y=${y(weeks[p].count) - 7} class="ax ax-strong st-anim a-fade" text-anchor="middle" style=${o(900)}>${weeks[p].count} · ${shortDate(weeks[p].start)}</text>`)}
        ${weeks.map((_, i) => html`<rect x=${L + i * slot} y=${T} width=${slot} height=${ih} class="hit" onPointerEnter=${() => setAct(i)} />`)}
      </svg>`,
    };
  };
  return html`<${Plot} h=${H} label="Plugin jar changes per week" count=${weeks.length} draw=${draw}
    tip=${(i) => html`<b>Week of ${shortDate(weeks[i].start)}</b>
      <span>${plural(weeks[i].count, "jar")} changed</span>
      <span><${Swatch} c=${C_JOBS} />${weeks[i].jobs} by PlugWarden jobs</span>
      <span><${Swatch} c=${C_OTHER} />${weeks[i].count - weeks[i].jobs} other</span>`} />`;
}

// Fixed, readable heat steps (1, 2–5, 6–20, 21+), trimmed to what the data reaches.
const HEAT = [1, 2, 6, 21];
const heatRange = (i, cuts) => i === 0 ? "0" : cuts[i] ? (cuts[i] - 1 > cuts[i - 1] ? `${cuts[i - 1]}–${cuts[i] - 1}` : `${cuts[i - 1]}`) : `${cuts[i - 1]}+`;

function Heatmap({ hm }) {
  const days = hm.days, start = new Date(hm.start + "T00:00:00Z");
  const off = (start.getUTCDay() + 6) % 7; // Monday-first rows
  const cols = Math.ceil((days.length + off) / 7);
  const max = Math.max(...days);
  const cuts = HEAT.filter(c => c <= Math.max(1, max));
  const level = (v) => v ? cuts.filter(c => v >= c).length : 0;
  const date = (i) => new Date(start.getTime() + i * 864e5);
  const H = 18 + 7 * 11;
  const draw = (w, act, setAct) => {
    const L = 34, T = 18, s = clamp(Math.floor((w - L - 2) / cols), 11, 21), cell = s - 3;
    const width = L + cols * s;
    const px = (i) => L + Math.floor((i + off) / 7) * s, py = (i) => T + ((i + off) % 7) * s;
    let lastM = -1, lastX = -99;
    const months = [];
    for (let i = 0; i < days.length; i++) {
      const dt = date(i), m = dt.getUTCMonth();
      if (m !== lastM && dt.getUTCDate() <= 7 && px(i) - lastX > 30) { months.push(html`<text x=${px(i)} y=${11} class="ax">${MON[m]}</text>`); lastX = px(i); }
      if (m !== lastM) lastM = m;
    }
    return {
      at: (i) => [px(i) + cell / 2, py(i)],
      svg: html`<svg width=${width} height=${T + 7 * s} role="img" aria-label=${`Daily plugin changes over the last ${days.length} days: ${sum(days)} changes on ${days.filter(Boolean).length} days.`}>
        ${months}
        ${["Mon", "", "Wed", "", "Fri", "", ""].map((d, r) => d && html`<text x=${L - 6} y=${T + r * s + cell - 1} class="ax" text-anchor="end">${d}</text>`)}
        ${days.map((v, i) => html`<rect x=${px(i)} y=${py(i)} width=${cell} height=${cell} rx="2.5" class=${`hm l${level(v)}${act === i ? " on" : ""} st-anim a-pop`}
          style=${o(Math.floor((i + off) / 7) * 14)} onPointerEnter=${() => setAct(i)} />`)}
      </svg>`,
    };
  };
  return html`<${Plot} h=${H} label="Daily plugin changes, last 12 months" count=${days.length} keys=${{ x: 7, y: 1 }} draw=${draw} minW=${34 + cols * 11} scrollEnd=${true}
    scrollHint="Scroll left for earlier months"
    tip=${(i) => html`<b>${dayLabel(date(i))}</b><span>${days[i] ? plural(days[i], "change") : "No changes"}</span>`} />
  <div class="hm-legend"><span class="muted">Changes a day</span>${[0, ...cuts].map((_, l) => html`<span class="hm-step"><i class=${"hm l" + l} aria-hidden="true"></i>${heatRange(l, cuts)}</span>`)}</div>`;
}

function heatStats(days, start) {
  let best = 0, run = 0, busiest = 0;
  days.forEach((v, i) => { run = v ? run + 1 : 0; best = Math.max(best, run); if (v > days[busiest]) busiest = i; });
  const d = new Date(new Date(start + "T00:00:00Z").getTime() + busiest * 864e5);
  return { total: sum(days), active: days.filter(Boolean).length, streak: best, busiest: days[busiest] ? `${dayLabel(d)} (${days[busiest]})` : "–" };
}

// ---- sources donut: only sources that matched something are drawn; the rest are named in one line.
const IDENTIFIED = "Identified: PlugWarden knows where the plugin's updates come from. Counted per plugin, like the Dashboard's check summary.";
const SOURCES = [["modrinth", "Modrinth", "var(--cat-1)"], ["spiget", "Spiget", "var(--cat-2)"], ["github", "GitHub", "var(--cat-3)"], ["hangar", "Hangar", "var(--cat-4)"], ["untracked", "No source found", "var(--fg-4)"]];

function Sources({ s }) {
  const all = SOURCES.map(([k, label, c]) => ({ k, label, c, v: s[k] || 0 }));
  const rows = all.filter(x => x.v), none = all.filter(x => !x.v && x.k !== "untracked");
  const total = sum(rows.map(r => r.v)), tracked = total - (s.untracked || 0);
  const [act, setAct] = useState(null);
  const S = 160, c = S / 2, r = 63, gap = 1.8;
  let a = 0;
  const segs = rows.map((x, i) => { const sweep = 360 * x.v / total; const seg = { ...x, a0: a, a1: a + sweep, i }; a += sweep; return seg; });
  const share = (v) => Math.round(100 * v / Math.max(1, total));
  return html`<div class="donut-wrap">
    <div class="donut">
      <svg viewBox=${`0 0 ${S} ${S}`} width=${S} height=${S} role="img" aria-label=${`Update sources for ${total} distinct plugins: ${rows.map(x => `${x.label} ${x.v}`).join(", ")}${none.length ? `; no matches on ${none.map(x => x.label).join(", ")}` : ""}.`}>
        ${segs.map((x) => html`<path d=${arc(c, c, r, x.a0 + (segs.length > 1 ? gap / 2 : 0), Math.max(x.a0 + 0.5, x.a1 - (segs.length > 1 ? gap / 2 : 0)))} pathLength="1"
          class=${"dn st-anim a-draw" + (act != null && act !== x.i ? " dim" : "")} style=${`stroke:${x.c};${o(150 + 900 * x.a0 / 360)};--dur:${Math.max(300, 900 * (x.a1 - x.a0) / 360)}ms`}
          onPointerEnter=${() => setAct(x.i)} onPointerLeave=${() => setAct(null)} />`)}
      </svg>
      <div class="dn-center">${act == null
        ? html`<b><${CountUp} value=${share(tracked)} delay=${200} />%</b><span>of plugins<br />identified</span>`
        : html`<b>${rows[act].v}</b><span>${rows[act].label}<br />${share(rows[act].v)}% of plugins</span>`}</div>
    </div>
    <div class="dn-side">
      <ul class="dn-legend">${rows.map((x, i) => html`<li class=${act === i ? "on" : ""} tabindex="0" aria-label=${`${x.label}: ${plural(x.v, "plugin")}, ${share(x.v)}%`}
        onPointerEnter=${() => setAct(i)} onPointerLeave=${() => setAct(null)} onFocus=${() => setAct(i)} onBlur=${() => setAct(null)}>
        <${Swatch} c=${x.c} /><span class="grow">${x.label}</span><b class="num">${x.v}</b><span class="num muted">${share(x.v)}%</span></li>`)}</ul>
      ${none.length > 0 && html`<p class="dn-none">Also checked: ${none.map(x => x.label).join(", ")} (no matches)</p>`}
    </div>
  </div>`;
}

// ---- histograms (update lag, waiting updates). The server labels its bins ("<1d", "1–3mo", ">3mo").
const binLabel = ({ lo, hi, bucket }) => bucket || (hi == null ? `${lo}d+` : lo === 0 ? `<${hi}d` : `${lo}–${hi}d`);
const binLong = ({ lo, hi }) => hi == null ? `over ${lo} days` : lo === 0 ? `under ${plural(hi, "day")}` : `${lo} to ${hi} days`;

function Histogram({ h, color, unit, what, H = 200 }) {
  const bins = h.bins, max = Math.max(1, ...bins.map(b => b.count));
  const tk = ticks(max, 3), top = tk[tk.length - 1];
  // Marker for the median, interpolated inside its bin.
  const mi = h.median_days == null ? -1 : bins.findIndex(b => h.median_days >= b.lo && (b.hi == null || h.median_days < b.hi));
  const draw = (w, act, setAct) => {
    const L = 28, R = 6, T = 22, B = 24, iw = w - L - R, ih = H - T - B, slot = iw / bins.length, bw = Math.max(6, slot - Math.max(4, slot * 0.22));
    const x = (i) => L + i * slot + (slot - bw) / 2, y = (v) => T + ih - v / top * ih;
    const mb = bins[mi], mx = mi < 0 ? null : L + mi * slot + slot * (mb.hi == null ? 0.25 : (h.median_days - mb.lo) / (mb.hi - mb.lo));
    return {
      at: (i) => [x(i) + bw / 2, y(bins[i].count)],
      svg: html`<svg width=${w} height=${H} role="img" aria-label=${`${what} for ${h.n} ${unit}: ${bins.map(b => `${binLong(b)} ${b.count}`).join(", ")}. Median ${dd(h.median_days)} days.`}>
        ${tk.map(t => html`<line x1=${L} x2=${w - R} y1=${y(t)} y2=${y(t)} class=${t ? "grid" : "base"} /><text x=${L - 7} y=${y(t) + 4} class="ax" text-anchor="end">${t}</text>`)}
        ${bins.map((b, i) => html`<rect x=${x(i)} y=${y(b.count)} width=${bw} height=${Math.max(0, y(0) - y(b.count))} rx=${Math.min(4, bw / 2)} class=${"bar st-anim a-grow-y" + (act != null && act !== i ? " dim" : "")}
            style=${`fill:${color};${o(100 + i * 60)}`} />
          <text x=${x(i) + bw / 2} y=${H - 8} class="ax" text-anchor="middle">${binLabel(b)}</text>`)}
        ${mx != null && html`<g class="st-anim a-fade" style=${o(900)}><line x1=${mx} x2=${mx} y1=${T - 6} y2=${T + ih} class="marker" />
          <text x=${clamp(mx, L + 34, w - R - 34)} y=${T - 10} class="ax ax-strong" text-anchor="middle">median ${dd(h.median_days)}d</text></g>`}
        ${bins.map((b, i) => html`<rect x=${L + i * slot} y=${T} width=${slot} height=${ih} class="hit" onPointerEnter=${() => setAct(i)} />`)}
      </svg>`,
    };
  };
  return html`<${Plot} h=${H} label=${what} count=${bins.length} draw=${draw}
    tip=${(i) => html`<b>${binLong(bins[i])}</b><span>${plural(bins[i].count, unit.replace(/s$/, ""))} · ${pct(bins[i].count, h.n)}%</span>`} />
  <p class="st-note-sm">Bins widen to the right: from under a day to over ${bins[bins.length - 1].lo} days.</p>`;
}

function Spread({ sp, servers }) {
  const byN = Object.fromEntries(sp.buckets.map(b => [b.servers, b.plugins]));
  const hiN = Math.max(servers, ...sp.buckets.map(b => b.servers));
  const bins = Array.from({ length: hiN }, (_, i) => ({ n: i + 1, v: byN[i + 1] || 0 }));
  const max = Math.max(1, ...bins.map(b => b.v)), tk = ticks(max, 3), top = tk[tk.length - 1];
  const H = 200;
  const draw = (w, act, setAct) => {
    const L = 28, R = 6, T = 14, B = 38, iw = w - L - R, ih = H - T - B, slot = iw / bins.length, bw = Math.max(5, slot - Math.max(3, slot * 0.25));
    const x = (i) => L + i * slot + (slot - bw) / 2, y = (v) => T + ih - v / top * ih;
    return {
      at: (i) => [x(i) + bw / 2, y(bins[i].v)],
      svg: html`<svg width=${w} height=${H} role="img" aria-label=${`Distinct plugins by how many servers run them: ${bins.filter(b => b.v).map(b => `${b.n} servers ${b.v}`).join(", ")}.`}>
        ${tk.map(t => html`<line x1=${L} x2=${w - R} y1=${y(t)} y2=${y(t)} class=${t ? "grid" : "base"} /><text x=${L - 7} y=${y(t) + 4} class="ax" text-anchor="end">${t}</text>`)}
        ${bins.map((b, i) => html`<rect x=${x(i)} y=${y(b.v)} width=${bw} height=${Math.max(0, y(0) - y(b.v))} rx=${Math.min(4, bw / 2)} class=${"bar st-anim a-grow-y" + (act != null && act !== i ? " dim" : "")}
            style=${`fill:var(--cat-4);${o(100 + i * 45)}`} />
          ${(bins.length <= 12 || i % 2 === 0 || i === bins.length - 1) && html`<text x=${x(i) + bw / 2} y=${T + ih + 16} class="ax" text-anchor="middle">${b.n}</text>`}`)}
        <text x=${L + iw / 2} y=${H - 4} class="ax" text-anchor="middle">servers running the plugin</text>
        ${bins.map((b, i) => html`<rect x=${L + i * slot} y=${T} width=${slot} height=${ih} class="hit" onPointerEnter=${() => setAct(i)} />`)}
      </svg>`,
    };
  };
  return html`<${Plot} h=${H} label="Plugins by number of servers" count=${bins.length} draw=${draw}
    tip=${(i) => html`<b>On ${plural(bins[i].n, "server")}</b><span>${plural(bins[i].v, "plugin")}</span>`} />`;
}

// Denominator = servers of the plugin's own platform (Bukkit plugins can't run on the Velocity proxy).
function Shared({ top, family, servers }) {
  const of = (t) => family[t.key.split(":")[0]] || servers;
  return html`<ol class="bars-h">${top.map((t, i) => html`<li aria-label=${`${t.name}: on ${t.servers} of ${of(t)} ${t.key.split(":")[0]} servers`}>
    <span class="bh-name ellipsis" title=${t.name} aria-hidden="true">${t.name}</span>
    <span class="bh-track" aria-hidden="true"><span class="bh-fill st-anim a-grow-x" style=${`width:${100 * Math.min(1, t.servers / of(t))}%;${o(80 + i * 50)}`}></span></span>
    <span class="bh-val num" aria-hidden="true">${t.servers}<span class="muted"> of ${of(t)}</span></span></li>`)}</ol>`;
}

function Waiting({ b, H }) {
  return html`<div class="wait-wrap"><div><${Histogram} h=${b} color="var(--update)" unit="installs" what="Days a newer version has been available" H=${H} /></div>
    ${b.plugins.length > 0 && html`<div class="waiting"><span class="kpi-label">Waiting longest</span><ol>${b.plugins.slice(0, 3).map(p => html`<li>
      <span class="ellipsis"><b>${p.name}</b> <span class="muted mono">${p.to_version}</span></span><span class="muted num">${plural(p.servers, "server")}</span><span class="num w-days">${dd(p.days)} d</span></li>`)}</ol></div>`}</div>`;
}

const KINDS = [["update-apply", "Updates", "var(--cat-1)"], ["deploy", "Deploys", "var(--cat-2)"], ["remove", "Removals", "var(--cat-3)"], ["undo", "Undos", "var(--cat-4)"], ["update-check", "Checks", "var(--fg-4)"]];

function Footprint({ fp, t }) {
  const j = fp.jobs;
  const kinds = j ? [...KINDS.map(([k, l, c]) => ({ k, l, c, v: j.by_kind?.[k] || 0 })),
    { k: "other", l: "Other", c: "var(--line-strong)", v: Math.max(0, j.total - sum(KINDS.map(([k]) => j.by_kind?.[k] || 0))) }].filter(x => x.v) : [];
  return html`<div class="fp">
    <div class="fp-fig"><span class="kpi-label">Plugin jars on disk</span><b>${gb(fp.jar_bytes ?? t.bytes)}</b><span class="kpi-sub">${plural(t.installs, "jar")} across ${plural(t.servers, "server")}</span></div>
    ${fp.backups_bytes != null && html`<div class="fp-fig"><span class="kpi-label">Backups kept</span><b>${gb(fp.backups_bytes)}</b><span class="kpi-sub">${fp.backups ? `${plural(fp.backups, "backup set")}, ` : ""}for undo</span></div>`}
    ${j && html`<div class="fp-fig"><span class="kpi-label">Jobs run</span><b><${CountUp} value=${j.total} /></b><span class="kpi-sub">${j.failed ? html`<span class="fp-fail">${j.failed} failed</span>` : "none failed"}</span></div>`}
    ${kinds.length > 0 && html`<div class="fp-jobs"><span class="kpi-label">Jobs by kind</span>
      <div class="st-stack st-stack-lg st-anim a-grow-x" style=${o(200)} role="img" aria-label=${`Jobs by kind: ${kinds.map(x => `${x.l} ${x.v}`).join(", ")}`}>
        ${kinds.map(x => html`<span style=${`flex:${x.v} 1 0;background:${x.c}`} title=${`${x.l}: ${x.v}`}></span>`)}</div>
      <div class="st-legend">${kinds.map(x => html`<span><${Swatch} c=${x.c} />${x.l} <b class="num">${x.v}</b></span>`)}</div></div>`}
  </div>`;
}

function Disk({ servers }) {
  const max = Math.max(1, ...servers.map(s => s.bytes));
  return html`<ol class="bars-h" style=${`--rows:${Math.ceil(servers.length / 2)}`}>${servers.map((s, i) => html`<li>
    <span class="bh-name ellipsis" title=${s.id}>${s.id}</span>
    <span class="bh-track" aria-hidden="true"><span class="bh-fill bh-disk st-anim a-grow-x" style=${`width:${100 * s.bytes / max}%;${o(80 + i * 40)}`}></span></span>
    <span class="bh-val num">${gb(s.bytes)}</span></li>`)}</ol>`;
}

// ---------------------------------------------------------------- page

// Cards animate when they scroll into view; anything still hidden after a few seconds is shown as-is.
function useReveal(ready) {
  useEffect(() => {
    if (!ready) return;
    const els = [...document.querySelectorAll("#main [data-reveal]:not(.is-in)")];
    const show = (el) => el.classList.add("is-in");
    if (REDUCED.matches || !("IntersectionObserver" in window)) { els.forEach(show); return; }
    const io = new IntersectionObserver((es) => es.forEach(e => { if (e.isIntersecting) { show(e.target); io.unobserve(e.target); } }), { threshold: 0.12 });
    els.forEach(el => io.observe(el));
    const t = setTimeout(() => els.forEach(show), 5000);
    return () => { io.disconnect(); clearTimeout(t); };
  }, [ready]);
}

function Loading() {
  return html`<div aria-busy="true" aria-label="Loading stats">
    <div class="st-hero"><div class="st-gauge"><${Skel} w="180px" h=${180} r=${90} /></div>
      <div class="st-kpis n4">${[0, 1, 2, 3].map(() => html`<div class="kpi"><${Skel} w="50%" h=${10} /><${Skel} w="40%" h=${30} style="margin-top:10px" /><${Skel} w="70%" h=${9} style="margin-top:10px" /></div>`)}</div></div>
    <div class="st-grid">${[[8, 420], [4, 420], [12, 220]].map(([s, h]) => html`<div class=${`panel st-card span-${s}`}><div class="st-head"><${Skel} w="40%" h=${12} /></div><div class="st-body"><${Skel} h=${h - 60} /></div></div>`)}</div>
  </div>`;
}

export function Stats() {
  const q = useQuery("/stats");
  const d = useMemo(() => q.data && shape(q.data), [q.data]);
  useReveal(!!d);
  const hidden = useMemo(() => !d ? [] : [
    !d.freshness && "freshness, update lag and waiting updates (no update check yet)",
    d.freshness && !d.lag && "update lag (no applied updates with a known release date yet)",
    d.freshness && d.trend.length < 2 && "freshness trend (needs update checks at least an hour apart)",
    !d.weeks && "weekly changes and daily activity (no jar or job history yet)",
  ].filter(Boolean), [d]);

  if (q.error && !d) return html`<${PageHead} title="Stats" /><${ErrorState} error=${q.error} retry=${q.reload} />`;
  const sub = d ? html`Measured from the jars on disk, update checks and the job log · <span title=${absTime(d.generated_at)}>updated ${relTime(d.generated_at)}</span>` : "Measured from the jars on disk, update checks and the job log";
  const head = html`<${PageHead} title="Stats" sub=${sub}><${Btn} kind="ghost" icon="refresh-cw" cls="hide-sm" busy=${q.refreshing && !!d} onClick=${q.reload}>Refresh<//><//>`;
  if (!d) return html`${head}<${Loading} />`;
  if (!d.totals?.installs) return html`${head}<div class="panel"><${Empty} icon="blocks" title="Nothing to measure yet">No plugin jars were found on any server. Stats appear once servers have plugins installed.<//></div>`;

  const hm = d.heatmap && heatStats(d.heatmap.days, d.heatmap.start);
  const w = d.weeks, t = d.totals, fr = d.freshness;
  // Last row: whatever small charts exist share 12 columns (Update sources joins them when there's no weekly chart).
  const small = [!w && "src", d.lag && "lag", d.spread && "spread", d.spread?.top?.length > 0 && "top"].filter(Boolean);
  const span = small.length ? Math.max(3, 12 / small.length) : 4;
  const cards = {
    src: (sp, delay) => html`<${Card} id="st-src" span=${sp} delay=${delay} title="Update sources" sub=${`Where new versions come from · ${t.plugins} plugins (${n0(t.installs)} installs)`}
      legend=${html`<span class="tip kpi-q" tabindex="0" data-tip=${IDENTIFIED} aria-label=${IDENTIFIED}><${Icon} n="info" cls="i-xs" /></span>`}
      table=${html`<${DataTable} caption="Update sources" cols=${["Source", "Plugins", "Share"]} rows=${SOURCES.map(([k, l]) => [l, d.sources[k] || 0, `${Math.round(100 * (d.sources[k] || 0) / Math.max(1, t.plugins))}%`])} />`}>
      <${Sources} s=${d.sources} /><//>`,
    lag: (sp, delay) => html`<${Card} id="st-lag" span=${sp} delay=${delay} title="Update lag" sub=${`Days from release to install for ${plural(d.lag.n, "applied update")}. 3 in 4 landed within ${dd(d.lag.p75_days)} days.`}
      table=${html`<${DataTable} caption="Update lag" cols=${["Lag", "Updates"]} rows=${d.lag.bins.map(b => [binLong(b), b.count])} />`}>
      <${Histogram} h=${d.lag} color="var(--cat-1)" unit="updates" what="Days from release to install" /><//>`,
    spread: (sp, delay) => html`<${Card} id="st-spread" span=${sp} delay=${delay} title="Plugin spread" sub=${heroShowsShared(d) ? "Distinct plugins by how many servers run them" : `Distinct plugins by how many servers run them · ${sum(d.spread.buckets.filter(b => b.servers === 1).map(b => b.plugins))} run on just one`}
      table=${html`<${DataTable} caption="Plugin spread" cols=${["Servers", "Plugins"]} rows=${d.spread.buckets.map(b => [b.servers, b.plugins])} />`}>
      <${Spread} sp=${d.spread} servers=${t.servers} /><//>`,
    top: (sp, delay) => html`<${Card} id="st-top" span=${sp} delay=${delay} title="Most shared plugins" sub="Servers running each plugin, out of the servers on its platform"
      table=${html`<${DataTable} caption="Most shared plugins" cols=${["Plugin", "Servers", "Of"]} rows=${d.spread.top.map(x => [x.name, x.servers, d.family[x.key.split(":")[0]] || t.servers])} />`}>
      <${Shared} top=${d.spread.top} family=${d.family} servers=${t.servers} /><//>`,
  };
  // The trend line makes the headline band taller; row 2 gives that height back so the 1440x900 frame
  // still ends on a card edge.
  const trendShown = !!fr && d.trend.length >= 2;
  return html`<div class=${"stats" + (trendShown ? " has-trend" : "")}>
    ${head}
    ${d.indexing && html`<div class="st-note" role="status"><${Icon} n="loader-circle" cls="i-sm spin" /><span><b>Reading plugin jars after a restart</b> (${d.indexing.done}/${d.indexing.total}). Figures may change until this finishes.</span></div>`}
    <${Hero} d=${d} />
    <div class="st-grid">
      ${fr && html`<${Card} id="st-fbs" span=${d.behind ? 8 : 12} delay=${120} title="Freshness by server" sub="Share of each server's tracked installs that are current · worst first"
        legend=${html`<${Legend} items=${[["var(--ok)", "current"], ["color-mix(in srgb, var(--update) 55%, transparent)", "outdated"]]} />`}
        table=${html`<${DataTable} caption="Freshness by server" cols=${["Server", "Current", "Outdated", "Not tracked", "Current %"]}
          rows=${fr.servers.map(x => [x.id, x.current, x.outdated, x.untracked, x.current + x.outdated ? `${Math.round(100 * x.current / (x.current + x.outdated))}%` : "–"])} />`}>
        <${FreshBy} servers=${fr.servers} /><//>`}
      ${d.behind && html`<${Card} id="st-behind" span=${fr ? 4 : 6} delay=${200} cls="st-wide-mid" title="Waiting updates" sub=${`How long ${plural(d.behind.n, "outdated install")} ${d.behind.n === 1 ? "has" : "have"} had a newer version`}
        table=${html`<${DataTable} caption="Waiting updates" cols=${["Waiting", "Installs"]} rows=${d.behind.bins.map(b => [binLong(b), b.count])} />`}>
        <${Waiting} b=${d.behind} H=${trendShown ? 154 : 184} /><//>`}
      ${d.heatmap && html`<${Card} id="st-hm" span=${12} delay=${120} title="Daily activity" sub="Plugin jar changes per day · last 12 months"
        legend=${html`<dl class="hm-stats"><div><dt>Changes</dt><dd class="num">${n0(hm.total)}</dd></div><div><dt>Active days</dt><dd class="num">${hm.active}</dd></div><div><dt>Longest streak</dt><dd class="num">${plural(hm.streak, "day")}</dd></div><div><dt>Busiest day</dt><dd>${hm.busiest}</dd></div></dl>`}
        table=${html`<${DataTable} caption="Daily activity, days with changes" cols=${["Date", "Changes"]} rows=${d.heatmap.days.map((v, i) => [new Date(Date.parse(d.heatmap.start + "T00:00:00Z") + i * 864e5).toISOString().slice(0, 10), v]).filter(r => r[1]).reverse()} />`}>
        <${Heatmap} hm=${d.heatmap} /><//>`}
      ${w && html`<${Card} id="st-wk" span=${8} delay=${120} title="Changes per week" sub=${`Plugin jars added or replaced, split by who made the change · last ${w.length} weeks`}
        legend=${html`<${Legend} items=${[[C_JOBS, "PlugWarden jobs"], [C_OTHER, "Other changes"]]} />`}
        table=${html`<${DataTable} caption="Changes per week" cols=${["Week of", "Jars changed", "By PlugWarden jobs", "Other"]} rows=${w.map(x => [shortDate(x.start), x.count, x.jobs, x.count - x.jobs])} />`}>
        <${Weekly} weeks=${w} />
        ${d.history_note && html`<details class="st-about"><summary><${Icon} n="info" cls="i-xs" />How change dates are measured</summary><p>${d.history_note}</p></details>`}<//>`}
      ${w && cards.src(4, 200)}
      ${small.map((k, i) => cards[k](span, 120 + i * 70))}
      ${d.footprint && html`<${Card} id="st-fp" span=${12} delay=${120} title="Footprint" sub="Disk used by plugin jars and backups, and every job PlugWarden has run"
        table=${d.footprint.servers.length > 0 && html`<${DataTable} caption="Plugin jars by server" cols=${["Server", "Plugin jars"]} rows=${d.footprint.servers.map(x => [x.id, gb(x.bytes)])} />`}>
        <div class="fp-wrap"><${Footprint} fp=${d.footprint} t=${t} />
          ${d.footprint.servers.length > 0 && html`<div class="fp-disk"><span class="kpi-label">Plugin jars by server</span><${Disk} servers=${d.footprint.servers} /></div>`}</div><//>`}
    </div>
    ${hidden.length > 0 && html`<p class="st-hidden"><${Icon} n="info" cls="i-sm" /><span>Not shown yet: ${hidden.join("; ")}.</span></p>`}
  </div>`;
}
