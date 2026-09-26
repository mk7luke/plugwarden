from datetime import datetime

from app import scheduler, settings


def test_in_window():
    assert scheduler.in_window(datetime(2026, 1, 1, 4, 0), "03:00-05:00")
    assert not scheduler.in_window(datetime(2026, 1, 1, 5, 0), "03:00-05:00")
    assert scheduler.in_window(datetime(2026, 1, 1, 23, 30), "23:00-02:00")
    assert scheduler.in_window(datetime(2026, 1, 1, 1, 0), "23:00-02:00")
    assert not scheduler.in_window(datetime(2026, 1, 1, 12, 0), "23:00-02:00")
    assert scheduler.in_window(datetime(2026, 1, 1, 12, 0), None)


def test_next_run(env):
    assert scheduler.next_run() is None  # mode off
    settings.update({"auto_update": {"mode": "notify", "window": "03:00-05:00"}})
    now = datetime(2026, 1, 1, 12, 0)
    assert scheduler.next_run(now) == datetime(2026, 1, 2, 3, 0)
    settings.update({"auto_update": {"window": None}})
    assert scheduler.next_run(now) == now


def test_settings_validation(env):
    import pytest
    for bad in ({"auto_update": {"mode": "yolo"}}, {"auto_update": {"window": "25:00-01:00"}},
                {"source_map": {"bukkit:x": {"kind": "github", "id": "../../x"}}},
                {"pins": {"not a key": "1"}}, {"default_source": "nope"}):
        with pytest.raises(settings.SettingsError):
            settings.update(bad)


def _row(server, key="bukkit:cp", to="24.1", age_h=100, **kw):
    import time as _t
    from datetime import datetime, timezone
    pub = datetime.fromtimestamp(_t.time() - age_h * 3600, timezone.utc).isoformat()
    return {"server": server, "key": key, "name": "CP", "from_version": "23.1", "to_version": to,
            "published": pub, "verified": True, "type": "release",
            "source": {"kind": "modrinth", "id": "x"}, **kw}


AU = {"min_release_age_hours": 48, "canary_soak_hours": 24, "max_changes_per_run": 20}


def test_auto_policy_age_prerelease_manual_and_canary():
    import time as _t
    now = _t.time()
    rows = [_row("elChapo01"), _row("M1-hub01"), _row("M3-hunger01", age_h=1),
            _row("M4-skyblock01", type="beta"), _row("M5-kitpvp01", verified=False),
            _row("M6-creative01", source={"kind": "github", "id": "a/b", "manual": True}),
            _row("M7-bending01", source={"kind": "github", "id": "a/b", "manual": True, "auto_apply": True,
                                        "overrides_modrinth": {"id": "p"}})]
    installed = {"bukkit:cp": ("23.1", "sha-old")}
    chosen, waiting = scheduler.select_auto_rows(rows, AU, {}, "elChapo01", True, now, installed)
    assert [r["server"] for r in chosen] == ["elChapo01"]
    why = {r["server"]: r["reason"] for r in waiting}
    assert why["M1-hub01"] == "waiting for canary elChapo01"
    assert why["M3-hunger01"].startswith("released less than 48")
    assert why["M4-skyblock01"] == "pre-release" and why["M5-kitpvp01"] == "no verified hash"
    assert why["M6-creative01"].startswith("manual source")
    assert why["M7-bending01"] == "waiting for canary elChapo01"  # opted in, still canaried
    # canary applied 25 h ago and restarted → rollout proceeds
    state = {"bukkit:cp|24.1": {"at": now - 25 * 3600}}
    chosen, waiting = scheduler.select_auto_rows(rows[1:2], AU, state, "elChapo01", True, now, installed)
    assert [r["server"] for r in chosen] == ["M1-hub01"]
    chosen, waiting = scheduler.select_auto_rows(rows[1:2], AU, state, "elChapo01", False, now, installed)
    assert waiting[0]["reason"] == "waiting for elChapo01 to restart"
    state = {"bukkit:cp|24.1": {"at": now - 2 * 3600}}
    _, waiting = scheduler.select_auto_rows(rows[1:2], AU, state, "elChapo01", True, now, installed)
    assert waiting[0]["reason"].startswith("canary soak")
    # a different build of that version soaked on the canary does not count
    r = {**rows[1], "_latest": {"hashes": {"sha1": "b" * 40}}}
    _, waiting = scheduler.select_auto_rows([r], AU, {"bukkit:cp|24.1": {"at": now - 25 * 3600, "sha1": "a" * 40}},
                                            "elChapo01", True, now, installed)
    assert waiting[0]["reason"] == "waiting for canary elChapo01"
    # missing type is not a release
    _, waiting = scheduler.select_auto_rows([{**rows[0], "type": None}], AU, {}, "elChapo01", True, now, installed)
    assert waiting[0]["reason"] == "pre-release"
    # plugin not on the canary: release age + soak
    chosen, waiting = scheduler.select_auto_rows([_row("M1-hub01", key="bukkit:x", age_h=60)], AU, {}, "elChapo01",
                                                 True, now, installed)
    assert waiting[0]["reason"].startswith("no canary")
    chosen, _ = scheduler.select_auto_rows([_row("M1-hub01", key="bukkit:x", age_h=80)], AU, {}, "elChapo01",
                                           True, now, installed)
    assert chosen


def test_auto_policy_cap_prefers_canary():
    import time as _t
    now = _t.time()
    rows = [_row(f"M{i}-x", key=f"bukkit:k{i}", age_h=500) for i in range(30)] + [_row("elChapo01", key="bukkit:k0")]
    chosen, waiting = scheduler.select_auto_rows(rows, {**AU, "max_changes_per_run": 5}, {}, "elChapo01", True, now, {})
    assert len(chosen) == 5 and chosen[0]["server"] == "elChapo01"
    assert sum(1 for r in waiting if r["reason"] == "over max_changes_per_run") == 26


def test_apply_mode_needs_confirmation(env):
    from app.main import app
    from conftest import client_for
    c = client_for(app)
    r = c.put("/api/v2/settings", json={"auto_update": {"mode": "apply"}})
    assert r.status_code == 400 and "confirm_apply" in r.json()["detail"]
    r = c.put("/api/v2/settings", json={"auto_update": {"mode": "apply", "canary_server": "elChapo01"},
                                       "confirm_apply": True})
    assert r.status_code == 200 and r.json()["auto_update"]["mode"] == "apply"
    ov = c.get("/api/v2/overview").json()["auto_update"]
    assert ov["policy"]["min_release_age_hours"] == 48 and ov["effective_canary"] == "elChapo01"
    log = c.get("/api/v2/access-log", params={"action": "settings"}).json()["entries"]
    assert log[0]["before"]["auto_update"]["mode"] == "off" and log[0]["after"]["auto_update"]["mode"] == "apply"


def test_hand_updated_canary_soaks_from_first_seen(env):
    import time as _t
    st = {}
    scheduler._note_canary_versions(st, "elChapo01", {"bukkit:cp": ("24.1", "s" * 40)}, [_row("M1-hub01")])
    c = st["canary"]["bukkit:cp|24.1"]
    assert abs(c["at"] - _t.time()) < 5 and c["seen"] == "installed"  # not the jar mtime
    _, waiting = scheduler.select_auto_rows([_row("M1-hub01")], AU, st["canary"], "elChapo01", True, _t.time(),
                                            {"bukkit:cp": ("24.1", "s" * 40)})
    assert waiting[0]["reason"].startswith("canary soak")
