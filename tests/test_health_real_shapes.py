"""Health regressions from real (anonymised) log shapes: crashed starts in same-day rotated logs, long-standing
soft errors, and which plugins count as "not running"."""
import gzip
import os
from datetime import datetime, timedelta

from app import health, inventory
from app.main import app
from conftest import client_for, make_jar, write_log

T = "[{t}] [Server thread/INFO]: "
E = "[{t}] [Server thread/ERROR]: "


def L(prefix, msg, t):
    return prefix.format(t=t) + msg


def _day():
    return (datetime.now() - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _at(day, hms):
    h, m, s = map(int, hms.split(":"))
    return day.replace(hour=h, minute=m, second=s)


def _log(logs, day, hms, body, name, mtime=None):
    p = write_log(logs, _at(day, hms), body, name=name)
    if mtime:  # rotated files get the mtime of the rotation, not of their content
        ts = _at(day, mtime).timestamp()
        os.utime(p, (ts, ts))
    return p


def _check(srv, name, version, jar):
    return health.plugin_health(srv, health.read_runs(srv), {"name": name, "version": version, "jar": jar},
                                {"main": None, "id": name})


def test_crashed_starts_in_same_day_rotated_logs_do_not_swallow_the_latest_start(env):
    """M4 shape: a 02:23 start that died without "Done"/"Stopping" (-3.log.gz), a 12:17:00 start on the old
    Java where the updated plugin failed (-4.log.gz, written at 12:17:36), then latest.log: a 12:17:36 start
    on the new Java where it enabled. The judged run is the latest.log one."""
    day, logs = _day(), env["src"].parent / "logs"
    ymd = day.strftime("%Y-%m-%d")
    _log(logs, day, "00:00:08", [L(T, "Starting minecraft server version 1.21.6", "00:00:27"),
                                 L(T, "[BentoBox] Enabling BentoBox v3.10.1", "00:00:47"),
                                 L(T, 'Done (74.533s)! For help, type "help"', "00:01:22"),
                                 L(T, "Stopping server", "02:18:27")], f"{ymd}-2.log.gz", mtime="02:23:18")
    _log(logs, day, "02:23:18", [" - BentoBox (3.23.1), ChestSort (14.2.0)"], f"{ymd}-3.log.gz", mtime="12:17:00")
    _log(logs, day, "12:17:00", [
        L(T, "Starting minecraft server version 1.21.6", "12:17:24"),
        L(E, "[ModernPluginLoadingStrategy] Could not load plugin 'BentoBox-3.23.1.jar' in folder 'plugins'", "12:17:31"),
        "org.bukkit.plugin.InvalidPluginException: java.lang.UnsupportedClassVersionError: world/bentobox/bentobox/"
        "BentoBox has been compiled by a more recent version of the Java Runtime (class file version 69.0)"],
        f"{ymd}-4.log.gz", mtime="12:17:36")
    latest = _log(logs, day, "12:17:36", [L(T, "Starting minecraft server version 1.21.6", "12:17:59"),
                                          L(T, "[BentoBox] Enabling BentoBox v3.23.1", "12:18:21"),
                                          L(T, 'Done (67.113s)! For help, type "help"', "12:18:41")], "latest.log")
    ts = _at(day, "12:23:40").timestamp()
    os.utime(latest, (ts, ts))

    srv = inventory.get_server("elChapo01")
    runs = health.read_runs(srv)
    assert [r["start"] for r in runs][-4:] == [_at(day, t).timestamp() for t in
                                               ("00:00:08", "02:23:18", "12:17:00", "12:17:36")]
    assert runs[-1]["file"] == "latest.log"
    r = _check(srv, "BentoBox", "3.23.1", "BentoBox-3.23.1.jar")
    assert r["status"] == "healthy" and r["run_started"] == _at(day, "12:17:36").timestamp()

    # Spigot-style logs without the bootstrap line: a repeated "Starting minecraft server" opens a new run too
    for p in logs.iterdir():
        p.unlink()
    write_log(logs, day.replace(hour=9), [])  # (write_log always adds a bootstrap line: replace it below)
    latest = logs / "latest.log"
    latest.write_text("\n".join([L(T, "Starting minecraft server version 1.21.6", "09:00:00"),
                                 L(T, "[BentoBox] Enabling BentoBox v3.10.1", "09:00:10"),
                                 L(T, "Starting minecraft server version 1.21.6", "10:00:00"),
                                 L(T, "[BentoBox] Enabling BentoBox v3.23.1", "10:00:10"),
                                 L(T, "Done (10s)!", "10:00:20")]) + "\n")
    os.utime(latest, (day.replace(hour=11).timestamp(),) * 2)
    runs = health.read_runs(srv)
    assert [r["start"] for r in runs] == [_at(day, "09:00:00").timestamp(), _at(day, "10:00:00").timestamp()]


def _starts(logs, day, bodies):
    """One rotated log per earlier start (oldest first), each a clean start that stopped."""
    for i, body in enumerate(bodies):
        d = day - timedelta(days=len(bodies) - i)
        _log(logs, d, "00:00:08", body + [L(T, 'Done (60s)! For help, type "help"', "00:01:00"),
                                          L(T, "Stopping server", "23:00:00")], d.strftime("%Y-%m-%d-2.log.gz"))


def test_soft_error_seen_in_any_earlier_start_is_known_whatever_the_version(env):
    """M6 shape: DiscordSRV's IDENTIFY rate limit appeared four starts ago (older version), not in the last
    two: still a known issue, not a failure."""
    day, logs = _day(), env["src"].parent / "logs"
    ident = "[{t}] [JDA MainWS-ReadThread/ERROR]: [DiscordSRV] [JDA] Encountered IDENTIFY Rate Limit!"
    _starts(logs, day, [[L(T, "[DiscordSRV] Enabling DiscordSRV v1.30.4", "00:00:53"), ident.format(t="00:00:58")]]
            + [[L(T, "[DiscordSRV] Enabling DiscordSRV v1.30.4", "00:00:53")]] * 3)
    _log(logs, day, "12:17:02", [L(T, "[DiscordSRV] Enabling DiscordSRV v1.30.5", "12:18:02"),
                                 ident.format(t="12:18:10"), L(T, "Done (70s)!", "12:18:30")], "latest.log")
    r = _check(inventory.get_server("elChapo01"), "DiscordSRV", "1.30.5", "DiscordSRV-Build-1.30.5.jar")
    assert r["status"] == "healthy" and r["running"] is True and r["baseline_runs"] == 4
    assert [e["title"] for e in r["preexisting_errors"]] == ["[DiscordSRV] [JDA] Encountered IDENTIFY Rate Limit!"]


def test_new_soft_error_on_a_running_plugin_is_a_warning_not_a_failure(env):
    """M8 shape. Only a start-less fragment before (server up since before the oldest log): nothing to compare
    startup errors with, so they're listed as warnings. With an earlier start that lacked them: "warning"."""
    day, logs = _day(), env["src"].parent / "logs"
    fragment = logs / (day - timedelta(days=1)).strftime("%Y-%m-%d-1.log.gz")
    logs.mkdir(parents=True, exist_ok=True)
    with gzip.open(fragment, "wt") as f:
        f.write(L(T, "[Essentials] CONSOLE issued server command: /list", "10:00:00") + "\n")
    body = [L(T, "[Essentials] Enabling Essentials v2.22.0", "12:17:57"),
            L(E, "[Essentials] You are running an unsupported server version!", "12:17:58"),
            L(T, "[Plan] Enabling Plan v5.8 build 3638", "12:18:16"),
            "[12:18:18] [Plan Non critical-pool-3/ERROR]: [Plan] Failed to enable geolocation.",
            L(T, "Done (60s)!", "12:18:30")]
    _log(logs, day, "12:17:03", body, "latest.log")
    srv = inventory.get_server("elChapo01")
    for name, v in (("Essentials", "2.22.0"), ("Plan", "5.8 build 3638")):
        r = _check(srv, name, v, f"{name}.jar")
        assert r["status"] == "healthy" and r["running"] is True and r["warnings"], name

    _starts(logs, day, [[L(T, "[Essentials] Enabling Essentials v2.21.0", "00:00:50")]])
    r = _check(srv, "Essentials", "2.22.0", "Essentials.jar")
    assert (r["status"], r["running"]) == ("warning", True)


def test_not_running_counts_only_plugins_the_start_left_not_running(env):
    """elChapo01/M1 shapes: an unsupported API version and a missing dependency are the only "not running";
    a plugin with a new soft error is a warning, and the dashboard's failed list skips it."""
    day, logs, plugins = _day(), env["src"].parent / "logs", env["src"]
    make_jar(plugins / "RottenFlesh2Leather-1.1.jar", "RottenFlesh2Leather", "1.1.0")
    make_jar(plugins / "Voting-5.3.0.jar", "Voting", "5.3.0")
    make_jar(plugins / "Essentials.jar", "Essentials", "2.22.0")
    _starts(logs, day, [[L(T, "[Essentials] Enabling Essentials v2.21.0", "00:00:50"),
                         L(T, "[Vault] Enabling Vault v1.7.3", "00:00:50"),
                         L(T, "[CoreProtect] Enabling CoreProtect v24.1", "00:00:50")]])
    _log(logs, day, "12:16:56", [
        L(E, "[ModernPluginLoadingStrategy] Could not load 'plugins/.paper-remapped/Voting-5.3.0.jar' in "
             "'plugins/.paper-remapped'", "12:17:23"),
        "org.bukkit.plugin.UnknownDependencyException: Unknown/missing dependency plugins: [TheCore]. Please "
        "download and install these plugins to run 'Voting'.",
        L(E, "[ModernPluginLoadingStrategy] Could not load plugin 'RottenFlesh2Leather-1.1.jar' in folder "
             "'plugins/.paper-remapped'", "12:17:32"),
        "org.bukkit.plugin.InvalidPluginException: Unsupported API version 26.1",
        L(T, "[Vault] Enabling Vault v1.7.3", "12:17:40"),
        L(T, "[CoreProtect] Enabling CoreProtect v24.1", "12:17:40"),
        L(T, "[Essentials] Enabling Essentials v2.22.0", "12:17:57"),
        L(E, "[Essentials] You are running an unsupported server version!", "12:17:58"),
        L(T, "Done (110.492s)!", "12:18:45")], "latest.log")
    inventory.reset_cache()
    srv = inventory.get_server("elChapo01")
    report = health.server_report(srv, None)
    by = {p["name"]: p for p in report["plugins"]}
    assert (by["Voting"]["status"], by["Voting"]["running"], by["Voting"]["cause"]["kind"]) == \
        ("failed", False, "missing_dependency")
    assert (by["RottenFlesh2Leather"]["status"], by["RottenFlesh2Leather"]["cause"]["kind"]) == \
        ("failed", "unsupported_version")
    assert (by["Essentials"]["status"], by["Essentials"]["running"]) == ("warning", True)
    assert report["counts"]["failed"] == 2 and report["counts"]["warning"] == 1

    health.refresh_summaries()
    with client_for(app) as c:
        s = next(s for s in c.get("/api/v2/overview").json()["servers"] if s["id"] == "elChapo01")
    assert sorted(f["name"] for f in s["startup"]["failed"]) == ["RottenFlesh2Leather", "Voting"]
    assert all(f["running"] is False for f in s["startup"]["failed"])
    assert [w["name"] for w in s["startup"]["warnings"]] == ["Essentials"]


def test_new_soft_error_on_the_canary_still_holds_the_rollout(env):
    """"warning" is not "not running", but on the canary a new error after the update still holds it."""
    from app import scheduler
    logs = env["src"].parent / "logs"
    make_jar(env["src"] / "CoreProtect-24.1.jar", "CoreProtect", "24.1")
    now = datetime.now()
    before = (now - timedelta(days=2)).replace(hour=0, minute=0, second=10)
    after = now - timedelta(hours=1)
    write_log(logs, before, [L(T, "[CoreProtect] Enabling CoreProtect v23.1", before.strftime("%H:%M:%S")),
                             L(T, "Done (60s)!", before.strftime("%H:%M:%S"))],
              name=before.strftime("%Y-%m-%d-1.log.gz"))
    t = after.strftime("%H:%M:%S")
    write_log(logs, after, [L(T, "[CoreProtect] Enabling CoreProtect v24.1", t),
                            L(E, "[CoreProtect] Database schema upgrade failed", t), L(T, "Done (60s)!", t)])
    applied = (now - timedelta(hours=3)).timestamp()
    st = {"canary": {"bukkit:coreprotect|24.1": {"at": applied, "server": "elChapo01", "sha1": None}}}
    scheduler.check_canary_health(st, inventory.get_server("elChapo01"))
    assert st["canary"]["bukkit:coreprotect|24.1"]["health"]["status"] == "warning"
    assert "bukkit:coreprotect|24.1" in st["held"]
