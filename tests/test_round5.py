"""Round 5: policy validation (422 per field, null = no limit), canary/log health checks, audit collapsing."""
import gzip
import os
import time

import pytest

from app import actions, audit, health, inventory, scheduler, settings
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


# ---------------------------------------------------------------- P1-2: log health

PAPER_OK = """[12:00:00 INFO]: Starting minecraft server version 1.21.6
[12:00:01 INFO]: [CoreProtect] Loading server plugin CoreProtect v24.1
[12:00:02 INFO]: [CoreProtect] Enabling CoreProtect v24.1
[12:00:02 INFO]: [CoreProtect] CoreProtect has been successfully enabled!
[12:00:03 ERROR]: [OtherPlugin] Something unrelated broke
java.lang.IllegalStateException: nope
\tat com.other.Thing.run(Thing.java:10)
[12:00:05 INFO]: Done (5.123s)! For help, type "help"
[13:00:00 INFO]: Stopping server
[13:00:00 INFO]: [CoreProtect] Disabling CoreProtect v24.1
"""

PAPER_ENABLE_FAIL = """[12:00:02 INFO]: [CoreProtect] Enabling CoreProtect v24.1
[12:00:02 ERROR]: Error occurred while enabling CoreProtect v24.1 (Is it up to date?)
java.lang.NoSuchMethodError: 'void org.bukkit.Foo.bar()'
\tat net.coreprotect.CoreProtect.onEnable(CoreProtect.java:88)
\tat org.bukkit.plugin.java.JavaPlugin.setEnabled(JavaPlugin.java:288)
[12:00:02 INFO]: [CoreProtect] Disabling CoreProtect v24.1
[12:00:05 INFO]: Done (5.1s)! For help, type "help"
"""

PAPER_LOAD_FAIL = """[12:00:01 ERROR]: Could not load 'plugins/CoreProtect-24.1.jar' in folder 'plugins'
org.bukkit.plugin.UnknownDependencyException: Unknown/missing dependency plugins: [ProtocolLib]. mysql://root:hunter2@db
\tat org.bukkit.plugin.SimplePluginManager.loadPlugins(SimplePluginManager.java:291)
[12:00:05 INFO]: Done (5.1s)! For help, type "help"
"""

PAPER_STACK = """[12:00:02 INFO]: [CoreProtect] Enabling CoreProtect v24.1
[12:00:05 INFO]: Done (5.1s)! For help, type "help"
[12:10:00 ERROR]: Could not pass event BlockBreakEvent to CoreProtect v24.1
java.lang.NullPointerException: null
\tat net.coreprotect.listener.BlockBreakListener.onBreak(BlockBreakListener.java:40)
"""

PLUGIN = {"key": "bukkit:coreprotect", "name": "CoreProtect", "version": "24.1", "jar": "CoreProtect-24.1.jar"}
DESC = {"main": "net.coreprotect.CoreProtect", "id": "CoreProtect"}


def _write_logs(env, server_key, latest, gz=None, gz_mtime=None):
    logs = env[server_key].parent / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "latest.log").write_text(latest)
    if gz is not None:
        p = logs / "2026-09-26-1.log.gz"
        with gzip.open(p, "wt") as f:
            f.write(gz)
        if gz_mtime:
            os.utime(p, (gz_mtime, gz_mtime))
    return logs


@pytest.mark.parametrize("log,status,reason", [
    (PAPER_OK, "healthy", "enabled"),
    (PAPER_ENABLE_FAIL, "failed", "failed to load or enable"),
    (PAPER_LOAD_FAIL, "failed", "failed to load or enable"),
    (PAPER_STACK, "failed", "error logged by the plugin"),
    ("[12:00:02 INFO]: [CoreProtect] Enabling CoreProtect v23.1\n", "unknown", "the server ran v23.1, not v24.1"),
    ("[12:00:05 INFO]: Done (5.1s)!\n", "unknown", "no log of this plugin since then"),
])
def test_plugin_health_from_paper_logs(env, log, status, reason):
    _write_logs(env, "src", log)
    srv = inventory.get_server("elChapo01")
    res = health.plugin_health(srv, health.read_runs(srv, None), PLUGIN, DESC)
    assert (res["status"], res["reason"]) == (status, reason)
    if status == "failed":
        assert res["excerpt"] and all("hunter2" not in ln for ln in res["excerpt"])


def test_health_uses_runs_since_and_is_bounded(env, monkeypatch):
    since = time.time()
    # older run (before the update) failed; the newest run is clean
    _write_logs(env, "src", PAPER_OK, gz=PAPER_ENABLE_FAIL, gz_mtime=since - 3600)
    srv = inventory.get_server("elChapo01")
    assert health.plugin_health(srv, health.read_runs(srv, since), PLUGIN, DESC)["status"] == "healthy"
    monkeypatch.setattr(health, "MAX_LOG_BYTES", 100)  # only the tail of latest.log is read
    runs = health.read_runs(srv, since)
    assert sum(len("\n".join(r["lines"])) for r in runs) <= 110


def test_health_refuses_logs_outside_server(env, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "latest.log").write_text(PAPER_ENABLE_FAIL)
    (env["src"].parent / "logs").symlink_to(outside)
    assert health.read_runs(inventory.get_server("elChapo01"), None) == []


def test_server_health_endpoint(env):
    (env["src"] / "CoreProtect-24.1.jar").unlink()
    make_jar(env["src"] / "CoreProtect-24.1.jar", "CoreProtect", "24.1")
    _write_logs(env, "src", PAPER_ENABLE_FAIL + "[12:00:02 INFO]: [Vault] Enabling Vault v1.7.3\n")
    with client_for(app) as c:
        r = c.get("/api/v2/servers/elChapo01/health").json()
        assert r["counts"] == {"healthy": 1, "failed": 1, "unknown": 0} and r["startup_complete"] is True
        assert r["plugins"][0]["name"] == "CoreProtect" and r["plugins"][0]["status"] == "failed"
        assert r["restarted"] is False and r["restarted_at"] is None  # no rotated log yet
        assert c.get("/api/v2/servers/elChapo01/health", params={"since": "yesterday"}).status_code == 400
        assert c.get("/api/v2/servers/elChapo01/health", params={"since": "2026-09-01T00:00:00Z"}).status_code == 200


def test_failed_canary_holds_rollout_and_is_audited(env):
    make_jar(env["src"] / "CoreProtect-24.1.jar", "CoreProtect", "24.1")
    applied = time.time() - 3600
    st = {"canary": {"bukkit:coreprotect|24.1": {"at": applied, "server": "elChapo01", "sha1": None}}}
    _write_logs(env, "src", PAPER_ENABLE_FAIL, gz="[old run]\n", gz_mtime=time.time())  # restarted after
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
    # not re-evaluated / not re-audited on the next cycle
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
