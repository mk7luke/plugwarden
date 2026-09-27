"""GET /api/v2/stats: inventory, freshness, sources, history (jar mtimes + jobs), lag, health, trend, footprint."""
import json
import os
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import config, health, inventory, main, settings, stats, updates
from app.main import app
from conftest import client_for, make_jar
from test_engine import _mock_modrinth

DAY = 86400


@pytest.fixture(autouse=True)
def _fresh_stats():
    stats.reset()
    yield
    stats.reset()


def _touch(path, ts):
    os.utime(path, (ts, ts))


def _compute(now=None):
    return stats.compute(main.snapshot(), now or time.time())


def _checked(env):
    """One update check: CoreProtect 23.1 on M1-hub01 is Modrinth-identified and outdated (24.1)."""
    new_bytes = make_jar(env["tmp"] / "dl" / "x.jar", "CoreProtect", "24.1", extra=b"modrinth").read_bytes()
    _mock_modrinth(env, new_bytes)
    updates.check()


def _write_job(jid, kind, finished, results, dry_run=False):
    d = {"id": jid, "kind": kind, "status": "done", "user": "t", "created": finished, "started": finished,
         "finished": finished, "summary": "", "dry_run": dry_run, "params": {}, "results": results,
         "changed_servers": sorted({r["server"] for r in results})}
    config.state("jobs", f"{jid}.json").write_text(json.dumps(d))


def test_inventory_counts_sharing_and_unique(env):
    make_jar(env["b"] / "Solo.jar", "Solo", "1.0")
    inv = _compute()["inventory"]
    by = {s["id"]: s for s in inv["servers"]}
    assert by["elChapo01"]["plugins"] == 2 and by["M1-hub01"]["jars"] == 2
    assert by["M3-hunger01"]["unique"] == 1 and by["M1-hub01"]["unique"] == 0
    assert by["elChapo01"]["bytes"] == sum(p.stat().st_size for p in env["src"].glob("*.jar"))
    assert by["M9-homestead01"]["plugins"] == 0
    assert inv["totals"]["plugins"] == 4 and inv["totals"]["jars"] == 6 and inv["totals"]["plugin_servers"] == 4
    assert inv["totals"]["bytes"] == sum(s["bytes"] for s in inv["servers"])
    assert inv["sharing"] == [{"servers": 1, "plugins": 2}, {"servers": 2, "plugins": 2}]
    assert {m["key"] for m in inv["most_shared"]} == {"bukkit:coreprotect", "bukkit:vault"}
    assert {(u["key"], u["server"]) for u in inv["unique"]} == {("bukkit:solo", "M3-hunger01"),
                                                                ("velocity:luckperms", "M0-proxy01")}


def test_freshness_score_and_sources(env):
    _checked(env)
    settings.update({"pins": {"bukkit:vault": {"version": "1.7.0", "servers": ["M1-hub01"]}}})
    s = _compute()
    f = s["freshness"]
    # CoreProtect: outdated on M1, 24.1 on elChapo unknown to the mock; Vault pinned on M1; rest unknown
    assert f["counts"] == {"current": 0, "outdated": 1, "unknown": 3, "pinned": 1, "ignored": 0, "total": 5}
    assert f["score"] == 0.0 and f["last_check"]
    hub = next(x for x in f["servers"] if x["id"] == "M1-hub01")
    assert hub["outdated"] == 1 and hub["pinned"] == 1 and hub["score"] == 0.0
    assert next(x for x in f["servers"] if x["id"] == "elChapo01")["score"] is None  # nothing tracked
    assert s["sources"] == {"modrinth": 1, "hangar": 0, "spiget": 0, "github": 0, "untracked": 2}


def test_freshness_score_counts_current(env):
    cache = updates.load_cache()
    for sid, d in (("elChapo01", env["src"]), ("M1-hub01", env["a"])):
        for p in inventory.list_plugins(inventory.get_server(sid)):
            cache["entries"][updates.cache_key(p["sha1"], "bukkit", "1.21.6")] = {
                "key": p["key"], "source": {"kind": "hangar", "id": "x"}, "current": {"version": p["version"]},
                "latest": {"version": "9"}, "outdated": p["key"] == "bukkit:vault" and sid == "M1-hub01",
                "error": None}
    updates.write_json(config.state("cache", "updates.json"), cache)
    s = _compute()
    assert s["freshness"]["counts"]["current"] == 3 and s["freshness"]["counts"]["outdated"] == 1
    assert s["freshness"]["score"] == 75.0
    assert s["sources"]["hangar"] == 2 and s["sources"]["untracked"] == 1


def test_history_from_mtimes_excludes_bogus_and_merges_jobs(env):
    now = time.time()
    _touch(env["src"] / "Vault.jar", now - 3 * DAY)
    _touch(env["a"] / "Vault.jar", now - 3 * DAY)
    _touch(env["src"] / "CoreProtect-24.1.jar", now - 40 * DAY)
    _touch(env["a"] / "CoreProtect-23.1.jar", now + 30 * DAY)      # future: bogus
    _touch(env["proxy"] / "LuckPerms-Velocity-5.5.jar", 1000)       # epoch: bogus
    job_at = datetime.fromtimestamp(now - 1 * DAY, timezone.utc).isoformat(timespec="seconds")
    # A job wrote Vault.jar on M1 (its time replaces that jar's mtime) and removed a jar elsewhere.
    _write_job("20260101-000000-000001", "update-apply", job_at, [
        {"server": "M1-hub01", "item": "Vault 1.6 → 1.7.0", "action": "update", "outcome": "changed",
         "new_jar": "Vault.jar", "old_jars": ["Vault-1.6.jar"]},
        {"server": "M1-hub01", "item": "Essentials", "action": "sync", "outcome": "changed"}])  # config: not a jar
    _write_job("20260101-000000-000002", "remove", job_at, [
        {"server": "M3-hunger01", "item": "Old.jar", "action": "delete", "outcome": "changed"}])
    _write_job("20260101-000000-000003", "update-apply", job_at, [
        {"server": "M3-hunger01", "item": "X.jar", "action": "update", "outcome": "changed"}], dry_run=True)
    _write_job("20260101-000000-000004", "update-check", job_at, [])
    h = _compute(now)["history"]
    assert h["excluded"] == 2
    assert len(h["daily"]) == 365 and len(h["weekly"]) == 26
    assert h["daily"][-1]["date"] == datetime.fromtimestamp(now).date().isoformat()
    days = {d["date"]: d["count"] for d in h["daily"]}
    day = lambda ago: datetime.fromtimestamp(now - ago * DAY).date().isoformat()  # noqa: E731
    assert days[day(3)] == 1   # elChapo Vault by mtime; M1's Vault counted at its job time instead
    assert days[day(1)] == 2   # the update + the removal (dry runs and config syncs ignored)
    assert days[day(40)] == 1
    assert h["total"] == 4 and h["max_daily"] == 2
    assert h["recent_30d"] == {"changes": 3, "from_jobs": 2}
    assert sum(w["count"] for w in h["weekly"]) == 4 and sum(w["from_jobs"] for w in h["weekly"]) == 2
    assert all(datetime.fromisoformat(w["week_start"]).weekday() == 0 for w in h["weekly"])
    assert "rsync -a" in h["note"]


def test_update_lag_and_behind(env):
    _checked(env)
    pub = datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp()   # the mock's installed 23.1 date
    _touch(env["a"] / "CoreProtect-23.1.jar", pub + 2 * DAY)
    now = datetime(2026, 1, 11, tzinfo=timezone.utc).timestamp()  # 24.1 was published 2026-01-01
    lag = _compute(now)["lag"]
    assert lag["samples"] == 1 and lag["median_days"] == 2.0 and lag["p75_days"] == 2.0
    assert [b["bucket"] for b in lag["histogram"]] == ["<1d", "1–3d", "3–7d", "1–2w", "2–4w", "1–3mo", ">3mo"]
    assert {b["bucket"]: b["count"] for b in lag["histogram"]}["1–3d"] == 1
    assert lag["histogram"][1] == {"bucket": "1–3d", "lo": 1, "hi": 3, "count": 1}
    assert lag["histogram"][-1]["hi"] is None
    assert lag["behind"]["installs"] == 1 and lag["behind"]["median_days"] == 10.0
    assert {b["bucket"]: b["count"] for b in lag["behind"]["histogram"]}["1–2w"] == 1
    assert lag["behind"]["plugins"] == [{"key": "bukkit:coreprotect", "name": "CoreProtect", "to_version": "24.1",
                                         "servers": 1, "days": 10.0}]
    # installed before it was published (clock skew) clamps to 0
    _touch(env["a"] / "CoreProtect-23.1.jar", pub - 5 * DAY)
    assert _compute(now)["lag"]["histogram"][0]["count"] == 1


def test_percentile():
    assert stats._percentile([], 0.5) is None
    assert stats._percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert stats._percentile([1.0, 2.0, 3.0, 4.0], 0.75) == 3.2


def test_health_from_cached_summaries(env, monkeypatch):
    summaries = {"elChapo01": {"failed_count": 0, "warning_count": 0},
                 "M1-hub01": {"failed_count": 1, "warning_count": 1},
                 "M3-hunger01": {"failed_count": 0, "warning_count": 2}}
    monkeypatch.setattr(health, "startup_summary", lambda srv: summaries.get(srv.id))
    h = _compute()["health"]
    assert h["servers"] == {"ok": 1, "warnings": 1, "not_running": 1, "unknown": 1}  # proxy has no report
    assert h["plugins"] == {"ok": 2, "warnings": 3, "not_running": 1}
    assert {p["id"]: p["status"] for p in h["per_server"]} == {
        "elChapo01": "ok", "M0-proxy01": "unknown", "M1-hub01": "not_running", "M3-hunger01": "warnings"}


def test_trend_snapshots_appended_by_checks_and_capped(env, monkeypatch):
    assert _compute()["trend"] == []
    _checked(env)
    _checked(env)
    trend = _compute()["trend"]
    assert len(trend) == 2
    assert {k: trend[-1][k] for k in ("outdated_plugins", "outdated_installs", "servers", "tracked", "total")} == \
        {"outdated_plugins": 1, "outdated_installs": 1, "servers": 1, "tracked": 1, "total": 5}
    monkeypatch.setattr(stats, "TREND_CAP", 3)
    for _ in range(3):
        stats.record_snapshot({"plugins": 0, "installs": 0, "servers": 0}, 0, 0)
    assert len(stats.load_trend()) == 3 and stats.load_trend()[-1]["outdated_plugins"] == 0


def test_footprint(env):
    b = config.state("backups", "20260101-000000-000001")
    (b / "M1-hub01").mkdir(parents=True)
    (b / "M1-hub01" / "Vault.jar").write_bytes(b"x" * 1000)
    (b / "manifest.json").write_text("{}")
    _write_job("20260101-000000-000001", "update-apply", "2026-01-01T00:00:00+00:00", [])
    _write_job("20260101-000000-000002", "undo", "2026-01-01T00:00:00+00:00", [])
    fp = _compute()["footprint"]
    assert fp["backups"] == 1 and fp["backups_bytes"] == 1002
    assert fp["jobs"] == 2 and fp["undos"] == 1 and fp["jobs_failed"] == 0
    assert fp["jobs_by_kind"] == {"undo": 1, "update-apply": 1}
    assert fp["jar_bytes"] == sum(p.stat().st_size for p in env["base"].rglob("plugins/*.jar"))
    assert {s["id"]: s["bytes"] for s in fp["servers"]}["M9-homestead01"] == 0


def test_endpoint_cached_no_network_and_invalidated(env, monkeypatch):
    _checked(env)

    def boom(req):
        raise AssertionError(f"network on a read path: {req.url}")
    updates.TRANSPORT = httpx.MockTransport(boom)
    calls = []
    real = main.snapshot
    monkeypatch.setattr(main, "snapshot", lambda: calls.append(1) or real())
    monkeypatch.setattr(health, "start_refresher", lambda stop: None)  # its first pass would invalidate
    with client_for(app) as c:
        r = c.get("/api/v2/stats")
        assert r.status_code == 200
        d = r.json()
        assert set(d) == {"generated_at", "indexing", "inventory", "freshness", "sources", "history", "lag",
                          "health", "trend", "footprint"}
        assert d["indexing"] is None and len(d["trend"]) == 1
        c.get("/api/v2/stats")
        assert len(calls) == 1  # warm: served from the cache
        time.sleep(0.01)
        make_jar(env["b"] / "New.jar", "New", "1.0")  # plugins dir changed -> recomputed
        assert c.get("/api/v2/stats").json()["inventory"]["totals"]["plugins"] == 4
        assert len(calls) == 2
