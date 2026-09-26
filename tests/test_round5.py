"""Round 5: policy validation (422 per field, null = no limit), canary/log health checks, audit collapsing."""
import json
import os
import time

import pytest

from app import actions, audit, config, health, inventory, scheduler, settings
from app.main import app
from conftest import client_for, make_jar

# ---------------------------------------------------------------- P1-1: policy validation

def test_policy_numbers_validated_per_field(env):
    c = client_for(app)
    r = c.put("/api/v2/settings", json={"auto_update": {"max_changes_per_run": None}})
    assert r.status_code == 200 and r.json()["auto_update"]["max_changes_per_run"] is None  # no limit
    r = c.put("/api/v2/settings", json={"auto_update": {"max_changes_per_run": -5, "canary_soak_hours": "x",
                                                        "interval_hours": 0, "window": "25:00-01:00"},
                                        "backup_keep_jobs": 2.5})
    assert r.status_code == 422
    f = r.json()["detail"]["fields"]
    assert set(f) >= {"auto_update.max_changes_per_run", "auto_update.canary_soak_hours",
                      "auto_update.interval_hours", "auto_update.window"}
    assert "1 and 500" in f["auto_update.max_changes_per_run"] and "no limit" in f["auto_update.max_changes_per_run"]
    r = c.put("/api/v2/settings", json={"backup_keep_jobs": 2.5})
    assert r.status_code == 422 and "backup_keep_jobs" in r.json()["detail"]["fields"]
    assert c.put("/api/v2/settings", json={"auto_update": {"max_changes_per_run": 1.5}}).status_code == 422
    assert c.put("/api/v2/settings", json={"auto_update": {"max_changes_per_run": True}}).status_code == 422
    assert settings.load_raw()["auto_update"]["max_changes_per_run"] is None  # nothing half-written


def test_no_limit_means_no_cap():
    now = time.time()
    from test_scheduler import _row, AU
    rows = [_row(f"M{i}-x", key=f"bukkit:k{i}", age_h=500) for i in range(40)]
    chosen, waiting = scheduler.select_auto_rows(rows, {**AU, "max_changes_per_run": None}, {}, "elChapo01", True,
                                                 now, {})
    assert len(chosen) == 40 and not waiting


# ---------------------------------------------------------------- P1-2: log health (differential)
# Fixtures follow the real Purpur/Velocity log shapes from the sandbox; player names and IPs are made up.

from datetime import datetime, timedelta
from conftest import write_log

T = "[{t}] [Server thread/INFO]: "
E = "[{t}] [Server thread/ERROR]: "


def L(prefix, msg, t="00:01:20"):
    return prefix.format(t=t) + msg


BASE_BODY = [
    L(T, "Starting minecraft server version 1.21.6", "00:00:28"),
    L(T, "[CoreProtect] Enabling CoreProtect v23.4"),
    L(T, "[Essentials] Enabling Essentials v2.22.0"),
    L(E, "[Essentials] You are running an unsupported server version!"),
    L(T, "[voicechat] Enabling voicechat v2.6.6"),
    L(T, "[Vivecraft-Spigot-Extension] Enabling Vivecraft-Spigot-Extension v1.3.15-1"),
    L(T, "[Voting] Enabling Voting v5.3.0"),
    L(T, "Done (90.041s)! For help, type \"help\"", "00:01:36"),
    "[00:01:36] [VoiceChatServerThread/ERROR]: [voicechat] Failed to bind to address '192.168.4.1', binding to wildcard IP instead",
    "[00:01:36] [VoiceChatServerThread/ERROR]: [voicechat] Failed to run voice chat at UDP port 24454, make sure no other application is running at that port",
    "[03:03:22] [User Authenticator #3/INFO]: UUID of player PlayerOne is 11111111-2222-3333-4444-555555555555",
    L(E, "Could not pass event PlayerJoinEvent to Vivecraft-Spigot-Extension v1.3.15-1", "21:22:12"),
    "org.bukkit.event.EventException: null",
    "\tat org.vivecraft.VSE.onJoin(VSE.java:12)",
    "[17:24:42] [Votifier epoll worker/ERROR]: [Votifier] Unable to process vote from /2.57.17.187:3118",
]


def new_run(body, when=None):
    when = when or datetime.now() - timedelta(hours=1)
    return when, [L(T, "Starting minecraft server version 1.21.6", when.strftime("%H:%M:%S"))] + body


def _setup(env, latest_body, with_baseline=True, when=None):
    logs = env["src"].parent / "logs"
    if with_baseline:
        day = datetime.now() - timedelta(days=1)
        write_log(logs, day.replace(hour=0, minute=0, second=10), BASE_BODY, name=day.strftime("%Y-%m-%d-2.log.gz"))
    when, body = new_run(latest_body, when)
    write_log(logs, when, body)
    return inventory.get_server("elChapo01"), when


def _check(srv, name, version, jar, main=None):
    runs = health.read_runs(srv)
    return health.plugin_health(srv, runs, {"name": name, "version": version, "jar": jar},
                                {"main": main, "id": name})


def _at(when, minutes):
    return (when + timedelta(minutes=minutes)).strftime("%H:%M:%S")


def test_known_issues_are_preexisting_not_failed(env):
    now = datetime.now() - timedelta(hours=1)
    body = [L(T, "[CoreProtect] Enabling CoreProtect v24.1", _at(now, 1)),
            L(E, "[Essentials] You are running an unsupported server version!", _at(now, 1)),
            L(T, "[voicechat] Enabling voicechat v2.6.6", _at(now, 1)),
            f"[{_at(now, 1)}] [VoiceChatServerThread/ERROR]: [voicechat] Failed to bind to address '10.0.0.9', binding to wildcard IP instead",
            L(T, "Done (60s)!", _at(now, 2))]
    srv, _ = _setup(env, body, when=now)
    assert _check(srv, "CoreProtect", "24.1", "CoreProtect-24.1.jar", "net.coreprotect.CoreProtect")["status"] == "healthy"
    ess = _check(srv, "Essentials", "2.22.0", "EssentialsX-2.22.0.jar")
    assert ess["status"] == "unknown"  # no enable line in this run, but the known error isn't a failure
    vc = _check(srv, "voicechat", "2.6.6", "voicechat-bukkit-2.6.6.jar")
    assert vc["status"] == "healthy" and vc["preexisting_errors"]  # same signature, different IP
    assert "<ip>" in vc["preexisting_errors"][0]["signature"]


def test_new_startup_error_after_update_fails(env):
    now = datetime.now() - timedelta(hours=1)
    body = [L(T, "[CoreProtect] Enabling CoreProtect v24.1", _at(now, 1)),
            L(E, "[CoreProtect] Database schema upgrade failed: table co_blocks missing", _at(now, 1)),
            "java.sql.SQLException: no such table", "\tat net.coreprotect.database.Database.init(Database.java:77)",
            L(T, "Done (60s)!", _at(now, 2))]
    srv, _ = _setup(env, body, when=now)
    res = _check(srv, "CoreProtect", "24.1", "CoreProtect-24.1.jar", "net.coreprotect.CoreProtect")
    assert (res["status"], res["reason"]) == ("failed", "new error after the update")
    assert res["new_errors"][0]["signature"].startswith("[CoreProtect] Database schema upgrade failed")


def test_hard_failures_always_fail(env):
    now = datetime.now() - timedelta(hours=1)
    body = [L(T, "[CoreProtect] Enabling CoreProtect v24.1", _at(now, 1)),
            L(E, "Error occurred while enabling CoreProtect v24.1 (Is it up to date?)", _at(now, 1)),
            "java.lang.NoSuchMethodError: 'void org.bukkit.Foo.bar()'",
            L(E, "[ModernPluginLoadingStrategy] Could not load 'plugins/.paper-remapped/Voting-5.3.0.jar' in "
                 "'plugins/.paper-remapped'", _at(now, 0)),
            "org.bukkit.plugin.UnknownDependencyException: mysql://root:hunter2@db",
            L(T, "Done (60s)!", _at(now, 2))]
    srv, _ = _setup(env, body, when=now)
    cp = _check(srv, "CoreProtect", "24.1", "CoreProtect-24.1.jar")
    assert (cp["status"], cp["reason"]) == ("failed", "failed to load or enable")
    vt = _check(srv, "Voting", "5.3.0", "Voting-5.3.0.jar")
    assert vt["status"] == "failed" and all("hunter2" not in ln for ln in vt["excerpt"])


def test_runtime_errors_after_grace_are_not_startup_failures(env):
    now = datetime.now() - timedelta(hours=3)
    body = [L(T, "[Vivecraft-Spigot-Extension] Enabling Vivecraft-Spigot-Extension v1.3.15-1", _at(now, 1)),
            L(T, "Done (60s)!", _at(now, 2)),
            L(E, "Could not pass event PlayerQuitEvent to Vivecraft-Spigot-Extension v1.3.15-1", _at(now, 90)),
            "java.lang.NullPointerException: null"]
    srv, _ = _setup(env, body, when=now)
    r = _check(srv, "Vivecraft-Spigot-Extension", "1.3.15-1", "Vivecraft-Spigot-Extension-1.3.15-1.jar")
    assert r["status"] == "healthy" and len(r["later_errors"]) == 1
    # the same new event error inside the grace window counts
    body[2] = L(E, "Could not pass event PlayerQuitEvent to Vivecraft-Spigot-Extension v1.3.15-1", _at(now, 5))
    srv, _ = _setup(env, body, when=now)
    assert _check(srv, "Vivecraft-Spigot-Extension", "1.3.15-1", "V.jar")["status"] == "failed"


def test_no_baseline_means_warning_not_failure(env):
    now = datetime.now() - timedelta(hours=1)
    body = [L(T, "[CoreProtect] Enabling CoreProtect v24.1", _at(now, 1)),
            L(E, "[CoreProtect] Something new", _at(now, 1)), L(T, "Done (60s)!", _at(now, 2))]
    srv, _ = _setup(env, body, with_baseline=False, when=now)
    r = _check(srv, "CoreProtect", "24.1", "CoreProtect-24.1.jar")
    assert r["status"] == "healthy" and r["warnings"] and r["baseline_runs"] == 0


def test_run_spans_daily_rollover_and_since_selects_run(env):
    logs = env["src"].parent / "logs"
    start = (datetime.now() - timedelta(days=1)).replace(hour=0, minute=0, second=10)
    write_log(logs, start, [L(T, "[CoreProtect] Enabling CoreProtect v24.1", "00:01:00"), L(T, "Done (60s)!", "00:01:10")],
              name=start.strftime("%Y-%m-%d-2.log.gz"))
    (logs / "latest.log").write_text("[00:00:01] [Server thread/INFO]: a new day\n")  # rollover, no restart
    srv = inventory.get_server("elChapo01")
    assert _check(srv, "CoreProtect", "24.1", "CoreProtect-24.1.jar")["status"] == "healthy"
    assert abs(health.last_startup(srv) - start.timestamp()) < 2
    # asking about a change after that start: no start since → unknown
    runs = health.read_runs(srv, time.time())
    r = health.plugin_health(srv, runs, {"name": "CoreProtect", "version": "24.1", "jar": "c.jar"}, None, since=time.time())
    assert r["status"] == "unknown" and "no server start" in r["reason"]


def test_start_older_than_logs_is_unknown(env):
    logs = env["src"].parent / "logs"
    logs.mkdir()
    (logs / "latest.log").write_text("[06:01:13] [Craft Scheduler Thread - 1 - Vault/INFO]: [Vault] Checking for Updates ...\n")
    r = _check(inventory.get_server("elChapo01"), "Vault", "1.7.3", "Vault.jar")
    assert r["status"] == "unknown" and "older than the available logs" in r["reason"]


def test_velocity_plugins(env):
    logs = env["proxy"].parent / "logs"
    now = datetime.now() - timedelta(hours=1)
    v = "[{t}] [main/INFO] [com.velocitypowered.proxy.plugin.VelocityPluginManager]: "
    body = [L(v, "Loaded plugin luckperms 5.5 by Luck", _at(now, 0)),
            L(v, "Loaded plugin plan 5.8 build 3638 by AuroraLS3", _at(now, 0)),
            "[" + _at(now, 0) + "] [main/ERROR] [com.velocitypowered.proxy.plugin.VelocityPluginManager]: Can't create plugin maintenance",
            "[" + _at(now, 30) + "] [Netty epoll Worker #4/ERROR] [com.velocitypowered.proxy.connection.MinecraftConnection]: [initial connection] /213.1.2.3:11493: read timed out",
            "[" + _at(now, 1) + "] [main/INFO] [com.velocitypowered.proxy.Velocity]: Done (4.62s)!"]
    when = now
    body = ["[" + when.strftime("%H:%M:%S") + "] [main/INFO] [com.velocitypowered.proxy.VelocityServer]: Booting up Velocity 3.4.0"] + body
    logs.mkdir()
    (logs / "latest.log").write_text("\n".join(body) + "\n")
    os.utime(logs / "latest.log", (when.timestamp(), when.timestamp()))
    srv = inventory.get_server("M0-proxy01")
    runs = health.read_runs(srv)
    ok = health.plugin_health(srv, runs, {"name": "LuckPerms", "version": "5.5", "jar": "LuckPerms-Velocity-5.5.jar"},
                              {"id": "luckperms"})
    assert ok["status"] == "healthy"
    plan = health.plugin_health(srv, runs, {"name": "Plan", "version": "5.8 build 3638", "jar": "Plan.jar"}, {"id": "plan"})
    assert plan["status"] == "healthy"
    bad = health.plugin_health(srv, runs, {"name": "Maintenance", "version": "4.3.0", "jar": "M.jar"}, {"id": "maintenance"})
    assert bad["status"] == "failed"


def test_health_is_bounded_and_confined(env, monkeypatch, tmp_path):
    now = datetime.now() - timedelta(hours=1)
    srv, _ = _setup(env, [L(T, "x" * 200, _at(now, 1))] * 50, when=now)
    monkeypatch.setattr(health, "MAX_LOG_BYTES", 1000)
    assert sum(len(ln) for r in health.read_runs(srv) for _t, ln in r["lines"]) <= 1100
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "latest.log").write_text("x\n")
    logs = env["a"].parent / "logs"
    logs.symlink_to(outside)
    assert health.read_runs(inventory.get_server("M1-hub01")) == []


def test_server_health_endpoint_known_issues(env):
    now = datetime.now() - timedelta(hours=1)
    make_jar(env["src"] / "voicechat-bukkit-2.6.6.jar", "voicechat", "2.6.6")
    body = [L(T, "[voicechat] Enabling voicechat v2.6.6", _at(now, 1)),
            f"[{_at(now, 1)}] [VoiceChatServerThread/ERROR]: [voicechat] Failed to run voice chat at UDP port 24454, make sure no other application is running at that port",
            L(T, "[Vault] Enabling Vault v1.7.3", _at(now, 1)), L(T, "Done (60s)!", _at(now, 2))]
    _setup(env, body, when=now)
    with client_for(app) as c:
        r = c.get("/api/v2/servers/elChapo01/health").json()
        assert r["startup_complete"] and r["baseline_runs"] == 1 and r["counts"]["failed"] == 0
        assert r["preexisting_errors"][0]["plugin"] == "voicechat"
        assert "UDP port #" in r["preexisting_errors"][0]["signature"]
        assert c.get("/api/v2/servers/elChapo01/health", params={"since": "yesterday"}).status_code == 400


def test_failed_canary_holds_rollout_and_is_audited(env):
    make_jar(env["src"] / "CoreProtect-24.1.jar", "CoreProtect", "24.1")
    applied = time.time() - 3600
    st = {"canary": {"bukkit:coreprotect|24.1": {"at": applied, "server": "elChapo01", "sha1": None}}}
    now = datetime.now() - timedelta(minutes=30)
    _setup(env, [L(T, "[CoreProtect] Enabling CoreProtect v24.1", _at(now, 1)),
                 L(E, "Error occurred while enabling CoreProtect v24.1 (Is it up to date?)", _at(now, 1)),
                 L(T, "Done (60s)!", _at(now, 2))], when=now)
    scheduler.check_canary_health(st, inventory.get_server("elChapo01"))
    c = st["canary"]["bukkit:coreprotect|24.1"]
    assert c["health"]["status"] == "failed"
    assert "bukkit:coreprotect|24.1" in st["held"]
    entry = audit.read(action="canary-failed")[0]
    assert entry["path"] == "bukkit:coreprotect" and "held" in entry["detail"] and entry["after"]["excerpt"]
    from test_scheduler import _row, AU
    _, waiting = scheduler.select_auto_rows([_row("M1-hub01", key="bukkit:coreprotect")], AU, st["canary"],
                                            "elChapo01", True, time.time(),
                                            {"bukkit:coreprotect": ("24.1", "x")}, set(st["held"]))
    assert waiting[0]["reason"].startswith("held")
    scheduler.check_canary_health(st, inventory.get_server("elChapo01"))
    assert len(audit.read(action="canary-failed")) == 1


def test_canary_not_checked_before_restart(env):
    st = {"canary": {"bukkit:coreprotect|24.1": {"at": time.time(), "server": "elChapo01"}}}
    scheduler.check_canary_health(st, inventory.get_server("elChapo01"))
    assert "health" not in st["canary"]["bukkit:coreprotect|24.1"]


# ---------------------------------------------------------------- P2: audit collapse

def test_audit_collapses_repeated_reads_but_not_changes(env):
    for _ in range(5):
        audit.record("a@x", "diff", ["s1", "s2"], "LP/config.yml", "1 value(s) redacted")
    audit.record("a@x", "diff", ["s1", "s3"], "LP/config.yml")
    audit.record("b@x", "diff", ["s1", "s2"], "LP/config.yml")
    for _ in range(2):
        audit.record("a@x", "pin", [], "bukkit:vault")
    rows = audit.read()
    diffs = [r for r in rows if r["action"] == "diff"]
    assert sorted(r["count"] for r in diffs) == [1, 1, 5]
    assert len([r for r in rows if r["action"] == "pin"]) == 2


def test_comment_prose_is_not_a_secret_label():
    from app.redact import redact_lines
    _, found = redact_lines(["# This can be changed to your own MaxMind URL if you have license https://x\n",
                             "# password: old\n"], "c.yml")
    assert list(found) == ["password"]


def test_failed_undo_marked_on_original(env, monkeypatch):
    from app import engine, jobs
    from test_security import _plan_deploy
    job = _plan_deploy({"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync", "items": {"jars": ["Vault.jar"]}})
    monkeypatch.setattr(engine, "run_undo", lambda ctx, jid: ctx.result("M1-hub01", "Vault.jar", "restore", "error", "boom"))
    undo = jobs.wait(actions.start_undo("t", job.id), 30)
    d = jobs.get(job.id).to_dict()
    assert undo.status == "failed" and d["undo_failed_by"] == undo.id and d["status"] == "done" and d["undoable"]


def test_excerpt_contains_match_with_context_and_file_line(env):
    now = datetime.now() - timedelta(hours=1)
    body = [L(T, "[CoreProtect] Enabling CoreProtect v24.1", _at(now, 1)),
            L(T, "[Vault] Enabling Vault v1.7.3", _at(now, 1)),
            L(E, "[CoreProtect] Something brand new", _at(now, 1)),
            "java.lang.IllegalStateException: boom", "\tat net.coreprotect.Foo.bar(Foo.java:1)",
            L(T, "Done (60s)!", _at(now, 2)), L(T, "after 1", _at(now, 2)), L(T, "after 2", _at(now, 2))]
    srv, _ = _setup(env, body, when=now)
    r = _check(srv, "CoreProtect", "24.1", "CoreProtect-24.1.jar", "net.coreprotect.CoreProtect")
    assert r["status"] == "failed"
    assert "[CoreProtect] Something brand new" in r["excerpt"][r["match_index"]]
    assert r["match_index"] == 2 and len(r["excerpt"]) >= r["match_index"] + 3  # context after the match
    # line numbers point into the actual log file (line 1 is the start marker written by write_log)
    lines = (env["src"].parent / "logs" / "latest.log").read_text().splitlines()
    assert r["log"] == "latest.log" and "Something brand new" in lines[r["line"] - 1]
    # preexisting entries too: baseline run's error, matched in a later run
    report = health.server_report(srv, None)
    for e in report["preexisting_errors"]:
        assert e["signature"].split("]")[0] in e["excerpt"][e["match_index"]]


# ---------------------------------------------------------------- cold start: background indexing

def test_cold_start_reads_do_not_hash_and_report_indexing(env, monkeypatch):
    from app import main as appmain
    inventory.reset_cache()
    calls = []
    real = inventory.file_hash
    monkeypatch.setattr(inventory, "file_hash", lambda p, algo="sha1": (calls.append(p), real(p, algo))[1])
    monkeypatch.setattr(inventory, "index_all", lambda: None)  # keep the app's own startup indexer out
    inventory.INDEX.update(running=True, done=0, total=5)  # as if the startup indexer were mid-way
    try:
        with client_for(app) as c:
            inventory.INDEX.update(running=True, done=0, total=5)
            t = time.time()
            ov = c.get("/api/v2/overview").json()
            assert time.time() - t < 2
            assert ov["indexing"] == {"done": 0, "total": 5}
            assert c.get("/api/v2/matrix").json()["indexing"] == {"done": 0, "total": 5}
            rows = c.get("/api/v2/servers/M1-hub01").json()["plugins"]
            assert rows and all(r["indexing"] and r["sha1"] is None for r in rows)
        assert calls == []  # nothing hashed on the read path
    finally:
        inventory.INDEX.update(running=False)
    monkeypatch.undo()
    inventory.index_all()
    assert inventory.indexing_state() is None  # the indexer did the hashing
    snap = appmain.snapshot()
    assert all(not r["indexing"] for rows in snap["plugins"].values() for r in rows)


def test_descriptor_version_bump_keeps_sha1(env, monkeypatch):
    jar = env["a"] / "Vault.jar"
    inventory.jar_meta(jar)
    monkeypatch.setattr(inventory, "DESC_VERSION", inventory.DESC_VERSION + 1)
    monkeypatch.setattr(inventory, "file_hash", lambda *a, **k: pytest.fail("rehashed after a descriptor bump"))
    assert inventory.jar_meta(jar)["dv"] == inventory.DESC_VERSION


def test_expected_version_with_provisional_rows():
    from app import main as appmain
    src = inventory.Server("elChapo01", None, None, "purpur", "bukkit", "1.21.6")
    assert appmain._expected_version({"M1": None, "M3": "1.0"}, src) in (None, "1.0")


def test_read_paths_make_no_network_calls(env, monkeypatch):
    import httpx
    from app import updates
    def boom(*a, **k):
        raise AssertionError("network call on a read path")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", boom)  # real network only (not TestClient)
    with client_for(app) as c:
        for path in ("/api/v2/overview", "/api/v2/servers", "/api/v2/matrix", "/api/v2/updates",
                     "/api/v2/servers/M1-hub01", "/api/v2/servers/M1-hub01/health", "/api/v2/settings",
                     "/api/v2/jobs", "/api/v2/access-log"):
            assert c.get(path).status_code == 200, path


# ---------------------------------------------------------------- round 6

VC_BODY = [
    L(T, "[voicechat] Enabling voicechat v2.6.6"),
    L(T, "Done (90.041s)! For help, type \"help\"", "00:01:36"),
    "[00:01:36] [Server thread/WARN]: [voicechat] Running in offline mode - Voice chat encryption is not secure!",
    "[00:01:36] [VoiceChatServerThread/ERROR]: [voicechat] Failed to bind to address '192.168.4.1', binding to wildcard IP instead",
    "[00:01:36] [VoiceChatServerThread/ERROR]: [voicechat] Failed to run voice chat at UDP port 24454, make sure no other application is running at that port",
    "[00:01:36] [VoiceChatServerThread/ERROR]: [voicechat] Voice chat server error",
    "[00:01:36] [Server thread/ERROR]: [voicechat] Disabling Simple Voice Chat",
    "[00:01:36] [Server thread/INFO]: [voicechat] Disabling voicechat v2.6.6",
]


def _vc_run(when):
    t = when.strftime("%H:%M:%S")
    return [ln.replace("00:01:36", t).replace("00:01:20", t) for ln in VC_BODY]


def test_plugin_disabling_itself_after_done_fails_and_is_not_running(env):
    logs = env["src"].parent / "logs"
    day = (datetime.now() - timedelta(days=1)).replace(hour=0, minute=0, second=8)
    write_log(logs, day, _vc_run(day), name=day.strftime("%Y-%m-%d-2.log.gz"))
    now = datetime.now() - timedelta(hours=1)
    write_log(logs, now, _vc_run(now))
    make_jar(env["src"] / "voicechat-bukkit-2.6.6.jar", "voicechat", "2.6.6")
    srv = inventory.get_server("elChapo01")
    r = _check(srv, "voicechat", "2.6.6", "voicechat-bukkit-2.6.6.jar")
    assert r["status"] == "failed" and r["running"] is False and r["preexisting"] is True
    assert r["reason"] == "disabled itself after startup (on every start)"
    assert "Disabling voicechat v2.6.6" in r["excerpt"][r["match_index"]]
    levels = sorted({(k["level"], k["signature"][:30]) for k in r["preexisting_errors"]})
    assert ("warning", "[voicechat] Running in offline") in levels
    errs = [k for k in r["preexisting_errors"] if k["level"] == "error"]
    # the three VoiceChatServerThread lines of the same second are one issue; the Server-thread line is another
    assert len(errs) == 2 and sorted(k["group_size"] for k in errs) == [1, 3]
    # first time (no baseline): still failed, but not "on every start"
    for f in logs.glob("*.gz"):
        f.unlink()
    r = _check(srv, "voicechat", "2.6.6", "voicechat-bukkit-2.6.6.jar")
    assert r["reason"] == "disabled itself after startup" and r["preexisting"] is False


def test_normal_shutdown_disable_is_not_a_failure(env):
    now = datetime.now() - timedelta(hours=2)
    body = [L(T, "[voicechat] Enabling voicechat v2.6.6", _at(now, 1)), L(T, "Done (60s)!", _at(now, 1)),
            L(T, "Stopping server", _at(now, 50)), L(T, "[voicechat] Disabling voicechat v2.6.6", _at(now, 50))]
    srv, _ = _setup(env, body, when=now)
    r = _check(srv, "voicechat", "2.6.6", "v.jar")
    assert r["status"] == "healthy" and r["running"] is True


def test_overview_carries_cached_startup_summary(env):
    now = datetime.now() - timedelta(hours=1)
    make_jar(env["src"] / "voicechat-bukkit-2.6.6.jar", "voicechat", "2.6.6")
    write_log(env["src"].parent / "logs", now, _vc_run(now))
    srv = inventory.get_server("elChapo01")
    health.refresh_summaries()
    with client_for(app) as c:
        tile = next(s for s in c.get("/api/v2/overview").json()["servers"] if s["id"] == "elChapo01")
    st = tile["startup"]
    assert st["failed_count"] == 1 and st["failed"][0]["name"] == "voicechat" and st["failed"][0]["running"] is False
    m1 = next(s for s in c.get("/api/v2/overview").json()["servers"] if s["id"] == "M1-hub01")["startup"]
    assert m1["failed_count"] == 0 and m1["run_started"] is None  # no logs for M1 in this fixture


def test_failed_undo_marked_real_flow_and_backfilled(env):
    from app import actions, engine, jobs
    from test_security import _plan_deploy
    job = _plan_deploy({"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync", "items": {"jars": ["Vault.jar"]}})
    # make the real undo fail: its backup copy disappeared
    for e in engine.Backup(job.id).entries:
        if e.get("store"):
            engine._remove(engine.Backup(job.id).store_path(e))
    before = sorted(p.name for p in env["a"].iterdir())
    undo = jobs.wait(actions.start_undo("t", job.id), 30)
    assert undo.status == "failed" and all(r["reason_code"] == "backup_missing" for r in undo.results)
    assert sorted(p.name for p in env["a"].iterdir()) == before  # nothing half-undone
    d = jobs.get(job.id).to_dict()
    assert d["undo_failed_by"] == undo.id and d["status"] == "done"
    # a record from before the field existed gets backfilled at startup
    from app import config
    from app.storage import read_json, write_json
    f = config.state("jobs", f"{job.id}.json")
    rec = read_json(f)
    rec.pop("undo_failed_by", None)
    write_json(f, rec)
    jobs._live.pop(job.id, None)
    assert jobs.get(job.id).to_dict()["undo_failed_by"] is None
    assert jobs.backfill_undo_failures() == 1
    assert jobs.get(job.id).to_dict()["undo_failed_by"] == undo.id
    assert jobs.backfill_undo_failures() == 0


# ---------------------------------------------------------------- round 7: known-issues noise

PLUGMAN_BANNER = [
    "[{t}] [Server thread/WARN]: [PlugManX] ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~",
    "[{t}] [Server thread/WARN]: [PlugManX] It seems like you're running on paper.",
    "[{t}] [Server thread/WARN]: [PlugManX] PlugManX cannot interact with paper-plugins, yet.",
    "[{t}] [Server thread/WARN]: [PlugManX] Also, if you encounter any issues, please join my discord: https://discord.gg/abc",
    "[{t}] [Server thread/WARN]: [PlugManX] Or create an issue on GitHub: https://github.com/x/PlugMan",
    "[{t}] [Server thread/WARN]: [PlugManX] ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~",
]
NAGS = [
    "[{t}] [Server thread/WARN]: [com.fastasyncworldedit.core.util.UpdateNotification] A new release for FastAsyncWorldEdit is available: 2.15.4",
    "[{t}] [Server thread/WARN]: [com.fastasyncworldedit.core.util.UpdateNotification] An update for FastAsyncWorldEdit is available. You are 12 builds out of date.",
]


def _noise_run(when):
    t = when.strftime("%H:%M:%S")
    return ([L(T, "[PlugManX] Enabling PlugManX v2.4.1", t), L(T, "[FastAsyncWorldEdit] Enabling FastAsyncWorldEdit v2.15.3", t)]
            + [ln.format(t=t) for ln in PLUGMAN_BANNER + NAGS]
            + ["[{t}] [Server thread/WARN]: [PlugManX] ==============".format(t=t),
               "[{t}] [Server thread/WARN]: [PlugManX] Support: https://discord.gg/abc".format(t=t),
               L(T, "Done (60s)!", t)])


def test_known_issues_group_banners_drop_noise_and_split_update_nags(env):
    logs = env["src"].parent / "logs"
    day = (datetime.now() - timedelta(days=1)).replace(hour=0, minute=0, second=8)
    write_log(logs, day, _noise_run(day), name=day.strftime("%Y-%m-%d-2.log.gz"))
    now = datetime.now() - timedelta(hours=1)
    write_log(logs, now, _noise_run(now))
    make_jar(env["src"] / "PlugManX-2.4.1.jar", "PlugManX", "2.4.1")
    make_jar(env["src"] / "FastAsyncWorldEdit-2.15.3.jar", "FastAsyncWorldEdit", "2.15.3")
    srv = inventory.get_server("elChapo01")
    pm = _check(srv, "PlugManX", "2.4.1", "PlugManX-2.4.1.jar")
    assert pm["status"] == "healthy"
    # six banner lines = one issue, titled by its first real line; the later decoration/link-only group is gone
    assert len(pm["preexisting_errors"]) == 1
    issue = pm["preexisting_errors"][0]
    assert issue["group_size"] == 6 and issue["title"] == "[PlugManX] It seems like you're running on paper."
    assert issue["excerpt"][issue["match_index"]].endswith("running on paper.")
    fawe = _check(srv, "FastAsyncWorldEdit", "2.15.3", "FastAsyncWorldEdit-2.15.3.jar")
    assert fawe["preexisting_errors"] == [] and fawe["warnings"] == []
    assert len(fawe["update_notices"]) == 1 and "new release" in fawe["update_notices"][0]["title"]
    with client_for(app) as c:
        r = c.get("/api/v2/servers/elChapo01/health").json()
    assert [n["plugin"] for n in r["update_notices"]] == ["FastAsyncWorldEdit"]
    assert not any("discord" in k["title"] or "~~~" in k["title"] for k in r["preexisting_errors"])


def test_update_nag_after_an_update_does_not_fail_the_plugin(env):
    now = datetime.now() - timedelta(hours=1)
    body = [L(T, "[CoreProtect] Enabling CoreProtect v24.1", _at(now, 1)),
            L(E, "[CoreProtect] A new version of CoreProtect is available: 24.2", _at(now, 1)),
            L(T, "Done (60s)!", _at(now, 2))]
    srv, _ = _setup(env, body, when=now)
    r = _check(srv, "CoreProtect", "24.1", "CoreProtect-24.1.jar")
    assert r["status"] == "healthy" and len(r["update_notices"]) == 1


def test_canary_gate_fails_on_self_disable(env):
    """No AMP needed: the canary check reads the logs after the owner restarts the server."""
    make_jar(env["src"] / "voicechat-bukkit-2.6.6.jar", "voicechat", "2.6.6")
    applied = time.time() - 3 * 3600
    st = {"canary": {"bukkit:voicechat|2.6.6": {"at": applied, "server": "elChapo01", "sha1": None}}}
    write_log(env["src"].parent / "logs", datetime.now() - timedelta(hours=1), _vc_run(datetime.now() - timedelta(hours=1)))
    scheduler.check_canary_health(st, inventory.get_server("elChapo01"))
    c = st["canary"]["bukkit:voicechat|2.6.6"]
    assert c["health"]["status"] == "failed" and c["health"]["reason"].startswith("disabled itself after startup")
    assert "bukkit:voicechat|2.6.6" in st["held"]


def test_legacy_job_kinds_still_render(env):
    """Records of removed features (e.g. rolling-restart, power) stay readable in Activity."""
    from app import jobs
    d = config.state("jobs")
    d.mkdir(parents=True, exist_ok=True)
    rec = {"id": "20260926-074609-0edad7", "kind": "rolling-restart", "status": "failed", "user": "a@b.c",
           "started": time.time() - 60, "finished": time.time(), "summary": "1 changed, 1 error, 1 skipped",
           "params": {"servers": ["M1-hub01"]}, "dry_run": False, "progress": {"done": 3, "total": 3, "current": None},
           "results": [{"server": "M1-hub01", "item": "restart", "action": "restart", "outcome": "changed",
                        "detail": "restarted in 12s"}], "log_lines": ["x"], "changed_servers": []}
    (d / f"{rec['id']}.json").write_text(json.dumps(rec))
    (d / "20260926-000000-aaaaaa.json").write_text(json.dumps({**rec, "id": "20260926-000000-aaaaaa", "kind": "power"}))
    with client_for(app) as c:
        lst = c.get("/api/v2/jobs").json()
        ids = {j["id"]: j for j in (lst["jobs"] if isinstance(lst, dict) else lst)}
        assert ids["20260926-074609-0edad7"]["kind"] == "rolling-restart"
        one = c.get("/api/v2/jobs/20260926-074609-0edad7")
        assert one.status_code == 200 and one.json()["results"][0]["outcome"] == "changed"
        assert c.get("/api/v2/overview").status_code == 200
        assert c.post("/api/v2/jobs/20260926-074609-0edad7/undo").status_code in (400, 409)
    assert jobs.get("20260926-000000-aaaaaa") is not None


def test_removed_settings_are_dropped(env):
    """auto_restart_* and amp_command_denylist came with the removed AMP integration."""
    config.state("settings.json").write_text(json.dumps({
        "auto_update": {"mode": "notify", "auto_restart_canary": True, "auto_restart_rest": True},
        "amp_command_denylist": ["stop"]}))
    raw = settings.load_raw()
    assert raw["auto_update"]["mode"] == "notify"
    assert "auto_restart_canary" not in raw["auto_update"] and "auto_restart_rest" not in raw["auto_update"]
    assert "amp_command_denylist" not in raw
    with client_for(app) as c:
        r = c.put("/api/v2/settings", json={"auto_update": {"interval_hours": 12}})
        assert r.status_code == 200 and "auto_restart_rest" not in r.json()["auto_update"]
    stored = json.loads(config.state("settings.json").read_text())
    assert "amp_command_denylist" not in stored and "auto_restart_canary" not in stored["auto_update"]


# ---------------------------------------------------------------- baseline = starts before the change only

def _cp_run(when, version, fail):
    t = when.strftime("%H:%M:%S")
    body = [L(T, "Starting minecraft server version 1.21.6", t), L(T, f"[CoreProtect] Enabling CoreProtect v{version}", t)]
    if fail:
        body.append(L(E, f"Error occurred while enabling CoreProtect v{version} (Is it up to date?)", t))
    return body + [L(T, "Done (60s)!", t)]


def _canary_scenario(env, old_fails):
    logs = env["src"].parent / "logs"
    make_jar(env["src"] / "CoreProtect-24.1.jar", "CoreProtect", "24.1")
    now = datetime.now()
    before = (now - timedelta(days=2)).replace(hour=0, minute=0, second=10)
    run1, run2 = now - timedelta(hours=2), now - timedelta(hours=1)
    write_log(logs, before, _cp_run(before, "23.1", old_fails), name=before.strftime("%Y-%m-%d-1.log.gz"))
    write_log(logs, run1, _cp_run(run1, "24.1", True), name=run1.strftime("%Y-%m-%d-2.log.gz"))
    write_log(logs, run2, _cp_run(run2, "24.1", True))  # restarted again after the update: fails again
    applied = (now - timedelta(hours=3)).timestamp()
    srv = inventory.get_server("elChapo01")
    res = health.plugin_health(srv, health.read_runs(srv, applied),
                               {"name": "CoreProtect", "version": "24.1", "jar": "CoreProtect-24.1.jar"},
                               {"main": None, "id": "CoreProtect"}, since=applied)
    st = {"canary": {"bukkit:coreprotect|24.1": {"at": applied, "server": "elChapo01", "sha1": None}}}
    scheduler.check_canary_health(st, srv)
    return res, st


def test_repeated_failure_after_update_is_not_preexisting(env):
    res, st = _canary_scenario(env, old_fails=False)
    assert res["status"] == "failed" and res["preexisting"] is False
    assert res["baseline_runs"] == 1  # only the start with the old version, not the first post-update start
    assert st["canary"]["bukkit:coreprotect|24.1"]["health"]["status"] == "failed"
    assert "bukkit:coreprotect|24.1" in st["held"]


def test_failure_also_with_old_version_is_preexisting_but_still_holds_the_canary(env):
    res, st = _canary_scenario(env, old_fails=True)
    assert res["status"] == "failed" and res["preexisting"] is True
    c = st["canary"]["bukkit:coreprotect|24.1"]
    assert c["health"]["status"] == "failed" and c["health"]["preexisting"] is True
    assert "bukkit:coreprotect|24.1" in st["held"]  # never "healthy": the updated plugin does not run
