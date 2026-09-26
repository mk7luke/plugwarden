"""Round 8: audit paging, "since you last looked", changelog excerpts, not-running causes, health report cache."""
import json
import time
from datetime import datetime, timedelta, timezone

import httpx

from app import audit, config, health, inventory, jobs, settings, updates, visits
from app.main import app
from conftest import client_for, make_jar, write_log
from test_engine import _mock_modrinth

T = "[{t}] [Server thread/INFO]: "
E = "[{t}] [Server thread/ERROR]: "


def L(prefix, msg, when):
    return prefix.format(t=when.strftime("%H:%M:%S")) + msg


# ---------------------------------------------------------------- (a) audit paging

def test_audit_cursor_paging_and_changes_only(env):
    for i in range(7):
        audit.record("a@x", "settings", servers=[], path=f"field{i}")
        audit.record("a@x", "diff", servers=["M1-hub01", "elChapo01"], path=f"file{i}.yml")
    with client_for(app) as c:
        seen, before, pages = [], None, 0
        while True:
            r = c.get("/api/v2/access-log", params={"limit": 5, **({"before": before} if before else {})}).json()
            seen += [e["id"] for e in r["entries"]]
            pages += 1
            if not r["has_more"]:
                assert r["next_before"] is None
                break
            before = r["next_before"]
        assert pages == 3 and len(seen) == 14 and len(set(seen)) == 14  # no gaps, no repeats
        ch = c.get("/api/v2/access-log", params={"limit": 50, "changes_only": True}).json()
        assert len(ch["entries"]) == 7 and {e["action"] for e in ch["entries"]} == {"settings"}
        # newest first, across pages too
        lst = c.get("/api/v2/access-log", params={"limit": 50}).json()["entries"]
        assert [e["id"] for e in lst] == seen


# ---------------------------------------------------------------- (b) since you last looked

def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def test_since_last_visit(env, monkeypatch):
    with client_for(app) as c:
        first = c.get("/api/v2/overview").json()["since_last_visit"]
        assert first == {"at": None, "new_updates": [], "new_updates_total": 0, "is_backlog": False,
                         "new_failures": [], "jobs_by_others": []}
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        assert c.post("/api/v2/seen", json={"at": _iso(past)}).json()["at"] == _iso(past)
        # never moves backwards; "at" in the future is capped at now
        assert c.post("/api/v2/seen", json={"at": _iso(past - timedelta(days=1))}).json()["at"] == _iso(past)

        # an update found after the visit, one found before
        new_bytes = make_jar(env["tmp"] / "dl" / "x.jar", "CoreProtect", "24.1").read_bytes()
        _mock_modrinth(env, new_bytes)
        updates.check()
        cache = updates.load_cache()
        assert cache["first_seen"] == {"bukkit:coreprotect|24.1": cache["checked_at"]}
        # a job by someone else, one by me (local), a dry run by someone else
        for user, dry in (("bob@x", False), ("local", False), ("bob@x", True)):
            j = jobs.Job("deploy", user, {}, mutating=True, dry_run=dry)
            j.status, j.summary, j.finished = "done", f"by {user}", jobs.now_iso()
            j.changed_servers = ["M1-hub01"]
            j.save()
        # a plugin that stopped running on a start after the visit
        now = datetime.now() - timedelta(minutes=30)
        write_log(env["src"].parent / "logs", now, [
            L(T, "[CoreProtect] Enabling CoreProtect v24.1", now),
            L(E, "Error occurred while enabling CoreProtect v24.1 (Is it up to date?)", now),
            L(T, "Done (60s)!", now)])
        health.refresh_summaries()
        s = c.get("/api/v2/overview").json()["since_last_visit"]
        assert s["at"] == _iso(past)
        assert [(u["key"], u["to_version"], u["servers"]) for u in s["new_updates"]] == \
            [("bukkit:coreprotect", "24.1", ["M1-hub01"])]
        assert [(f["server"], f["name"]) for f in s["new_failures"]] == [("elChapo01", "CoreProtect")]
        assert [j["user"] for j in s["jobs_by_others"]] == ["bob@x"]
        # after looking, nothing is new
        c.post("/api/v2/seen", json={})
        s = c.get("/api/v2/overview").json()["since_last_visit"]
        assert s["new_updates"] == [] and s["new_failures"] == [] and s["jobs_by_others"] == []
        assert c.post("/api/v2/seen", json={"at": 5}).status_code == 400


def test_first_seen_migration_does_not_flag_old_updates(env):
    new_bytes = make_jar(env["tmp"] / "dl" / "x.jar", "CoreProtect", "24.1").read_bytes()
    _mock_modrinth(env, new_bytes)
    updates.check()
    cache = updates.load_cache()
    old = "2020-01-01T00:00:00+00:00"
    cache.pop("first_seen")
    cache["checked_at"] = old
    updates.write_json(updates._cache_file(), cache)
    updates.check()
    assert updates.load_cache()["first_seen"] == {"bukkit:coreprotect|24.1": old}


# ---------------------------------------------------------------- (c) changelog excerpts

def test_changelog_excerpt_is_plain_text():
    md = ("## What's new\n<!-- x -->\n- **Breaking:** needs *Java 21*, see [guide](https://x/y)\n"
          "- `config.yml` <script>alert(1)</script> &lt;img src=x onerror=alert(1)&gt;\n---\n"
          "| a | b |\n|---|---|\n![logo](https://x/y.png)\n> quote\nl7\nl8\n")
    ex = updates.changelog_excerpt(md)
    assert ex["lines"][:2] == ["• Breaking: needs Java 21, see guide", "• config.yml alert(1)"]
    assert len(ex["lines"]) == 6 and ex["truncated"] is True  # "What's new" is skipped as a bare heading
    assert not any("<" in ln or ">" in ln or "](" in ln for ln in ex["lines"])
    assert updates.changelog_excerpt("") is None and updates.changelog_excerpt(None) is None
    assert updates.changelog_excerpt("<p> </p>") is None
    assert updates.changelog_excerpt("one\n\ntwo") == {"lines": ["one", "two"], "truncated": False}


def test_changelog_from_check_on_updates_and_plan_without_network_on_reads(env):
    new_bytes = make_jar(env["tmp"] / "dl" / "x.jar", "CoreProtect", "24.1").read_bytes()
    _mock_modrinth(env, new_bytes, changelog="# 24.1\n* Fixed **rollback**\n* Java 21 required")
    updates.check()

    def boom(req):
        raise AssertionError(f"network on a read path: {req.url}")
    updates.TRANSPORT = httpx.MockTransport(boom)
    with client_for(app) as c:
        u = c.get("/api/v2/updates").json()
        rows = u if isinstance(u, list) else u.get("updates", u.get("items"))
        cp = next(x for x in rows if x["key"] == "bukkit:coreprotect")
        want = {"lines": ["• Fixed rollback", "• Java 21 required"], "truncated": False}  # "# 24.1" skipped
        assert cp["changelog"] == want and cp["changelog_url"].startswith("https://modrinth.com/")
        plan = c.post("/api/v2/updates/plan", json={"items": "all"}).json()
        assert plan["rows"][0]["changelog"] == want


def test_hangar_and_github_changelogs():
    gh = {"tag_name": "v1.2", "id": 5, "html_url": "https://github.com/o/r/releases/v1.2", "body": "## Changes\r\n- fix",
          "assets": [{"name": "x-paper.jar", "browser_download_url": "https://github.com/o/r/x.jar"}]}
    hg_ver = {"id": 9, "description": "**New:** stuff", "createdAt": "2026-01-01T00:00:00Z",
              "downloads": {"PAPER": {"downloadUrl": "https://hangarcdn.papermc.io/x.jar", "fileInfo": {"name": "x.jar"}}},
              "platformDependencies": {"PAPER": ["1.21.6"]}}

    def handler(req):
        if req.url.host == "api.github.com":
            return httpx.Response(200, json=gh)
        if req.url.path.endswith("/latestrelease"):
            return httpx.Response(200, text="1.2")
        return httpx.Response(200, json=hg_ver)
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assert updates._resolve_github(c, "o/r", "bukkit", "1.21.6")["changelog"]["lines"] == ["• fix"]
        assert updates._resolve_hangar(c, "x", "bukkit", "1.21.6")["changelog"]["lines"] == ["New: stuff"]


# ---------------------------------------------------------------- (d) why a plugin is not running

def test_classify_causes():
    cc = health.classify_cause
    c = cc(["[00:01:36] [VoiceChatServerThread/ERROR]: [voicechat] Failed to run voice chat at UDP port 24454, "
            "make sure no other application is running at that port"])
    assert c["kind"] == "port_in_use" and c["detail"] == {"port": 24454, "protocol": "UDP"} and "UDP port 24454" in c["suggestion"]
    c = cc(["java.net.BindException: Address already in use", "  while binding port 8192"])
    assert c["kind"] == "port_in_use" and c["detail"]["port"] == 8192
    c = cc(["org.bukkit.plugin.UnknownDependencyException: Unknown/missing dependency plugins: [TheCore, Vault]. "
            "Please download and install these plugins to run 'Voting'."])
    assert c["kind"] == "missing_dependency" and [d["name"] for d in c["detail"]["dependencies"]] == ["TheCore", "Vault"]
    assert cc(["org.bukkit.plugin.UnknownDependencyException: Vault"])["detail"]["dependencies"] == [{"name": "Vault"}]
    assert cc(["java.lang.UnsupportedClassVersionError: x has been compiled by a more recent version of the Java Runtime"])["kind"] == "unsupported_version"
    assert cc(["[X] Unsupported API version 1.22"])["kind"] == "unsupported_version"
    assert cc(["org.bukkit.configuration.InvalidConfigurationException: while parsing a block mapping"])["kind"] == "config_error"
    assert cc(["java.lang.NullPointerException: null"]) == {"kind": "unknown", "detail": {},
                                                            "suggestion": health.SUGGESTIONS["unknown"]}


def test_missing_dependency_cause_points_at_source_jar(env):
    settings.mutate(lambda raw: raw.update(default_source="elChapo01"))
    make_jar(env["src"] / "TheCore-3.6.5.jar", "TheCore", "3.6.5")
    make_jar(env["a"] / "Voting-5.3.0.jar", "Voting", "5.3.0")
    now = datetime.now() - timedelta(minutes=30)
    write_log(env["a"].parent / "logs", now, [
        L(E, "[ModernPluginLoadingStrategy] Could not load 'plugins/.paper-remapped/Voting-5.3.0.jar' in 'plugins/.paper-remapped'", now),
        "org.bukkit.plugin.UnknownDependencyException: Unknown/missing dependency plugins: [TheCore]. Please download and install these plugins to run 'Voting'.",
        "\tat io.papermc.paper.plugin.entrypoint.strategy.modern.ModernPluginLoadingStrategy.loadProviders(ModernPluginLoadingStrategy.java:82)",
        L(T, "Done (60s)!", now)])
    inventory.index_all()
    with client_for(app) as c:
        r = c.get("/api/v2/servers/M1-hub01/health").json()
        v = next(p for p in r["plugins"] if p["name"] == "Voting")
        assert v["status"] == "failed" and v["running"] is False
        assert v["cause"]["kind"] == "missing_dependency"
        assert v["cause"]["detail"]["dependencies"] == [{"name": "TheCore", "installed": False, "source": "elChapo01",
                                                         "on_source": True, "source_jar": "TheCore-3.6.5.jar",
                                                         "key": "bukkit:thecore"}]
        assert "TheCore-3.6.5.jar is on elChapo01" in v["cause"]["suggestion"]
        health.refresh_summaries()
        tile = next(s for s in c.get("/api/v2/overview").json()["servers"] if s["id"] == "M1-hub01")
        assert tile["startup"]["failed"][0]["cause"]["kind"] == "missing_dependency"
    # healthy plugins carry no cause
    assert all("cause" not in p for p in r["plugins"] if p["status"] != "failed")


# ---------------------------------------------------------------- (e) health report cache

def test_health_report_cached_until_logs_or_jars_change(env, monkeypatch):
    now = datetime.now() - timedelta(minutes=30)
    logs = env["a"].parent / "logs"
    write_log(logs, now, [L(T, "[CoreProtect] Enabling CoreProtect v23.1", now), L(T, "Done (60s)!", now)])
    inventory.index_all()
    srv = inventory.get_server("M1-hub01")
    calls = []
    real = health._server_report
    monkeypatch.setattr(health, "_server_report", lambda s, since: calls.append(since) or real(s, since))
    r1 = health.server_report(srv, None)
    t = time.perf_counter()
    r2 = health.server_report(srv, None)
    assert r2 is r1 and len(calls) == 1 and time.perf_counter() - t < 0.05
    health.server_report(srv, time.time() - 3600)  # "since" reports are never served from the cache
    assert len(calls) == 2
    time.sleep(0.01)
    write_log(logs, now, [L(T, "[CoreProtect] Enabling CoreProtect v23.1", now), L(T, "Done (61.5s)!", now)])
    health.server_report(srv, None)
    assert len(calls) == 3
    make_jar(env["a"] / "Extra-1.0.jar", "Extra", "1.0")
    health.server_report(srv, None)
    assert len(calls) == 4


# ---------------------------------------------------------------- round 8 polish

def test_changelog_unescapes_and_skips_boilerplate():
    md = ("If you enjoy CoreProtect, please consider supporting development on [Patreon](https://patreon.com/x)!\n"
          "Join our Discord: https://discord.gg/abc\n## Changelog\n# v24.1\n### Version 24.1\nWhat's new in 24.1\n"
          "- Added support for Minecraft 26\\.2\\.\n- Fixed container interactions\\. \\(again\\) \\*not italic\\*\n"
          "- Fixed Discord integration sending twice\n- Donate: https://ko-fi.com/x\n1.21.6\n- Real change\n")
    assert updates.changelog_excerpt(md)["lines"] == [
        "• Added support for Minecraft 26.2.", "• Fixed container interactions. (again) *not italic*",
        "• Fixed Discord integration sending twice", "• Real change"]
    assert updates.changelog_excerpt("## Changelog\n# 1.0\nSupport us on Patreon!") is None


def test_since_last_visit_backlog_flag_and_order(env):
    new_bytes = make_jar(env["tmp"] / "dl" / "x.jar", "CoreProtect", "24.1").read_bytes()
    _mock_modrinth(env, new_bytes)
    with client_for(app) as c:
        c.post("/api/v2/seen", json={"at": "2020-01-01T00:00:00+00:00"})  # looked long before any check
        updates.check()
        s = c.get("/api/v2/overview").json()["since_last_visit"]
        assert s["new_updates_total"] == 1 and s["is_backlog"] is True
        c.post("/api/v2/seen", json={})
        s = c.get("/api/v2/overview").json()["since_last_visit"]
        assert s["new_updates"] == [] and s["is_backlog"] is False
    # newest first
    cache = updates.load_cache()
    cache["first_seen"] = {"bukkit:coreprotect|24.1": "2030-01-01T00:00:00+00:00"}
    updates.write_json(updates._cache_file(), cache)
    pend = [{"key": "bukkit:coreprotect", "name": "CoreProtect", "to_version": "24.1", "from_versions": ["23.1"],
             "servers": ["M1-hub01"]},
            {"key": "bukkit:vault", "name": "Vault", "to_version": "1.7.3", "from_versions": ["1.7.0"], "servers": ["M1-hub01"]}]
    cache["first_seen"]["bukkit:vault|1.7.3"] = "2031-01-01T00:00:00+00:00"
    cache["first_checked_at"] = "2024-01-01T00:00:00+00:00"  # the visit below is after the first check
    updates.write_json(updates._cache_file(), cache)
    visits.mark_seen("x@y", "2025-01-01T00:00:00+00:00")
    s = visits.since_last_visit("x@y", pend)
    # every pending update is new since the visit: backlog
    assert [u["name"] for u in s["new_updates"]] == ["Vault", "CoreProtect"] and s["is_backlog"] is True
    s = visits.since_last_visit("x@y", pend + [{**pend[0], "key": "bukkit:old", "to_version": "9"}])
    assert s["new_updates_total"] == 2 and s["is_backlog"] is False  # an older update exists: not the whole list
