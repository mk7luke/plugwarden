"""Auto-update scheduler: off / notify (check only) / apply, with interval, maintenance window, dry-run-first."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

from . import actions, audit, config, health, inventory, jobs, plans, settings, updates
from .storage import read_json, write_json

USER = "scheduler"
TICK_SECONDS = 60

_stop = threading.Event()
_thread: threading.Thread | None = None


def _state_file():
    return config.state("scheduler.json")


def load_state() -> dict:
    return read_json(_state_file(), {}) or {}


def in_window(dt: datetime, window: str | None) -> bool:
    """window is 'HH:MM-HH:MM' in server local time; may wrap past midnight."""
    if not window:
        return True
    start_s, end_s = window.split("-")
    t = dt.hour * 60 + dt.minute
    start = int(start_s[:2]) * 60 + int(start_s[3:])
    end = int(end_s[:2]) * 60 + int(end_s[3:])
    if start == end:
        return True
    if start < end:
        return start <= t < end
    return t >= start or t < end


def next_window_start(dt: datetime, window: str | None) -> datetime:
    if in_window(dt, window):
        return dt
    start_s = window.split("-")[0]
    cand = dt.replace(hour=int(start_s[:2]), minute=int(start_s[3:]), second=0, microsecond=0)
    if cand <= dt:
        cand += timedelta(days=1)
    return cand


def next_run(now: datetime | None = None) -> datetime | None:
    au = settings.load_raw()["auto_update"]
    if au["mode"] == "off":
        return None
    now = now or datetime.now()
    last = load_state().get("last_run")
    due = datetime.fromtimestamp(last) + timedelta(hours=float(au["interval_hours"])) if last else now
    return next_window_start(max(due, now), au["window"])


def status() -> dict:
    au = settings.load_raw()["auto_update"]
    st = load_state()
    nr = next_run()
    now = time.time()
    canary = []
    for k, v in (st.get("canary") or {}).items():
        key, _, version = k.partition("|")
        left = max(0.0, au["canary_soak_hours"] - (now - v["at"]) / 3600)
        canary.append({"key": key, "version": version, "server": v.get("server"),
                       "applied_at": _iso(v["at"]), "soak_hours_left": round(left, 1),
                       "canary_health": _public_health(v.get("health"))})
    held = [{**h, "at": _iso(h["at"])} for h in (st.get("held") or {}).values()]
    return {"mode": au["mode"], "next_run": nr.astimezone().isoformat(timespec="seconds") if nr else None,
            "last_run": _iso(st["last_run"]) if st.get("last_run") else None,
            "last_result": st.get("last_result"), "interval_hours": au["interval_hours"],
            "window": au["window"], "dry_run_first": au["dry_run_first"],
            "policy": {k: au[k] for k in ("min_release_age_hours", "canary_server", "canary_soak_hours",
                                         "max_changes_per_run")},
            "effective_canary": _canary_id(au),
            "canary": sorted(canary, key=lambda c: c["applied_at"], reverse=True)[:50],
            "held": sorted(held, key=lambda h: h["at"], reverse=True),
            "last_selection": st.get("last_selection")}


def _public_health(h: dict | None) -> dict:
    if not h:
        return {"status": "pending", "reason": "not checked yet (waiting for the canary to restart)"}
    return {k: h.get(k) for k in ("status", "reason", "excerpt", "log")} | {
        "checked_at": _iso(h["checked_at"]) if h.get("checked_at") else None}


def canary_health_for(key: str, version: str) -> dict | None:
    c = (load_state().get("canary") or {}).get(f"{key}|{version}")
    return _public_health(c.get("health")) if c else None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _canary_id(au: dict) -> str | None:
    return au.get("canary_server") or settings.load_raw()["default_source"] or None


def select_auto_rows(rows: list[dict], au: dict, canary_state: dict, canary: str | None,
                     canary_restarted: bool, now: float, installed_on_canary: dict[str, tuple[str, str]],
                     held: set[str] = frozenset()) -> tuple[list[dict], list[dict]]:
    """Which plan rows the unattended run may apply, and why the others wait.

    Only verified release builds from a Modrinth hash match (or a manual source marked auto_apply),
    published at least min_release_age_hours ago. The canary server gets them first; other servers only
    after the canary has run that exact build for canary_soak_hours, restarted since, and its log shows
    the plugin enabled cleanly (canary health "healthy"). A version that failed the canary check is held
    and never auto-applied again. Plugins the canary doesn't have wait min_release_age_hours +
    canary_soak_hours instead. At most max_changes_per_run rows (None = no limit), canary rows first."""
    chosen, deferred = [], []
    min_age = au["min_release_age_hours"] * 3600
    soak = au["canary_soak_hours"] * 3600
    for r in rows:
        src = r.get("source") or {}
        why = None
        published = updates._parse_time(r.get("published"))
        age = now - published.timestamp() if published else None
        if f"{r['key']}|{r['to_version']}" in held:
            why = "held: this version failed the canary health check"
        elif not r.get("verified"):
            why = "no verified hash"
        elif r.get("type") != "release":
            why = "pre-release"
        elif src.get("manual") and not src.get("auto_apply"):
            why = "manual source (not enabled for auto-apply)"
        elif src.get("overrides_modrinth") and not src.get("auto_apply"):
            why = "manual source overrides Modrinth"
        elif age is None or age < min_age:
            why = f"released less than {au['min_release_age_hours']:g} h ago"
        elif r["server"] != canary:
            k = f"{r['key']}|{r['to_version']}"
            if canary and r["key"] in installed_on_canary:
                c = canary_state.get(k)
                want_sha1 = ((r.get("_latest") or {}).get("hashes") or {}).get("sha1")
                if c and want_sha1 and c.get("sha1") and c["sha1"] != want_sha1:
                    c = None  # the canary soaked a different build of this version
                if not c:
                    why = f"waiting for canary {canary}"
                elif now - c["at"] < soak:
                    why = f"canary soak ({au['canary_soak_hours']:g} h)"
                elif not canary_restarted:
                    why = f"waiting for {canary} to restart"
                elif (c.get("health") or {}).get("status") != "healthy":
                    h = c.get("health") or {}
                    why = (f"canary health check failed on {canary}" if h.get("status") == "failed"
                           else f"waiting for {canary}'s log to show the plugin enabled")
            elif age < min_age + soak:
                why = "no canary for this plugin: waiting release age + soak"
        (deferred if why else chosen).append({**r, "reason": why} if why else r)
    chosen.sort(key=lambda r: r["server"] != canary)
    cap = au.get("max_changes_per_run")  # None = no limit
    over = chosen[cap:] if cap else []
    chosen = chosen[:cap] if cap else chosen
    deferred += [{**r, "reason": "over max_changes_per_run"} for r in over]
    return chosen, deferred


def run_cycle() -> str:
    """One scheduled run. Blocks until its jobs finish."""
    au = settings.load_raw()["auto_update"]
    st = load_state()
    st["last_run"] = time.time()
    write_json(_state_file(), st)
    check = jobs.wait(actions.start_check(USER), timeout=1800)
    result = f"check: {check.status} ({check.summary})"
    if au["mode"] != "apply" or check.status != "done":
        return _record(result)
    if not in_window(datetime.now(), au["window"]):
        return _record(result + "; outside the maintenance window, nothing applied")
    plan = updates.create_plan("all", USER)
    plan["rows"] = plans.load(plan["plan_id"], "updates")["rows"]  # internal rows (with build hashes)
    canary = _canary_id(au)
    servers = inventory.servers_by_id()
    csrv = servers.get(canary) if canary else None
    installed = {p["key"]: (p["version"] or "", p["sha1"]) for p in inventory.list_plugins(csrv)} if csrv else {}
    _note_canary_versions(st, canary, installed, plan["rows"])
    if csrv:
        check_canary_health(st, csrv)
    chosen, deferred = select_auto_rows(plan["rows"], au, st.get("canary") or {}, canary,
                                        csrv is not None and not actions.pending_restart(csrv), time.time(),
                                        installed, set(st.get("held") or {}))
    _save_selection(chosen, deferred)
    if not chosen:
        return _record(result + f"; nothing eligible ({len(deferred)} waiting)")
    keep = {(r["server"], r["key"]) for r in chosen}
    exclude = [[r["server"], r["key"]] for r in plan["rows"] if (r["server"], r["key"]) not in keep]
    if au["dry_run_first"]:
        dry = jobs.wait(actions.start_apply(USER, plan["plan_id"], exclude=exclude or None, dry_run=True, auto=True),
                        timeout=3600)
        if dry.status != "done":
            return _record(result + f"; dry run {dry.status}, not applying")
    real = jobs.wait(actions.start_apply(USER, plan["plan_id"], exclude=exclude or None, auto=True),
                     timeout=3 * 3600)
    changed = {(r["server"], r["item"]) for r in real.results if r["outcome"] == "changed"}
    st = load_state()
    cs = st.setdefault("canary", {})
    for r in chosen:
        label = f"{r['name']} {r['from_version']} → {r['to_version']}"
        if r["server"] == canary and (r["server"], label) in changed:
            cs[f"{r['key']}|{r['to_version']}"] = {
                "at": time.time(), "server": canary,
                "sha1": ((r.get("_latest") or {}).get("hashes") or {}).get("sha1")}
    write_json(_state_file(), st)
    return _record(result + f"; apply: {real.status} ({real.summary}); {len(deferred)} waiting")


def check_canary_health(st: dict, csrv: inventory.Server) -> None:
    """After the canary restarted with a new version, read its logs: healthy / failed / unknown.
    A failure holds that version for good (never auto-retried) and is written to the audit log."""
    plugins = {p["key"]: p for p in inventory.list_plugins(csrv)}
    last_start = actions._last_start(csrv)
    held = st.setdefault("held", {})
    for k, c in (st.get("canary") or {}).items():
        if c.get("server") != csrv.id or (c.get("health") or {}).get("status") in ("healthy", "failed"):
            continue
        if last_start < c["at"]:
            continue  # not restarted with it yet
        key, _, version = k.partition("|")
        p = plugins.get(key)
        if not p:
            c["health"] = {"status": "unknown", "reason": "plugin no longer installed on the canary",
                           "excerpt": [], "checked_at": time.time()}
            continue
        desc = inventory.jar_meta(csrv.plugins_dir / p["jar"])["descriptors"].get(csrv.family)
        res = health.plugin_health(csrv, health.read_runs(csrv, c["at"]), {**p, "version": version}, desc)
        c["health"] = {**res, "checked_at": time.time()}
        if res["status"] == "failed" and k not in held:
            held[k] = {"key": key, "name": p["name"], "version": version, "server": csrv.id,
                       "at": time.time(), "reason": res["reason"], "excerpt": res["excerpt"]}
            audit.record(USER, "canary-failed", servers=[csrv.id], path=key,
                         detail=f"{p['name']} {version} failed the canary health check: {res['reason']}; "
                                "rollout held", after={"excerpt": res["excerpt"], "log": res.get("log")})
    write_json(_state_file(), st)


def _note_canary_versions(st: dict, canary: str | None, installed: dict[str, tuple[str, str]],
                          rows: list[dict]) -> None:
    """If the canary already runs a version other servers are about to get (e.g. updated by hand), start
    its soak clock now — first seen by the scheduler, never the jar's (copyable) mtime."""
    if not canary:
        return
    cs = st.setdefault("canary", {})
    for r in rows:
        if r["server"] == canary or r["key"] not in installed:
            continue
        ver, sha1 = installed[r["key"]]
        k = f"{r['key']}|{r['to_version']}"
        if k not in cs and updates.display_version(ver) == r["to_version"]:
            cs[k] = {"at": time.time(), "server": canary, "sha1": sha1, "seen": "installed"}
    write_json(_state_file(), st)


def _save_selection(chosen: list[dict], deferred: list[dict]) -> None:
    st = load_state()
    st["last_selection"] = {
        "at": _iso(time.time()),
        "applied": [{"server": r["server"], "key": r["key"], "to_version": r["to_version"],
                     "canary_health": canary_health_for(r["key"], r["to_version"])} for r in chosen],
        "waiting": [{"server": r["server"], "key": r["key"], "to_version": r["to_version"], "reason": r["reason"],
                     "canary_health": canary_health_for(r["key"], r["to_version"])} for r in deferred][:200]}
    write_json(_state_file(), st)


def _record(result: str) -> str:
    st = load_state()
    st["last_result"] = result
    write_json(_state_file(), st)
    return result


HOUSEKEEPING_EVERY = 3600
_last_housekeeping = 0.0


def _loop() -> None:
    global _last_housekeeping
    while not _stop.wait(TICK_SECONDS):
        try:
            if time.time() - _last_housekeeping > HOUSEKEEPING_EVERY and not jobs.active_jobs():
                _last_housekeeping = time.time()
                jobs.run_exclusive(actions.housekeeping)
        except Exception:  # noqa: BLE001
            pass
        try:
            nr = next_run()
            if nr and datetime.now() >= nr and not jobs.active_jobs():
                run_cycle()
        except Exception as e:  # noqa: BLE001 - never let the scheduler thread die
            _record(f"scheduler error: {e}")


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="scheduler", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
