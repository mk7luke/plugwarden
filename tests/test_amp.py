"""AMP integration against dev/mock_amp.py (never the real ADS)."""
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev"))
import mock_amp  # noqa: E402

from app import actions, amp, audit, config, inventory, jobs, scheduler, settings  # noqa: E402
from app.main import app  # noqa: E402
from conftest import client_for, make_jar  # noqa: E402


@pytest.fixture()
def ads(env, monkeypatch):
    mock = mock_amp.MockADS(log_root=str(env["base"]))
    monkeypatch.setattr(config, "AMP_URL", "http://ads.test:8080")
    monkeypatch.setattr(config, "AMP_USER", mock_amp.USER)
    monkeypatch.setattr(config, "AMP_PASSWORD", mock_amp.PASSWORD)
    monkeypatch.setattr(config, "AMP_READONLY", False)
    monkeypatch.setattr(amp, "TRANSPORT", mock_amp.mock_transport(mock))
    monkeypatch.setattr(amp, "SLEEP", lambda s: None)
    amp.reset()
    yield mock
    amp.reset()


def test_instances_map_only_minecraft_servers_we_know(ads):
    m = amp.instances()
    assert set(m) == {"elChapo01", "M1-hub01", "M3-hunger01", "M0-proxy01", "M9-homestead01"}
    assert m["elChapo01"]["friendly"] == "2-survival"


def test_status_parsing_and_players(ads):
    st = amp.fetch_status("elChapo01")
    assert st["state"] == "running" and st["players"] == ["PlayerOne"] and st["players_online"] == 1
    assert st["players_max"] == 50 and st["memory_mb"] == 3120 and st["uptime_seconds"] >= 3600
    assert amp.parse_status({"State": 0, "Uptime": "0.00:00:00", "Metrics": {}}, {})["state"] == "stopped"


def test_expired_session_relogs_in(ads):
    amp.fetch_status("M1-hub01")
    logins = sum(1 for c in ads.calls if c.endswith("Core/Login"))
    ads.sessions.clear()  # AMP restarted / sessions expired
    assert amp.fetch_status("M1-hub01")["state"] == "running"
    assert sum(1 for c in ads.calls if c.endswith("Core/Login")) > logins


def test_bad_credentials_never_leak(ads, monkeypatch):
    monkeypatch.setattr(config, "AMP_PASSWORD", "S3cret-hunter2")
    amp.reset()
    with pytest.raises(amp.AmpError) as e:
        amp.instances()
    assert "hunter2" not in str(e.value) and "login failed" in str(e.value)
    with client_for(app) as c:
        r = c.get("/api/v2/amp/status").json()
        assert "hunter2" not in str(r) and r["error"]


def test_overview_uses_cache_only(ads, monkeypatch):
    amp.refresh_all()
    def boom(*a, **k):
        raise AssertionError("network on overview")
    monkeypatch.setattr(amp, "TRANSPORT", httpx.MockTransport(boom))
    with amp._client_lock:
        amp._client = None  # a fresh client would use the failing transport
    with client_for(app) as c:
        ov = c.get("/api/v2/overview").json()
    assert ov["amp"]["configured"] and ov["amp"]["servers"]["elChapo01"]["players"] == ["PlayerOne"]


def test_unconfigured_hides_features(env, monkeypatch):
    monkeypatch.setattr(config, "AMP_URL", "")
    amp.reset()
    with client_for(app) as c:
        assert c.get("/api/v2/amp/status").json()["configured"] is False
        r = c.post("/api/v2/servers/M1-hub01/power", json={"action": "restart"})
        assert r.status_code == 503 and r.json()["code"] == "amp_unconfigured"


def test_power_restart_waits_for_done_and_clears_pending(ads):
    actions.mark_changed(type("J", (), {"changed_servers": ["M1-hub01"], "dry_run": False, "id": "x", "kind": "deploy",
                                        "user": "t", "results": []})())
    job = jobs.wait(actions.start_power("t", "M1-hub01", "restart"), 30)
    assert job.status == "done", job.results
    assert "restarted" in job.results[0]["detail"] and "plugins healthy" in job.results[0]["detail"]
    assert "M1-hub01:Core/Restart" in ads.calls
    assert not actions.pending_restart(inventory.get_server("M1-hub01"))
    assert job.to_dict()["restart_servers"] == []  # a restart itself doesn't need another restart
    assert (env_logs := (inventory.get_server("M1-hub01").root / "Minecraft/logs/latest.log")).exists()
    assert "Done (" in env_logs.read_text()


def test_power_stop_and_start(ads):
    assert jobs.wait(actions.start_power("t", "M3-hunger01", "stop"), 30).status == "done"
    assert ads.by_name("M3-hunger01").state == 0
    assert jobs.wait(actions.start_power("t", "M3-hunger01", "start"), 30).status == "done"
    assert ads.by_name("M3-hunger01").state == 20


def test_rolling_restart_warns_in_order_and_stops_on_failed_health(ads, env):
    make_jar(env["b"] / "CoreProtect-24.1.jar", "CoreProtect", "24.1")
    ads.by_name("M3-hunger01").fail_plugin = "CoreProtect"
    job = jobs.wait(actions.start_rolling("t", {"servers": ["M1-hub01", "M3-hunger01", "elChapo01"],
                                                "warn_seconds": [30, 10], "message": "Restart in {seconds}s!"}), 60)
    rows = {r["server"]: r for r in job.results}
    assert rows["M1-hub01"]["outcome"] == "changed"
    assert rows["M3-hunger01"]["outcome"] == "error" and "CoreProtect" in rows["M3-hunger01"]["detail"]
    assert rows["elChapo01"]["outcome"] == "skipped" and rows["elChapo01"]["reason_code"] == "stopped_after_failure"
    assert ads.by_name("M1-hub01").commands == ["say Restart in 30s!", "say Restart in 10s!"]
    assert "elChapo01:Core/Restart" not in ads.calls
    order = [c for c in ads.calls if c.endswith("Core/Restart")]
    assert order == ["M1-hub01:Core/Restart", "M3-hunger01:Core/Restart"]


def test_rolling_validation(ads):
    for bad in ({"servers": []}, {"servers": ["Enshrouded01"]}, {"servers": ["M1-hub01"], "warn_seconds": [0]},
                {"servers": ["M1-hub01"], "message": "a\nb"}, {"servers": ["M1-hub01"], "max_wait_min": 999}):
        with pytest.raises(Exception):
            actions.start_rolling("t", bad)


def test_console_stream_backlog_live_and_redacted(ads):
    inst = ads.by_name("elChapo01")
    inst.say("[10:00:00 INFO]: db url mysql://root:hunter2@db/x")
    q, backlog = amp.CONSOLE.subscribe("elChapo01")
    try:
        deadline = time.time() + 5
        got = []
        while time.time() < deadline and not any("later line" in g for g in got):
            if not got:
                inst.say("[10:00:01 INFO]: later line")
            try:
                got.append(q.get(timeout=0.2))
            except Exception:
                pass
        assert any("later line" in g for g in got)
        assert all("hunter2" not in g for g in got + backlog)
    finally:
        amp.CONSOLE.unsubscribe("elChapo01", q)


def test_console_commands_denylist_confirm_and_audit(ads):
    with client_for(app) as c:
        assert c.post("/api/v2/servers/M1-hub01/console", json={"command": ""}).status_code == 400
        assert c.post("/api/v2/servers/M1-hub01/console", json={"command": "x" * 300}).status_code == 400
        r = c.post("/api/v2/servers/M1-hub01/console", json={"command": "/op PlayerOne"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "confirm_required"
        assert r.json()["detail"]["matched"] == "op *"
        assert c.post("/api/v2/servers/M1-hub01/console", json={"command": "op PlayerOne", "confirm": True}).status_code == 200
        assert c.post("/api/v2/servers/M1-hub01/console", json={"command": "list"}).status_code == 200
        assert c.post("/api/v2/servers/Enshrouded01/console", json={"command": "list"}).status_code == 404
    assert ads.by_name("M1-hub01").commands == ["op PlayerOne", "list"]
    log = [e for e in audit.read(action="console-command")]
    assert [e["path"] for e in log] == ["list", "op PlayerOne"] and "confirmed" in log[1]["detail"]


def test_readonly_blocks_actions(ads, monkeypatch):
    monkeypatch.setattr(config, "AMP_READONLY", True)
    with client_for(app) as c:
        assert c.post("/api/v2/servers/M1-hub01/console", json={"command": "list"}).status_code == 403
        r = c.post("/api/v2/servers/M1-hub01/power", json={"action": "restart"})
        assert r.status_code == 403 and r.json()["code"] == "amp_readonly"
        assert c.get("/api/v2/amp/status").status_code == 200
    assert ads.by_name("M1-hub01").commands == [] and "M1-hub01:Core/Restart" not in ads.calls


def test_scheduler_auto_restart(ads):
    out = scheduler._auto_restart({"auto_restart_canary": True, "auto_restart_rest": True}, "elChapo01",
                                  {"elChapo01", "M1-hub01", "M3-hunger01"})
    assert "canary restart: done" in out and "rolling restart of 2: done" in out
    order = [c for c in ads.calls if c.endswith("Core/Restart")]
    assert order == ["elChapo01:Core/Restart", "M1-hub01:Core/Restart", "M3-hunger01:Core/Restart"]
    assert scheduler._auto_restart({"auto_restart_canary": False, "auto_restart_rest": False}, "elChapo01",
                                   {"M1-hub01"}) == ""


def test_rolling_restart_fails_when_a_plugin_disables_itself_after_start(ads, env):
    make_jar(env["a"] / "voicechat-bukkit-2.6.6.jar", "voicechat", "2.6.6")
    jobs.wait(actions.start_rolling("t", {"servers": ["M1-hub01"], "warn_seconds": []}), 30)  # clean baseline start
    ads.by_name("M1-hub01").disable_after_done = "voicechat"
    job = jobs.wait(actions.start_rolling("t", {"servers": ["M1-hub01", "M3-hunger01"], "warn_seconds": []}), 30)
    rows = {r["server"]: r for r in job.results}
    assert rows["M1-hub01"]["outcome"] == "error" and "disabled itself after startup" in rows["M1-hub01"]["detail"]
    assert rows["M3-hunger01"]["outcome"] == "skipped"


def test_canary_gate_fails_on_self_disable(ads, env):
    import time as _t
    make_jar(env["src"] / "voicechat-bukkit-2.6.7.jar", "voicechat", "2.6.7")
    ads.by_name("elChapo01").disable_after_done = "voicechat"
    st = {"canary": {"bukkit:voicechat|2.6.7": {"at": _t.time() - 5, "server": "elChapo01"}}}
    jobs.wait(actions.start_power("t", "elChapo01", "restart"), 30)
    scheduler.check_canary_health(st, inventory.get_server("elChapo01"))
    h = st["canary"]["bukkit:voicechat|2.6.7"]["health"]
    assert h["status"] == "failed" and h["reason"].startswith("disabled itself after startup")
    assert "bukkit:voicechat|2.6.7" in st["held"]
