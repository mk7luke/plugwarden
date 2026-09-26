"""Round 2: changesets, drift basis, scoped holds, counts, undone status, restart checklist, search, diff."""
import time

import pytest
from fastapi.testclient import TestClient

from app import actions, engine, inventory, jobs, plans, settings, updates
from app.main import app
from conftest import make_jar
from test_engine import _mock_modrinth, deploy, jars_of


@pytest.fixture()
def outdated(env):
    """CoreProtect 23.1 on M1 has a (mocked) Modrinth update to 24.1."""
    new_bytes = make_jar(env["tmp"] / "dl" / "x.jar", "CoreProtect", "24.1", extra=b"mr").read_bytes()
    _mock_modrinth(env, new_bytes)
    updates.check()
    return env


# ---------------------------------------------------------------- update changesets

def test_update_plan_applies_exactly_and_only_once(outdated):
    plan = updates.create_plan("all", "t")
    assert plan["summary"]["rows"] == 1 and plan["expires"]
    job = jobs.wait(actions.start_apply("t", plan["plan_id"]), 30)
    assert job.status == "done" and job.params["plan_id"] == plan["plan_id"]
    with pytest.raises(plans.PlanError) as e:
        actions.start_apply("t", plan["plan_id"])
    assert e.value.status == 409 and "already applied" in e.value.detail["message"]


def test_update_plan_conflicts_when_jar_changed(outdated):
    plan = updates.create_plan([{"key": "bukkit:coreprotect", "servers": ["M1-hub01", "M3-hunger01"]}], "t")
    assert plan["skipped"] == [{"server": "M3-hunger01", "key": "bukkit:coreprotect",
                                "reason": "no update available (current, pinned or ignored)"}]
    make_jar(outdated["a"] / "CoreProtect-23.1.jar", "CoreProtect", "23.1", extra=b"hand-edited")
    with pytest.raises(plans.PlanError) as e:
        actions.start_apply("t", plan["plan_id"])
    assert e.value.status == 409
    assert e.value.detail["conflicts"] == [{"server": "M1-hub01", "key": "bukkit:coreprotect",
                                            "from_jar": "CoreProtect-23.1.jar", "reason": "jar changed"}]


def test_update_plan_expiry_and_exclude(outdated):
    plan = updates.create_plan("all", "t")
    with pytest.raises(plans.PlanError) as e:
        actions.start_apply("t", plan["plan_id"], exclude=[["M1-hub01", "bukkit:coreprotect"]])
    assert e.value.status == 400  # everything excluded
    stored = plans.load(plan["plan_id"], "updates")
    stored["expires"] = time.time() - 1
    plans.save(stored)
    with pytest.raises(plans.PlanError) as e:
        actions.start_apply("t", plan["plan_id"])
    assert e.value.status == 409 and "expired" in e.value.detail["message"]


def test_api_requires_plan_and_maps_409(outdated):
    with TestClient(app) as c:
        assert c.post("/api/v2/updates/apply", json={"items": "all"}).status_code == 400
        pid = c.post("/api/v2/updates/plan", json={"items": "all"}).json()["plan_id"]
        make_jar(outdated["a"] / "CoreProtect-23.1.jar", "CoreProtect", "23.1", extra=b"changed")
        r = c.post("/api/v2/updates/apply", json={"plan_id": pid})
        assert r.status_code == 409 and r.json()["detail"]["conflicts"][0]["reason"] == "jar changed"


def test_deploy_plan_refuses_if_targets_changed(env):
    body = {"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync",
            "items": {"paths": ["Essentials/config.yml"]}}
    plan = engine.plan(body, "t")
    (env["a"] / "Essentials" / "config.yml").write_text("new-config\n")  # someone already synced it
    with pytest.raises(plans.PlanError) as e:
        actions.start_deploy("t", plan["plan_id"])
    assert e.value.status == 409
    assert e.value.detail["conflicts"][0]["planned"] == "changed"
    assert e.value.detail["conflicts"][0]["now"] == "unchanged"


# ---------------------------------------------------------------- drift

def _server(sid):
    from app import main
    snap = main.snapshot()
    return next(main.server_view(s, snap) for s in snap["servers"] if s.id == sid), snap


def test_drift_is_relative_to_majority(env):
    make_jar(env["b"] / "CoreProtect-23.1.jar", "CoreProtect", "23.1")  # M1 + M3 on 23.1, source on 24.1
    m1, snap = _server("M1-hub01")
    src, _ = _server("elChapo01")
    assert m1["drift_basis"] == "majority"
    assert not any(d["key"] == "bukkit:coreprotect" for d in m1["drift_plugins"])
    assert {"key": "bukkit:coreprotect", "name": "CoreProtect", "version": "24.1", "expected": "23.1"} \
        in src["drift_plugins"]


def test_drift_tie_prefers_source(env):
    m1, _ = _server("M1-hub01")  # CoreProtect: source 24.1 vs M1 23.1 → tie → source wins
    assert {"key": "bukkit:coreprotect", "name": "CoreProtect", "version": "23.1", "expected": "24.1"} \
        in m1["drift_plugins"]


# ---------------------------------------------------------------- scoped pins / ignores

def test_pin_scoped_to_one_server(outdated):
    make_jar(outdated["b"] / "CoreProtect-23.1.jar", "CoreProtect", "23.1")
    updates.check()
    assert updates.pending_updates()[0]["servers"] == ["M1-hub01", "M3-hunger01"]
    with TestClient(app) as c:
        r = c.post("/api/v2/plugins/bukkit:coreprotect/pin", json={"version": "23.1", "servers": ["M1-hub01"]})
        assert r.json()["pins"] == {"bukkit:coreprotect": {"version": "23.1", "servers": ["M1-hub01"]}}
        assert updates.pending_updates()[0]["servers"] == ["M3-hunger01"]
        m1 = c.get("/api/v2/servers/M1-hub01/plugins").json()
        cp = next(p for p in m1 if p["key"] == "bukkit:coreprotect")
        assert cp["status"] == "pinned" and cp["pinned_version"] == "23.1" and cp["pin_scope"] == ["M1-hub01"]
        m3 = c.get("/api/v2/servers/M3-hunger01/plugins").json()
        assert next(p for p in m3 if p["key"] == "bukkit:coreprotect")["pinned_version"] is None
        # ignore everywhere, then un-ignore just M3
        c.post("/api/v2/plugins/bukkit:coreprotect/ignore", json={"ignored": True, "servers": "*"})
        r = c.post("/api/v2/plugins/bukkit:coreprotect/ignore", json={"ignored": False, "servers": ["M3-hunger01"]})
        assert "M3-hunger01" not in r.json()["ignores"]["bukkit:coreprotect"]["servers"]
        assert "M1-hub01" in r.json()["ignores"]["bukkit:coreprotect"]["servers"]
        assert c.post("/api/v2/plugins/bukkit:coreprotect/pin", json={"version": "1", "servers": ["nope"]}).status_code == 400


def test_legacy_holds_migrate(env):
    from app.storage import write_json
    from app import config
    write_json(config.state("settings.json"), {"pins": {"bukkit:vault": "1.7"}, "ignores": ["bukkit:coreprotect"]})
    raw = settings.load_raw()
    assert raw["pins"] == {"bukkit:vault": {"version": "1.7", "servers": "*"}}
    assert raw["ignores"] == {"bukkit:coreprotect": {"servers": "*"}}


# ---------------------------------------------------------------- consistent counts

def test_counts_match_everywhere(outdated):
    make_jar(outdated["b"] / "CoreProtect-23.1.jar", "CoreProtect", "23.1")
    make_jar(outdated["a"] / "CoreProtect-22.0.jar", "CoreProtect", "22.0")  # duplicate: counts once
    updates.check()
    with TestClient(app) as c:
        ov = c.get("/api/v2/overview").json()
        up = c.get("/api/v2/updates").json()
        mx = c.get("/api/v2/matrix").json()
    assert ov["totals"]["updates"] == up["counts"] == mx["counts"]
    assert up["counts"]["installs"] == sum(s["updates"] for s in ov["servers"])
    assert up["counts"]["plugins"] == len(up["updates"])
    assert up["check_summary"].startswith("1 plugin outdated (")


# ---------------------------------------------------------------- job status after undo

def test_undone_job_status_and_summary(env):
    job = deploy({"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync", "items": {"jars": ["Vault.jar"]}})
    undo = jobs.wait(actions.start_undo("t", job.id), 30)
    d = jobs.get(job.id).to_dict()
    assert d["status"] == "undone" and d["run_status"] == "done"
    assert d["summary"].startswith(f"Undone by {undo.id}")
    listed = next(j for j in jobs.list_jobs() if j["id"] == job.id)
    assert listed["status"] == "undone"
    # raw status on disk is untouched (no double prefix after reload)
    jobs._live.pop(job.id, None)
    assert jobs.get(job.id).to_dict()["summary"].count("Undone by") == 1


# ---------------------------------------------------------------- pending restart

def _pending(c):
    ov = c.get("/api/v2/overview").json()
    return {s["id"] for s in ov["servers"] if s["pending_restart"]}, ov["restart_checklist"]


def test_pending_restart_recorded_for_every_real_change(outdated):
    with TestClient(app) as c:
        assert _pending(c)[0] == set()
        # update apply
        jobs.wait(actions.start_apply("t", updates.create_plan("all", "t")["plan_id"]), 30)
        ids, checklist = _pending(c)
        assert ids == {"M1-hub01"}
        assert checklist[0]["jobs"][0]["kind"] == "update-apply" and checklist[0]["jobs"][0]["items"]
        assert c.post("/api/v2/servers/M1-hub01/restarted").status_code == 200
        assert _pending(c)[0] == set()
        # deploy
        j = deploy({"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync",
                    "items": {"paths": ["Essentials/config.yml"]}})
        assert _pending(c)[0] == {"M1-hub01"}
        c.post("/api/v2/servers/M1-hub01/restarted")
        # undo
        jobs.wait(actions.start_undo("t", j.id), 30)
        assert _pending(c)[0] == {"M1-hub01"}
        c.post("/api/v2/servers/M1-hub01/restarted")
        # remove
        jobs.wait(actions.start_remove("t", "bukkit:vault", ["M1-hub01"], False, False), 30)
        assert _pending(c)[0] == {"M1-hub01"}
        # dry runs never mark
        c.post("/api/v2/servers/M1-hub01/restarted")
        jobs.wait(actions.start_remove("t", "bukkit:coreprotect", ["M1-hub01"], False, True), 30)
        assert _pending(c)[0] == set()


# ---------------------------------------------------------------- search / diff

def test_search_recursive_and_skips_userdata(env):
    with TestClient(app) as c:
        r = c.get("/api/v2/servers/M1-hub01/search", params={"q": "yml"}).json()
        paths = [x["path"] for x in r["results"]]
        assert "Essentials/config.yml" in paths and "Essentials/stale.yml" in paths
        assert not any(p.startswith("Essentials/userdata/") for p in paths)
        assert c.get("/api/v2/servers/M1-hub01/search", params={"q": "y"}).status_code == 422
        assert c.get("/api/v2/servers/nope/search", params={"q": "yml"}).status_code == 404


def test_diff(env):
    with TestClient(app) as c:
        r = c.get("/api/v2/diff", params={"source": "elChapo01", "target": "M1-hub01",
                                          "path": "Essentials/config.yml"}).json()
        assert not r["identical"] and "-old-config" in r["diff"] and "+new-config" in r["diff"]
        r = c.get("/api/v2/diff", params={"source": "elChapo01", "target": "M3-hunger01",
                                          "path": "Essentials/config.yml"}).json()
        assert r["target_exists"] is False and "+new-config" in r["diff"]
        r = c.get("/api/v2/diff", params={"source": "elChapo01", "target": "M1-hub01", "path": "Vault.jar"}).json()
        assert r["binary"] is True and r["diff"] == ""
        big = env["src"] / "big.txt"
        big.write_text("x" * (300 * 1024))
        r = c.get("/api/v2/diff", params={"source": "elChapo01", "target": "M1-hub01", "path": "big.txt"}).json()
        assert r["too_large"] is True
        assert c.get("/api/v2/diff", params={"source": "elChapo01", "target": "M1-hub01",
                                             "path": "../../etc/passwd"}).status_code == 400


# ---------------------------------------------------------------- regressions from the round-2 review

def test_deploy_plan_catches_same_size_source_edit(env):
    plan = engine.plan({"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync",
                        "items": {"paths": ["Essentials/config.yml"]}}, "t")
    (env["src"] / "Essentials" / "config.yml").write_text("EVI-config\n")  # same size, different content
    with pytest.raises(plans.PlanError) as e:
        actions.start_deploy("t", plan["plan_id"])
    assert e.value.detail["conflicts"][0]["reason"] == "source content changed"
    assert (env["a"] / "Essentials" / "config.yml").read_text() == "old-config\n"


def test_deploy_plan_catches_rebuilt_jar_same_name(env):
    plan = engine.plan({"source": "elChapo01", "targets": ["M1-hub01"], "action": "replace",
                        "items": {"jars": ["CoreProtect-24.1.jar"]}}, "t")
    make_jar(env["src"] / "CoreProtect-24.1.jar", "CoreProtect", "99.9")
    with pytest.raises(plans.PlanError):
        actions.start_deploy("t", plan["plan_id"])
    assert jars_of("M1-hub01", "bukkit:coreprotect") == ["CoreProtect-23.1.jar"]


def test_mirror_plan_tolerates_live_writes_but_not_new_deletions(env):
    body = {"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync", "items": {"folders": ["Essentials"]}}
    plan = engine.plan(body, "t")
    (env["a"] / "Essentials" / "config.yml").write_text("server rewrote its config\n")  # not a deletion
    jobs.wait(actions.start_deploy("t", plan["plan_id"]), 30)
    plan = engine.plan(body, "t")
    (env["a"] / "Essentials" / "extra.yml").write_text("x\n")  # the mirror would now delete this
    with pytest.raises(plans.PlanError):
        actions.start_deploy("t", plan["plan_id"])


def test_pin_does_not_narrow_or_silently_replace(env):
    with TestClient(app) as c:
        c.post("/api/v2/plugins/bukkit:vault/pin", json={"version": "1.7.0"})
        r = c.post("/api/v2/plugins/bukkit:vault/pin", json={"version": "1.7.0", "servers": ["M1-hub01"]})
        assert r.json()["pins"]["bukkit:vault"]["servers"] == "*"
        c.post("/api/v2/plugins/bukkit:vault/pin", json={"version": None})
        c.post("/api/v2/plugins/bukkit:vault/pin", json={"version": "1.7.0", "servers": ["M1-hub01"]})
        r = c.post("/api/v2/plugins/bukkit:vault/pin", json={"version": "1.7.3", "servers": ["elChapo01"]})
        assert r.status_code == 400 and "already pinned" in r.json()["detail"]
        c.post("/api/v2/plugins/bukkit:vault/ignore", json={"ignored": True})
        r = c.post("/api/v2/plugins/bukkit:vault/ignore", json={"ignored": True, "servers": ["M1-hub01"]})
        assert r.json()["ignores"]["bukkit:vault"]["servers"] == "*"
        bad = {"pins": {"bukkit:vault": {"version": "1", "servers": [1, "a"]}}}
        assert c.put("/api/v2/settings", json=bad).status_code == 400


def test_error_after_backup_still_marks_pending_restart(env, monkeypatch):
    real = engine.rsync

    def flaky(src, dest, *, dry_run, **kw):
        rc, lines, err = real(src, dest, dry_run=dry_run, **kw)
        return (rc, lines, err) if dry_run else (23, lines, "partial transfer")

    plan = engine.plan({"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync",
                        "items": {"paths": ["Essentials/config.yml"]}}, "t")
    monkeypatch.setattr(engine, "rsync", flaky)
    job = jobs.wait(actions.start_deploy("t", plan["plan_id"]), 30)
    assert job.status == "failed" and job.changed_servers == ["M1-hub01"]
    assert actions.pending_restart(inventory.get_server("M1-hub01"))


def test_checklist_resets_after_natural_restart(env):
    srv = inventory.get_server("M1-hub01")
    j1 = deploy({"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync", "items": {"jars": ["Vault.jar"]}})
    logs = srv.root / "Minecraft" / "logs"
    logs.mkdir()
    (logs / "2099-01-01-1.log.gz").write_text("")  # server restarted (rotated log is newer)
    import os
    os.utime(logs / "2099-01-01-1.log.gz", (time.time() + 5, time.time() + 5))
    assert not actions.pending_restart(srv)
    j2 = deploy({"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync",
                 "items": {"paths": ["Essentials/config.yml"]}})
    os.utime(logs / "2099-01-01-1.log.gz", (0, 0))
    checklist = actions.restart_checklist([srv])
    assert [j["job_id"] for j in checklist[0]["jobs"]] == [j2.id]
    assert j1.id


def test_update_plan_conflicts_when_other_version_added(outdated):
    plan = updates.create_plan("all", "t")
    assert plan["rows"][0]["also_removes"] == []
    make_jar(outdated["a"] / "CoreProtect-22.0.jar", "CoreProtect", "22.0")
    with pytest.raises(plans.PlanError) as e:
        actions.start_apply("t", plan["plan_id"])
    assert e.value.detail["conflicts"][0]["reason"] == "other versions changed"
    with TestClient(app) as c:
        pid = c.post("/api/v2/updates/plan", json={"items": "all"}).json()["plan_id"]
        r = c.post("/api/v2/updates/apply", json={"plan_id": pid, "exclude": [[["x"], "y"]]})
        assert r.status_code == 400


def test_search_and_diff_edge_cases(env):
    with TestClient(app) as c:
        assert c.get("/api/v2/servers/M1-hub01/search", params={"q": "   "}).status_code == 400
        r = c.get("/api/v2/diff", params={"source": "elChapo01", "target": "M1-hub01", "path": "nope.yml"})
        assert r.status_code == 404


def test_undoing_an_undo_clears_undone(env):
    j = deploy({"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync", "items": {"jars": ["Vault.jar"]}})
    u = jobs.wait(actions.start_undo("t", j.id), 30)
    assert jobs.get(j.id).to_dict()["status"] == "undone"
    jobs.wait(actions.start_undo("t", u.id), 30)
    assert jobs.get(j.id).to_dict()["status"] == "done"
    assert jobs.get(u.id).to_dict()["status"] == "undone"
