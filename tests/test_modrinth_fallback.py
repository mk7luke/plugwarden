"""Modrinth POST blocked by the WAF: GET-only fallback, hash caching, post_blocked_until, rate limits."""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import inventory, updates
from conftest import make_jar

BLOCK_PAGE = "<!DOCTYPE html><title>Request blocked · Modrinth</title>looked like automated or malicious traffic"


def _v(vid, pid, number, vtype, published, changelog=None):
    return {"id": vid, "project_id": pid, "version_number": number, "version_type": vtype,
            "date_published": published, "game_versions": ["1.21.6"], "loaders": ["paper"],
            "changelog": changelog,
            "files": [{"url": f"https://cdn.modrinth.com/{vid}.jar", "filename": f"{pid}-{number}.jar",
                       "primary": True, "size": 10, "hashes": {"sha1": "a" * 40, "sha512": "b" * 128}}]}


# One model of Modrinth, served both through the POST endpoints and the GET endpoints.
PROJECTS = {
    "Lu3KuzdV": [_v("v231", "Lu3KuzdV", "23.1", "release", "2025-01-01T00:00:00Z"),
                 _v("v241", "Lu3KuzdV", "bukkit-24.1", "release", "2026-01-01T00:00:00Z", "* Fixed **rollback**"),
                 _v("v250b", "Lu3KuzdV", "25.0-beta", "beta", "2026-02-01T00:00:00Z")],
    "vaultPid": [_v("vb1", "vaultPid", "1.7.0-beta", "beta", "2025-01-01T00:00:00Z"),
                 _v("vb2", "vaultPid", "1.7.1-beta", "beta", "2026-03-01T00:00:00Z", "- faster")],
}
TITLES = [{"id": "Lu3KuzdV", "title": "CoreProtect", "slug": "coreprotect"},
          {"id": "vaultPid", "title": "Vault", "slug": "vault"}]


class Modrinth:
    def __init__(self, env, post="ok", fail_hashes=()):
        self.post = post  # "ok" | "403" | "429" | "html200"
        self.fail_hashes = set(fail_hashes)
        self.seen: list[httpx.Request] = []
        h = lambda srv, jar: inventory.file_hash(env[srv] / jar)  # noqa: E731
        self.by_hash = {h("a", "CoreProtect-23.1.jar"): PROJECTS["Lu3KuzdV"][0],
                        h("src", "CoreProtect-24.1.jar"): PROJECTS["Lu3KuzdV"][1],
                        h("a", "Vault.jar"): PROJECTS["vaultPid"][0]}

    def calls(self, method=None, prefix=""):
        return [r for r in self.seen if (method is None or r.method == method) and r.url.path.startswith(prefix)]

    @staticmethod
    def _versions(pid, loaders, game_versions, types=None):
        vs = [v for v in PROJECTS[pid] if set(v["loaders"]) & set(loaders)
              and (not game_versions or set(v["game_versions"]) & set(game_versions))
              and (not types or v["version_type"] in types)]
        return sorted(vs, key=lambda v: v["date_published"], reverse=True)

    def __call__(self, req: httpx.Request):
        self.seen.append(req)
        path = req.url.path
        if req.method == "POST":
            if self.post == "403":
                return httpx.Response(403, text=BLOCK_PAGE, headers={"content-type": "text/html"})
            if self.post == "429":
                return httpx.Response(429, text="slow down")
            if self.post == "html200":
                return httpx.Response(200, text=BLOCK_PAGE, headers={"content-type": "text/html"})
            body = json.loads(req.content)
            if path == "/v2/version_files":
                return httpx.Response(200, json={h: self.by_hash[h] for h in body["hashes"] if h in self.by_hash})
            if path == "/v2/version_files/update":
                out = {}
                for h in body["hashes"]:
                    if h in self.by_hash:
                        vs = self._versions(self.by_hash[h]["project_id"], body["loaders"], body["game_versions"],
                                            body.get("version_types"))
                        if vs:
                            out[h] = vs[0]
                return httpx.Response(200, json=out)
        if path.startswith("/v2/version_file/"):
            sha1 = path.rsplit("/", 1)[1]
            assert req.url.params["algorithm"] == "sha1"
            if sha1 in self.fail_hashes:
                return httpx.Response(500)
            if sha1 in self.by_hash:
                return httpx.Response(200, json=self.by_hash[sha1])
            return httpx.Response(404, json={"error": "not_found"})
        if path.startswith("/v2/project/") and path.endswith("/version"):
            pid = path.split("/")[3]
            gv = json.loads(req.url.params.get("game_versions", "[]"))
            return httpx.Response(200, json=self._versions(pid, json.loads(req.url.params["loaders"]), gv))
        if path == "/v2/projects":
            return httpx.Response(200, json=TITLES)
        return httpx.Response(404)


@pytest.fixture()
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(updates, "_sleep", slept.append)
    return slept


def _entries():
    return {k: {f: v for f, v in e.items() if f != "checked_at"} for k, e in updates.load_cache()["entries"].items()}


def _install(mock):
    updates.TRANSPORT = httpx.MockTransport(mock)
    return mock


@pytest.mark.parametrize("block", ["403", "429", "html200"])
def test_get_fallback_matches_post_results(env, no_sleep, block):
    make_jar(env["a"] / "Vault.jar", "Vault", "1.7.0-beta")  # a pre-release install: exercises latest_any
    inventory.reset_cache()
    post = _install(Modrinth(env))
    res_post = updates.check()
    want = _entries()
    assert res_post["errors"] == 0 and res_post["outdated"] == 2
    assert not post.calls("GET", "/v2/version_file/") and not post.calls("GET", "/v2/project/")
    cp = want[updates.cache_key(inventory.file_hash(env["a"] / "CoreProtect-23.1.jar"), "bukkit", "1.21.6")]
    assert cp["latest"]["version"] == "24.1" and cp["outdated"]  # loader prefix dropped, beta not offered
    assert cp["latest"]["changelog"] == {"lines": ["• Fixed rollback"], "truncated": False}
    assert cp["latest"]["hashes"] == {"sha1": "a" * 40, "sha512": "b" * 128}
    vault = want[updates.cache_key(inventory.file_hash(env["a"] / "Vault.jar"), "bukkit", "1.21.6")]
    assert vault["latest"]["version"] == "1.7.1-beta" and vault["outdated"]
    first_seen = updates.load_cache()["first_seen"]

    updates._cache_file().unlink()
    get = _install(Modrinth(env, post=block))
    res_get = updates.check()
    assert _entries() == want
    assert res_get == res_post
    assert updates.load_cache()["first_seen"].keys() == first_seen.keys()
    # one POST attempt (blocked), then GETs only; a project's versions are fetched once per (loaders, mc)
    assert len(get.calls("POST")) == 1
    proj = [r.url.path for r in get.calls("GET", "/v2/project/")]
    assert sorted(proj) == ["/v2/project/Lu3KuzdV/version", "/v2/project/vaultPid/version"]
    assert json.loads(get.calls("GET", "/v2/project/")[0].url.params["game_versions"]) == ["1.21.6"]
    assert updates.pending_updates()[0]["source"]["name"] == "CoreProtect"


def test_post_blocked_until_skips_post(env, no_sleep):
    mock = _install(Modrinth(env, post="403"))
    updates.check()
    until = datetime.fromisoformat(updates.load_cache()["post_blocked_until"])
    assert timedelta(hours=23) < until - datetime.now(timezone.utc) <= timedelta(hours=24)

    mock.seen.clear()
    updates.check()
    assert not mock.calls("POST")
    # identified hashes are permanent, unknown ones are fresh: no hash lookups at all the second time
    assert not mock.calls("GET", "/v2/version_file/")
    assert mock.calls("GET", "/v2/project/")

    # once expired, POST is tried again; success clears the block
    cache = updates.load_cache()
    cache["post_blocked_until"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    updates.write_json(updates._cache_file(), cache)
    mock.post = "ok"
    mock.seen.clear()
    assert updates.check()["errors"] == 0
    assert mock.calls("POST", "/v2/version_files") and not mock.calls("GET", "/v2/version_file/")
    assert updates.load_cache()["post_blocked_until"] is None


def test_unknown_hashes_are_cached_for_seven_days(env, no_sleep):
    mock = _install(Modrinth(env, post="403"))
    updates.check()
    unknown = {r.url.path.rsplit("/", 1)[1] for r in mock.calls("GET", "/v2/version_file/")} - set(mock.by_hash)
    assert unknown  # Vault 1.7.3 on elChapo01 is not on Modrinth
    hashes = updates.load_cache()["hashes"]
    assert all("miss_at" in hashes[h] for h in unknown)
    assert all(set(hashes[h]["v"]) == set(updates._VERSION_FIELDS) for h in mock.by_hash)

    mock.seen.clear()
    updates.check()
    assert not mock.calls("GET", "/v2/version_file/")

    cache = updates.load_cache()
    stale = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(timespec="seconds")
    for h in unknown:
        cache["hashes"][h]["miss_at"] = stale
    updates.write_json(updates._cache_file(), cache)
    mock.seen.clear()
    updates.check()
    assert {r.url.path.rsplit("/", 1)[1] for r in mock.calls("GET", "/v2/version_file/")} == unknown


def test_failed_hash_lookup_keeps_previous_entry(env, no_sleep):
    mock = _install(Modrinth(env, post="403"))
    updates.check()
    before = _entries()
    cp_sha = inventory.file_hash(env["a"] / "CoreProtect-23.1.jar")
    cache = updates.load_cache()
    del cache["hashes"][cp_sha]
    updates.write_json(updates._cache_file(), cache)

    mock.fail_hashes = {cp_sha}
    mock.seen.clear()
    res = updates.check()
    assert res["errors"] >= 1
    ck = updates.cache_key(cp_sha, "bukkit", "1.21.6")
    after = updates.load_cache()["entries"][ck]
    assert after["stale"] is True and after["latest"] == before[ck]["latest"]
    assert len([r for r in mock.calls("GET", "/v2/version_file/") if cp_sha in r.url.path]) == 1 + updates.GET_RETRIES
    assert no_sleep[:updates.GET_RETRIES] == [1.0, 2.0, 4.0]  # exponential backoff on 5xx


def test_all_hash_lookups_failing_keeps_previous_results(env, no_sleep):
    mock = _install(Modrinth(env, post="403"))
    updates.check()
    before = updates.load_cache()["entries"]
    cache = updates.load_cache()
    cache["hashes"] = {}
    updates.write_json(updates._cache_file(), cache)
    mock.fail_hashes = set(mock.by_hash) | {inventory.file_hash(env["src"] / "Vault.jar"),
                                            inventory.file_hash(env["proxy"] / "LuckPerms-Velocity-5.5.jar")}
    with pytest.raises(RuntimeError, match="previous results kept"):
        updates.check()
    assert updates.load_cache()["entries"] == before
    assert updates.load_cache()["post_blocked_until"]


def test_rate_limit_headers_and_retries(no_sleep, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(updates.time, "monotonic", lambda: clock[0])
    replies = iter([
        httpx.Response(200, json={}, headers={"X-Ratelimit-Remaining": "0", "X-Ratelimit-Reset": "7"}),
        httpx.Response(429, headers={"X-Ratelimit-Remaining": "0", "X-Ratelimit-Reset": "3"}),
        httpx.Response(200, json={}, headers={"X-Ratelimit-Remaining": "250", "X-Ratelimit-Reset": "40"}),
        httpx.Response(404),
    ])
    seen = []
    transport = httpx.MockTransport(lambda r: (seen.append(r), next(replies))[1])
    with httpx.Client(transport=transport) as c:
        mr = updates._ModrinthRun(c, {}, lambda s: None)
        assert mr.get(f"{updates.MODRINTH}/a").status_code == 200
        assert no_sleep == []  # nothing known about the budget yet
        # budget exhausted: wait for the reset, then a 429 waits for its reset and is retried
        assert mr.get(f"{updates.MODRINTH}/b").status_code == 200
        assert no_sleep == [7.0, 3.0]
        assert mr.get(f"{updates.MODRINTH}/c").status_code == 404  # 4xx: not retried, no wait
        assert no_sleep == [7.0, 3.0] and len(seen) == 4 and mr.requests == 4


def test_post_errors_that_are_not_blocks_still_fail(env, no_sleep):
    def handler(req):
        return httpx.Response(500) if req.method == "POST" else httpx.Response(200, json=[])
    updates.TRANSPORT = httpx.MockTransport(handler)
    with pytest.raises(RuntimeError, match="previous results kept"):
        updates.check()
    assert updates.load_cache()["post_blocked_until"] is None
