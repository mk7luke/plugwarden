"""Auto-update scheduler: off / notify (check only) / apply, with interval, maintenance window, dry-run-first."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

from . import actions, config, jobs, settings
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
    return {"mode": au["mode"], "next_run": nr.astimezone().isoformat(timespec="seconds") if nr else None,
            "last_run": datetime.fromtimestamp(st["last_run"]).astimezone().isoformat(timespec="seconds")
            if st.get("last_run") else None,
            "last_result": st.get("last_result"), "interval_hours": au["interval_hours"],
            "window": au["window"], "dry_run_first": au["dry_run_first"]}


def run_cycle() -> str:
    """One scheduled run. Blocks until its jobs finish."""
    au = settings.load_raw()["auto_update"]
    st = load_state()
    st["last_run"] = time.time()
    write_json(_state_file(), st)
    check = jobs.wait(actions.start_check(USER), timeout=1800)
    result = f"check: {check.status} ({check.summary})"
    if au["mode"] == "apply" and check.status == "done":
        if au["dry_run_first"]:
            dry = jobs.wait(actions.start_apply(USER, "all", dry_run=True, auto=True), timeout=3600)
            if dry.status != "done":
                result += f"; dry run {dry.status}, not applying"
                return _record(result)
        real = jobs.wait(actions.start_apply(USER, "all", dry_run=False, auto=True), timeout=3 * 3600)
        result += f"; apply: {real.status} ({real.summary})"
    return _record(result)


def _record(result: str) -> str:
    st = load_state()
    st["last_result"] = result
    write_json(_state_file(), st)
    return result


def _loop() -> None:
    while not _stop.wait(TICK_SECONDS):
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
