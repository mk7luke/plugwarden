"""Testing a source before mapping it (POST /sources/test), and where a plugin's status came from."""
import httpx
import pytest

from app import inventory, updates
from app.main import app
from app.storage import write_json
from conftest import client_for

PROJECT = {"id": "xP3pBOC7", "title": "HarvestXP", "slug": "harvest-exp"}
VERSION = {"id": "MaFrO8OC", "project_id": "xP3pBOC7", "version_number": "1.0", "version_type": "release",
           "date_published": "2026-06-21T16:48:33Z", "game_versions": ["1.21.6"], "loaders": ["paper"],
           "files": [{"url": "https://cdn.modrinth.com/data/xP3pBOC7/versions/MaFrO8OC/harvestxp-1.0.jar",
                      "filename": "harvestxp-1.0.jar", "primary": True, "size": 10, "hashes": {"sha1": "a" * 40}}]}


@pytest.fixture()
def modrinth(env, monkeypatch):
    monkeypatch.setattr(updates, "_last_test", {})
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.path in ("/v2/project/harvest-exp", "/v2/project/xP3pBOC7"):
            return httpx.Response(200, json=PROJECT)
        if req.url.path == "/v2/project/xP3pBOC7/version":
            return httpx.Response(200, json=[VERSION])
        return httpx.Response(404, json={"error": "not_found"})
    updates.TRANSPORT = httpx.MockTransport(handler)
    return seen


def test_modrinth_slug_found_with_latest_release(modrinth):
    r = client_for(app).post("/api/v2/sources/test", json={"kind": "modrinth", "id": "harvest-exp", "server": "M1-hub01"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] and d["project"]["id"] == "xP3pBOC7" and d["project"]["slug"] == "harvest-exp"
    assert d["latest"]["version"] == "1.0"
    assert len(modrinth) == 2  # project + versions, nothing else


def test_wrong_slug_says_so(modrinth):
    d = client_for(app).post("/api/v2/sources/test", json={"kind": "modrinth", "id": "harvestxp", "server": "M1-hub01"}).json()
    assert d == {"ok": False, "error": d["error"]} and "no project “harvestxp”" in d["error"]


def test_tests_are_throttled(modrinth):
    c = client_for(app)
    body = {"kind": "modrinth", "id": "harvest-exp", "server": "M1-hub01"}
    assert c.post("/api/v2/sources/test", json=body).status_code == 200
    assert c.post("/api/v2/sources/test", json=body).status_code == 429
    assert len(modrinth) == 2


def test_invalid_id_and_unknown_server_make_no_requests(modrinth):
    c = client_for(app)
    assert c.post("/api/v2/sources/test", json={"kind": "modrinth", "id": "../x", "server": "M1-hub01"}).status_code == 400
    assert c.post("/api/v2/sources/test", json={"kind": "spiget", "id": "abc", "server": "M1-hub01"}).status_code == 400
    assert c.post("/api/v2/sources/test", json={"kind": "modrinth", "id": "harvest-exp/version", "server": "M1-hub01"}).status_code == 400
    assert c.post("/api/v2/sources/test", json={"kind": "ftp", "id": "x", "server": "M1-hub01"}).status_code == 400
    assert c.post("/api/v2/sources/test", json={"kind": "modrinth", "id": "x", "server": "nope"}).status_code == 404
    assert modrinth == []


def test_plugin_rows_say_where_the_status_came_from(env):
    srv = inventory.get_server("M1-hub01")
    plugins = {p["key"]: p for p in inventory.list_plugins(srv)}
    cp, vault = plugins["bukkit:coreprotect"], plugins["bukkit:vault"]
    entries = {
        updates.cache_key(cp["sha1"], srv.family, srv.mc_version): {
            "key": cp["key"], "source": {"kind": "modrinth", "id": "Lu3KuzdV", "url": "https://modrinth.com/project/Lu3KuzdV",
                                         "slug": "coreprotect"},
            "current": {"version": "23.1", "version_id": "v231"}, "latest": None, "outdated": False,
            "error": None, "checked_at": "2026-10-01T00:00:00+00:00", "stale": True},
        updates.cache_key(vault["sha1"], srv.family, srv.mc_version): {
            "key": vault["key"], "source": {"kind": "spiget", "id": "34315", "url": "https://www.spigotmc.org/resources/34315/",
                                            "manual": True},
            "current": {"version": "1.7.0"}, "latest": None, "outdated": False, "error": None,
            "checked_at": "2026-10-02T00:00:00+00:00"},
    }
    write_json(updates._cache_file(), {"checked_at": None, "entries": entries})
    rows = {p["key"]: p for p in client_for(app).get("/api/v2/servers/M1-hub01/plugins").json()}
    assert rows["bukkit:coreprotect"]["installed_url"] == "https://modrinth.com/project/Lu3KuzdV/version/v231"
    assert rows["bukkit:coreprotect"]["stale"] is True
    assert rows["bukkit:coreprotect"]["checked_at"] == "2026-10-01T00:00:00+00:00"
    assert rows["bukkit:vault"]["installed_url"] is None and rows["bukkit:vault"]["stale"] is False


def test_throttle_is_per_source_and_longer_for_github(modrinth):
    c = client_for(app)
    assert c.post("/api/v2/sources/test", json={"kind": "modrinth", "id": "harvest-exp", "server": "M1-hub01"}).status_code == 200
    # a different source isn't blocked by a Modrinth test, but GitHub's own window is long
    assert c.post("/api/v2/sources/test", json={"kind": "github", "id": "a/b", "server": "M1-hub01"}).status_code == 200
    updates._last_test["github"] -= updates.TEST_INTERVAL_DEFAULT + 1
    assert c.post("/api/v2/sources/test", json={"kind": "github", "id": "a/b", "server": "M1-hub01"}).status_code == 429


def test_non_project_answer_is_an_error_not_a_crash(env, monkeypatch):
    monkeypatch.setattr(updates, "_last_test", {})
    updates.TRANSPORT = httpx.MockTransport(lambda req: httpx.Response(200, json=[]))
    d = updates.test_source("modrinth", "x", "bukkit", "1.21.6")
    assert d["ok"] is False
