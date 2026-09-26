import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import actions, engine, inventory, jobs, updates
from app.main import app
from conftest import make_jar, snapshot_tree


def deploy(body):
    plan = engine.plan(body, "tester")
    job = jobs.wait(actions.start_deploy("tester", plan["plan_id"]), timeout=30)
    assert job.status in ("done", "failed"), job.log
    return job


# ---------------------------------------------------------------- path safety

@pytest.mark.parametrize("bad", ["../x", "a/../../x", "/etc/passwd", "..", "a\\b", "~root", "x\x00y"])
def test_check_rel_rejects(env, bad):
    with pytest.raises(inventory.PathError):
        inventory.check_rel(bad)


def test_symlink_escape_rejected(env, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (env["a"] / "evil").symlink_to(outside)
    srv = inventory.get_server("M1-hub01")
    with pytest.raises(inventory.PathError):
        inventory.resolve_in(srv, "evil/file")
    with pytest.raises(inventory.PathError):
        inventory.resolve_in(srv, "evil")


def test_api_rejects_traversal_and_unknown_servers(env):
    with TestClient(app) as c:
        assert c.get("/api/v2/servers/M1-hub01/tree", params={"path": "../../"}).status_code == 400
        assert c.get("/api/v2/servers/M1-hub01/tree", params={"path": "/etc"}).status_code == 400
        assert c.get("/api/v2/servers/..%2F..%2Fetc/plugins").status_code == 404
        assert c.get("/api/v2/servers/nope/plugins").status_code == 404
        r = c.post("/api/v2/deploy/plan", json={"source": "elChapo01", "targets": ["M1-hub01"],
                                                 "action": "sync", "items": {"paths": ["../../etc/passwd"]}})
        assert r.status_code == 400
        r = c.post("/api/v2/deploy/plan", json={"source": "elChapo01", "targets": ["/tmp"],
                                                 "action": "sync", "items": {"jars": ["Vault.jar"]}})
        assert r.status_code == 400
        # Base can never be client supplied: unknown query params are ignored.
        r = c.get("/api/v2/servers", params={"base": "/"})
        assert {s["id"] for s in r.json()} == {"elChapo01", "M0-proxy01", "M1-hub01", "M3-hunger01", "M9-homestead01"}
        # cross-site writes refused
        r = c.post("/api/v2/updates/check", headers={"Origin": "https://evil.example"})
        assert r.status_code == 403


def test_proxy_and_source_never_targets(env):
    for targets in (["M0-proxy01"], ["elChapo01"], ["M9-homestead01"]):
        with pytest.raises(engine.DeployError):
            engine.validate_deploy({"source": "elChapo01", "targets": targets, "action": "sync",
                                    "items": {"jars": ["Vault.jar"]}})


# ---------------------------------------------------------------- plan / dry run

def test_plan_and_dry_run_make_no_changes(env):
    before = snapshot_tree(env["base"])
    body = {"source": "elChapo01", "targets": ["M1-hub01", "M3-hunger01"], "action": "install",
            "items": {"jars": ["CoreProtect-24.1.jar", "Vault.jar"], "folders": ["Essentials"],
                      "paths": ["Essentials/messages/en.yml"]}}
    plan = engine.plan(body)
    assert plan["summary"]["changed"] > 0
    body["action"] = "delete"
    engine.plan(body)
    body["action"] = "replace"
    engine.plan(body)
    assert engine.dry_run(body)["summary"]["changed"] > 0
    assert snapshot_tree(env["base"]) == before


# ---------------------------------------------------------------- sync semantics

def test_sync_updates_existing_only(env):
    job = deploy({"source": "elChapo01", "targets": ["M1-hub01", "M3-hunger01"], "action": "sync",
                  "items": {"jars": ["Vault.jar"], "paths": ["Essentials/config.yml"]}})
    by = {(r["server"], r["item"]): r["outcome"] for r in job.results}
    assert by[("M1-hub01", "Vault.jar")] == "changed"
    assert by[("M3-hunger01", "Vault.jar")] == "skipped"
    assert by[("M3-hunger01", "Essentials/config.yml")] == "skipped"
    assert (env["a"] / "Vault.jar").read_bytes() == (env["src"] / "Vault.jar").read_bytes()
    assert (env["a"] / "Essentials" / "config.yml").read_text() == "new-config\n"
    assert not (env["b"] / "Vault.jar").exists()
    # path sync does not delete extra files
    assert (env["a"] / "Essentials" / "stale.yml").exists()


def test_install_copies_when_missing(env):
    deploy({"source": "elChapo01", "targets": ["M3-hunger01"], "action": "sync",
            "items": {"jars": ["Vault.jar"], "paths": ["Essentials/messages/en.yml"]}, "options": {"install": True}})
    assert (env["b"] / "Vault.jar").exists()
    assert (env["b"] / "Essentials" / "messages" / "en.yml").read_text() == "hello\n"


def test_folder_is_mirrored_with_delete(env):
    deploy({"source": "elChapo01", "targets": ["M1-hub01", "M3-hunger01"], "action": "sync",
            "items": {"folders": ["Essentials"]}})
    ess = env["a"] / "Essentials"
    assert snapshot_tree(ess) == snapshot_tree(env["src"] / "Essentials")
    assert not (ess / "stale.yml").exists()
    assert not (env["b"] / "Essentials").exists()  # not installed there, install off


def test_delete(env):
    job = deploy({"targets": ["M1-hub01"], "action": "delete", "items": {"folders": ["Essentials"]}})
    assert not (env["a"] / "Essentials").exists()
    assert job.undoable


# ---------------------------------------------------------------- jar replace + undo

def jars_of(d, key):
    srv = inventory.get_server(d)
    return [p["jar"] for p in inventory.list_plugins(srv) if p["key"] == key]


def test_replace_leaves_exactly_one_version_and_undo_restores(env):
    before = snapshot_tree(env["a"])
    # a duplicate old version too: replace must clean up both
    make_jar(env["a"] / "CoreProtect-22.0.jar", "CoreProtect", "22.0")
    before = snapshot_tree(env["a"])
    job = deploy({"source": "elChapo01", "targets": ["M1-hub01", "M3-hunger01"], "action": "replace",
                  "items": {"jars": ["CoreProtect-24.1.jar"]}})
    assert jars_of("M1-hub01", "bukkit:coreprotect") == ["CoreProtect-24.1.jar"]
    assert jars_of("M3-hunger01", "bukkit:coreprotect") == []  # not installed, install off
    assert job.undoable and "M1-hub01" in job.changed_servers

    undo = jobs.wait(actions.start_undo("tester", job.id), timeout=30)
    assert undo.status == "done", undo.log
    assert snapshot_tree(env["a"]) == before
    assert jobs.get(job.id).undone_by == undo.id
    with pytest.raises(engine.DeployError):
        actions.start_undo("tester", job.id)


def test_undo_restores_sync_folder_and_delete(env):
    before = snapshot_tree(env["base"])
    j1 = deploy({"source": "elChapo01", "targets": ["M1-hub01", "M3-hunger01"], "action": "install",
                 "items": {"folders": ["Essentials"], "jars": ["Vault.jar"], "paths": ["Essentials/messages/en.yml"]}})
    assert (env["b"] / "Essentials").is_dir()
    jobs.wait(actions.start_undo("tester", j1.id), timeout=30)
    assert snapshot_tree(env["base"]) == before
    j2 = deploy({"targets": ["M1-hub01"], "action": "delete", "items": {"jars": ["Vault.jar"], "folders": ["Essentials"]}})
    assert not (env["a"] / "Vault.jar").exists()
    jobs.wait(actions.start_undo("tester", j2.id), timeout=30)
    assert snapshot_tree(env["base"]) == before


def test_upload_rejects_non_jars(env):
    with TestClient(app) as c:
        r = c.post("/api/v2/upload", files={"file": ("x.txt", b"hi")})
        assert r.status_code == 400
        r = c.post("/api/v2/upload", files={"file": ("x.jar", b"not a zip")})
        assert r.status_code == 400
        jar = make_jar(env["tmp"] / "up" / "Vault-1.8.jar", "Vault", "1.8")
        r = c.post("/api/v2/upload", files={"file": ("../../Vault-1.8.jar", jar.read_bytes())})
        assert r.status_code == 200, r.text
        up = r.json()
        assert up["plugin_name"] == "Vault" and up["name"] == "Vault-1.8.jar"
        r = c.post("/api/v2/deploy/plan", json={"targets": ["M1-hub01"], "action": "replace",
                                                "items": {"uploads": [up["upload_id"]]}})
        assert r.status_code == 200, r.text
        assert c.post("/api/v2/deploy", json={"action": "replace"}).status_code == 400  # plan_id required
        r = c.post("/api/v2/deploy", json={"plan_id": r.json()["plan_id"]})
        assert r.status_code == 200, r.text
        jobs.wait(jobs.get(r.json()["job_id"]), timeout=30)
    assert jars_of("M1-hub01", "bukkit:vault") == ["Vault-1.8.jar"]


# ---------------------------------------------------------------- updates (mocked HTTP)

def _mock_modrinth(env, new_jar_bytes, fail_hash=False):
    old_sha1 = inventory.file_hash(env["a"] / "CoreProtect-23.1.jar")
    import hashlib
    sha512 = hashlib.sha512(new_jar_bytes).hexdigest()
    if fail_hash:
        sha512 = "0" * 128
    cur = {"id": "v231", "project_id": "Lu3KuzdV", "version_number": "23.1", "version_type": "release",
           "date_published": "2025-01-01T00:00:00Z", "files": []}
    new = {"id": "v241", "project_id": "Lu3KuzdV", "version_number": "24.1", "version_type": "release",
           "date_published": "2026-01-01T00:00:00Z", "game_versions": ["1.21.6"], "loaders": ["paper"],
           "files": [{"url": "https://cdn.modrinth.com/cp.jar", "filename": "CoreProtect-CE-24.1.jar",
                      "primary": True, "hashes": {"sha1": hashlib.sha1(new_jar_bytes).hexdigest(), "sha512": sha512}}]}
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        assert req.headers["user-agent"].startswith("lgt-amp-sync/2.0")
        if req.url.path == "/v2/version_files":
            hashes = json.loads(req.content)["hashes"]
            return httpx.Response(200, json={old_sha1: cur} if old_sha1 in hashes else {})
        if req.url.path == "/v2/version_files/update":
            body = json.loads(req.content)
            assert body["loaders"] == updates.LOADERS["bukkit"] and body["game_versions"] == ["1.21.6"]
            return httpx.Response(200, json={old_sha1: new} if old_sha1 in body["hashes"] else {})
        if req.url.path == "/v2/projects":
            return httpx.Response(200, json=[{"id": "Lu3KuzdV", "title": "CoreProtect", "slug": "coreprotect"}])
        if req.url.host == "cdn.modrinth.com":
            return httpx.Response(200, content=new_jar_bytes)
        return httpx.Response(404)

    updates.TRANSPORT = httpx.MockTransport(handler)
    return seen


def test_update_check_parsing_and_apply(env):
    new_bytes = make_jar(env["tmp"] / "dl" / "x.jar", "CoreProtect", "24.1", extra=b"modrinth").read_bytes()
    seen = _mock_modrinth(env, new_bytes)
    res = updates.check()
    assert res["outdated"] == 1 and res["errors"] == 0
    assert any(r.url.path == "/v2/version_files/update" and json.loads(r.content).get("version_types") == ["release"]
               for r in seen)
    pend = updates.pending_updates()
    assert [(u["key"], u["to_version"], u["servers"]) for u in pend] == [("bukkit:coreprotect", "24.1", ["M1-hub01"])]
    assert pend[0]["source"]["name"] == "CoreProtect"

    plan = updates.create_plan("all", "tester")
    assert [(r["server"], r["from_jar"], r["to_jar"], r["to_version"]) for r in plan["rows"]] == [
        ("M1-hub01", "CoreProtect-23.1.jar", "CoreProtect-CE-24.1.jar", "24.1")]
    assert plan["rows"][0]["compat"] == {"mc_versions": ["1.21.6"], "loaders": ["paper"], "mc": "1.21.6", "ok": True}
    job = jobs.wait(actions.start_apply("tester", plan["plan_id"], dry_run=True), timeout=30)
    assert jars_of("M1-hub01", "bukkit:coreprotect") == ["CoreProtect-23.1.jar"]
    assert job.results and job.results[0]["outcome"] == "changed", job.log
    assert job.summary == "Dry run: 1 would change"

    job = jobs.wait(actions.start_apply("tester", plan["plan_id"]), timeout=30)
    assert job.status == "done", job.log
    assert jars_of("M1-hub01", "bukkit:coreprotect") == ["CoreProtect-CE-24.1.jar"]
    jobs.wait(actions.start_undo("tester", job.id), timeout=30)
    assert jars_of("M1-hub01", "bukkit:coreprotect") == ["CoreProtect-23.1.jar"]


def test_update_apply_rejects_hash_mismatch(env):
    new_bytes = make_jar(env["tmp"] / "dl" / "x.jar", "CoreProtect", "24.1", extra=b"m").read_bytes()
    _mock_modrinth(env, new_bytes, fail_hash=True)
    updates.check()
    job = jobs.wait(actions.start_apply("tester", updates.create_plan("all", "t")["plan_id"]), timeout=30)
    assert job.status == "failed"
    assert "hash mismatch" in job.results[0]["detail"]
    assert jars_of("M1-hub01", "bukkit:coreprotect") == ["CoreProtect-23.1.jar"]


def test_pinned_and_ignored_are_not_outdated(env):
    new_bytes = make_jar(env["tmp"] / "dl" / "x.jar", "CoreProtect", "24.1").read_bytes()
    _mock_modrinth(env, new_bytes)
    updates.check()
    from app import settings
    settings.update({"pins": {"bukkit:coreprotect": "23.1"}})  # legacy shape is migrated
    assert settings.load_raw()["pins"] == {"bukkit:coreprotect": {"version": "23.1", "servers": "*"}}
    assert updates.pending_updates() == []
    settings.update({"pins": {}, "ignores": ["bukkit:coreprotect"]})
    assert updates.pending_updates() == []


def test_display_version_strips_loader_prefix():
    assert updates.display_version("bukkit-2.6.24") == "2.6.24"
    assert updates.display_version("paper-v1.2") == "v1.2"
    assert updates.display_version("5.12.0") == "5.12.0"
    assert updates.display_version("bukkitx") == "bukkitx"


def test_compare_versions():
    assert updates.compare_versions("2.6.24", "2.6.6") == 1
    assert updates.compare_versions("v2.0.5", "2.0.5") == 0
    assert updates.compare_versions("5.8+build.3638", "5.8 build 3638") == 0
    assert updates.compare_versions("1.0", "1.1") == -1


def test_velocity_key_differs_from_bukkit(env):
    make_jar(env["a"] / "LuckPerms-Bukkit-5.5.jar", "LuckPerms", "5.5")
    keys_a = {p["key"] for p in inventory.list_plugins(inventory.get_server("M1-hub01"))}
    keys_p = {p["key"] for p in inventory.list_plugins(inventory.get_server("M0-proxy01"))}
    assert "bukkit:luckperms" in keys_a and "velocity:luckperms" in keys_p


# ---------------------------------------------------------------- regressions from review

def test_install_jar_never_leaves_two_versions(env):
    deploy({"source": "elChapo01", "targets": ["M1-hub01", "M3-hunger01"], "action": "install",
            "items": {"jars": ["CoreProtect-24.1.jar"]}})
    assert jars_of("M1-hub01", "bukkit:coreprotect") == ["CoreProtect-24.1.jar"]
    assert jars_of("M3-hunger01", "bukkit:coreprotect") == ["CoreProtect-24.1.jar"]
    # a top-level jar passed as a path gets the same treatment
    make_jar(env["src"] / "CoreProtect-25.0.jar", "CoreProtect", "25.0")
    deploy({"source": "elChapo01", "targets": ["M1-hub01"], "action": "install",
            "items": {"paths": ["CoreProtect-25.0.jar"]}})
    assert jars_of("M1-hub01", "bukkit:coreprotect") == ["CoreProtect-25.0.jar"]


@pytest.mark.parametrize("action", ["sync", "delete"])
def test_overlapping_items_undo_exactly(env, action):
    before = snapshot_tree(env["a"])
    body = {"targets": ["M1-hub01"], "action": action, "items": {"paths": ["Essentials/config.yml", "Essentials"]}}
    if action == "sync":
        body["source"] = "elChapo01"
    job = deploy(body)
    assert [r["item"] for r in job.results] == (["Essentials/"] if action == "sync" else ["Essentials"])
    jobs.wait(actions.start_undo("t", job.id), timeout=30)
    assert snapshot_tree(env["a"]) == before


def test_backup_store_handles_child_then_parent(env):
    srv = inventory.get_server("M1-hub01")
    before = snapshot_tree(env["a"])
    b = engine.Backup("20990101-000000-aaaaaa")
    ctx = engine.Ctx(dry_run=False, backup=b)
    (env["a"] / "Essentials" / "config.yml").write_text("changed\n")  # pretend: after child backup
    b.entries.clear()
    b.save(srv, "Essentials/config.yml")
    b.save(srv, "Essentials", move=True)
    assert not (env["a"] / "Essentials").exists()
    undo = engine.Ctx(dry_run=False, backup=engine.Backup("20990101-000001-bbbbbb"))
    engine.run_undo(undo, "20990101-000000-aaaaaa")
    after = snapshot_tree(env["a"])
    assert after.keys() == before.keys()


def test_exception_mid_job_keeps_undo(env, monkeypatch):
    orig = engine.sync_path
    calls = {"n": 0}

    def boom(ctx, s, t, rel, install):
        if not ctx.dry_run:
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("boom")
        return orig(ctx, s, t, rel, install)

    monkeypatch.setattr(engine, "sync_path", boom)
    before = snapshot_tree(env["a"])
    job = deploy({"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync",
                  "items": {"paths": ["Essentials/config.yml", "Essentials/messages"]}, "options": {"install": True}})
    assert job.status == "failed" and job.undoable
    assert any("boom" in r["detail"] for r in job.results)
    jobs.wait(actions.start_undo("t", job.id), timeout=30)
    assert snapshot_tree(env["a"]) == before


def test_out_of_order_undo_refused(env):
    j1 = deploy({"source": "elChapo01", "targets": ["M1-hub01"], "action": "replace",
                 "items": {"jars": ["CoreProtect-24.1.jar"]}})
    make_jar(env["src"] / "CoreProtect-25.0.jar", "CoreProtect", "25.0")
    j2 = deploy({"source": "elChapo01", "targets": ["M1-hub01"], "action": "replace",
                 "items": {"jars": ["CoreProtect-25.0.jar"]}})
    with pytest.raises(engine.DeployError, match=j2.id):
        actions.start_undo("t", j1.id)
    jobs.wait(actions.start_undo("t", j2.id), timeout=30)
    jobs.wait(actions.start_undo("t", j1.id), timeout=30)
    assert jars_of("M1-hub01", "bukkit:coreprotect") == ["CoreProtect-23.1.jar"]


def test_failed_update_lookup_is_not_current(env):
    def handler(req):
        if req.url.path == "/v2/version_files":
            sha1 = inventory.file_hash(env["a"] / "CoreProtect-23.1.jar")
            return httpx.Response(200, json={sha1: {"id": "a", "project_id": "p", "version_number": "23.1",
                                                    "version_type": "release", "date_published": "2025-01-01T00:00:00Z"}})
        if req.url.path == "/v2/projects":
            return httpx.Response(200, json=[])
        return httpx.Response(500)
    updates.TRANSPORT = httpx.MockTransport(handler)
    res = updates.check()
    assert res["errors"] >= 1
    srv = inventory.get_server("M1-hub01")
    p = [p for p in inventory.list_plugins(srv) if p["key"] == "bukkit:coreprotect"][0]
    from app import settings
    assert updates.status_for(p, srv, settings.load_raw(), updates.load_cache())[0] == "unknown"


def test_jobs_run_in_submission_order(env):
    order = []
    js = [jobs.submit("t", "u", {}, (lambda i: lambda job: order.append(i))(i)) for i in range(6)]
    for j in js:
        jobs.wait(j, 10)
    assert order == list(range(6))


# ---------------------------------------------------------------- shared config folders

def _essentials(env):
    make_jar(env["a"] / "EssentialsX-2.22.0.jar", "Essentials", "2.22.0")
    make_jar(env["a"] / "EssentialsXChat-2.22.0.jar", "EssentialsChat", "2.22.0", yml="depend: [Essentials]\n")
    make_jar(env["a"] / "EssentialsXSpawn-2.22.0.jar", "EssentialsSpawn", "2.22.0",
             yml="depend:\n  - Essentials\n")
    make_jar(env["a"] / "Unrelated.jar", "EssentialsLookalike", "1", yml="softdepend: [Vault]\n")


def test_remove_skips_shared_folder_unless_forced(env):
    _essentials(env)
    job = jobs.wait(actions.start_remove("t", "bukkit:essentials", ["M1-hub01"], True, False), 30)
    rows = {r["item"]: r for r in job.results}
    assert rows["Essentials"]["outcome"] == "skipped"
    assert rows["Essentials"]["shared_with"] == ["EssentialsChat", "EssentialsSpawn"]
    assert "pass force:true" in rows["Essentials"]["detail"]
    assert (env["a"] / "Essentials").is_dir() and not (env["a"] / "EssentialsX-2.22.0.jar").exists()
    jobs.wait(actions.start_undo("t", job.id), 30)

    job = jobs.wait(actions.start_remove("t", "bukkit:essentials", ["M1-hub01"], True, False, force=True), 30)
    assert {r["item"]: r["outcome"] for r in job.results}["Essentials"] == "changed"
    assert not (env["a"] / "Essentials").exists()


def test_delete_plan_warns_about_shared_folder_with_size(env):
    _essentials(env)
    plan = engine.plan({"targets": ["M1-hub01"], "action": "delete", "items": {"folders": ["Essentials"]}})
    assert plan["warnings"] == [{"server": "M1-hub01", "folder": "Essentials",
                                 "shared_with": ["Essentials", "EssentialsChat", "EssentialsSpawn"]}]
    row = plan["results"][0]
    assert row["outcome"] == "skipped" and row["files"] == 3 and row["size"] > 0
    # deleting the whole family in one request is not "shared"
    plan = engine.plan({"targets": ["M1-hub01"], "action": "delete", "items": {
        "jars": ["EssentialsX-2.22.0.jar", "EssentialsXChat-2.22.0.jar", "EssentialsXSpawn-2.22.0.jar"],
        "folders": ["Essentials"]}})
    assert plan["warnings"] == []
    folder_row = [r for r in plan["results"] if r["item"] == "Essentials"][0]
    assert folder_row["outcome"] == "changed" and folder_row["size"] > 0
    forced = engine.plan({"targets": ["M1-hub01"], "action": "delete", "force": True,
                          "items": {"folders": ["Essentials"]}})
    assert forced["results"][0]["outcome"] == "changed" and forced["warnings"]
