"""Round 3: server-specific config keys (preserve/merge, needs_decision), replace = jars only,
data excluded from folder mirrors, deletion lists, installed_on, id-free summaries, check progress."""
import pytest
from fastapi.testclient import TestClient

from app import actions, configmerge, engine, jobs, plans, updates
from app.main import app
from conftest import client_for, make_jar, snapshot_tree
from test_engine import _mock_modrinth

# Shape of the real LuckPerms config.yml (comments, nested sections, lists, quoted values).
LP = """####################################################################################################
# +----------------------------------------------------------------------------------------------+ #
# |                                     LuckPerms Configuration                                  | #
####################################################################################################

# The name of the server, used for server specific permissions.
#
# - When set to "global" this setting is effectively ignored.
# - See: https://luckperms.net/wiki/Context
server: {server}

# If the servers own UUID cache/lookup facility should be used.
use-server-uuid-cache: false

storage-method: MariaDB

data:
  # Define the address and port for the database.
  address: localhost
  database: luckperms
  username: 'luckperms'
  password: '{password}'

  pool-settings:
    maximum-pool-size: 10   # connections
    minimum-idle: 10

  table-prefix: 'luckperms_'

sync-minutes: {sync}

# Worlds that should be treated as another world.
world-rewrite:
#  world_nether: world

group-weight:
#  default: 0

disabled-contexts:
  - "world"
  - "gamemode"
"""


def lp(server, password="pw-shared", sync="-1"):
    return LP.format(server=server, password=password, sync=sync)


def write_lp(env, **servers):
    for key, text in servers.items():
        d = env[key] / "LuckPerms"
        d.mkdir(exist_ok=True)
        (d / "config.yml").write_text(text)


@pytest.fixture()
def lp_env(env):
    write_lp(env, src=lp("survival", sync="5"), a=lp("hub"), b=lp("hungergames"))
    return env


def lp_plan(**extra):
    return engine.plan({"source": "elChapo01", "targets": ["M1-hub01", "M3-hunger01"], "action": "sync",
                        "items": {"paths": ["LuckPerms/config.yml"]}, **extra}, "t")


# ---------------------------------------------------------------- P0-A detection + decision

def test_luckperms_server_key_flagged_and_decision_required(lp_env):
    plan = lp_plan()
    ss = [w for w in plan["warnings"] if w["type"] == "server_specific"]
    assert [(w["server"], w["path"]) for w in ss] == [("M1-hub01", "LuckPerms/config.yml"),
                                                      ("M3-hunger01", "LuckPerms/config.yml")]
    assert ss[0]["keys"] == [{"key": "server", "source_value": "survival", "target_value": "hub", "reason": "name"}]
    assert plan["needs_decision"] is True
    with pytest.raises(plans.PlanError) as e:
        actions.start_deploy("t", plan["plan_id"])
    assert e.value.status == 409 and e.value.detail["code"] == "needs_decision"
    assert "server: hub" in (lp_env["a"] / "LuckPerms" / "config.yml").read_text()
    with client_for(app) as c:
        r = c.post("/api/v2/deploy", json={"plan_id": lp_plan()["plan_id"]})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "needs_decision"


def test_preserve_server_specific_merges_keeping_format(lp_env):
    plan = lp_plan(preserve_keys="server_specific")
    assert plan["needs_decision"] is False
    rows = {(r["server"], r["action"]): r for r in plan["results"]}
    assert rows[("M1-hub01", "merge")]["kept_keys"] == ["server"]
    before = snapshot_tree(lp_env["base"])
    job = jobs.wait(actions.start_deploy("t", plan["plan_id"]), 30)
    assert job.status == "done", job.log
    # Everything from the source (sync-minutes: 5, comments) except each server's own `server:` line.
    assert (lp_env["a"] / "LuckPerms" / "config.yml").read_text() == lp("hub", sync="5")
    assert (lp_env["b"] / "LuckPerms" / "config.yml").read_text() == lp("hungergames", sync="5")
    jobs.wait(actions.start_undo("t", job.id), 30)
    assert snapshot_tree(lp_env["base"]) == before


def test_overwrite_and_none_are_explicit_choices(lp_env):
    for extra in ({"overwrite_server_specific": True}, {"preserve_keys": "none"}):
        plan = lp_plan(**extra)
        assert plan["needs_decision"] is False and plan["warnings"]
    job = jobs.wait(actions.start_deploy("t", lp_plan(overwrite_server_specific=True)["plan_id"]), 30)
    assert job.status == "done"
    assert (lp_env["a"] / "LuckPerms" / "config.yml").read_text() == lp("survival", sync="5")


def test_secret_keys_redacted_in_warnings_and_preservable_by_path(lp_env):
    write_lp(lp_env, a=lp("hub", password="hub-secret"), b=lp("hungergames", password="hg-secret"))
    plan = lp_plan(preserve_keys={"LuckPerms/config.yml": ["server", "data.password"]})
    keys = {k["key"]: k for w in plan["warnings"] if w["server"] == "M1-hub01" for k in w["keys"]}
    assert keys["data.password"] == {"key": "data.password", "source_value": "«redacted»",
                                     "target_value": "«redacted»", "reason": "varies"}
    jobs.wait(actions.start_deploy("t", plan["plan_id"]), 30)
    text = (lp_env["a"] / "LuckPerms" / "config.yml").read_text()
    assert "password: 'hub-secret'" in text and "server: hub" in text and "sync-minutes: 5" in text


def test_merge_unsafe_refuses_row(lp_env):
    plan = lp_plan(preserve_keys=["disabled-contexts"])  # a list: not a single-line value
    rows = [r for r in plan["results"] if r["action"] == "merge"]
    assert rows and all(r["outcome"] == "error" and r["reason_code"] == "merge_unsafe" for r in rows)
    job = jobs.wait(actions.start_deploy("t", plan["plan_id"]), 30)
    assert "server: hub" in (lp_env["a"] / "LuckPerms" / "config.yml").read_text()  # untouched
    assert job.status == "failed"


def test_essentials_folder_mirror_keeps_server_name_and_data(env):
    for key, name in (("src", "survival"), ("a", "hub")):
        (env[key] / "Essentials" / "config.yml").write_text(
            f"# EssentialsX\nops-name-color: '4'\nserver-name: {name}\nteleport-cooldown: {5 if key == 'src' else 0}\n")
    plan = engine.plan({"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync",
                        "items": {"folders": ["Essentials"]}, "preserve_keys": "server_specific"}, "t")
    mirror = next(r for r in plan["results"] if r["action"] == "mirror")
    assert "stale.yml" in mirror["deletes"] and mirror["delete_count"] == 1 and mirror["delete_bytes"] > 0
    assert not any("config.yml" in c for c in mirror["changes"])  # handled by the merge row
    jobs.wait(actions.start_deploy("t", plan["plan_id"]), 30)
    ess = env["a"] / "Essentials"
    assert (ess / "config.yml").read_text() == "# EssentialsX\nops-name-color: '4'\nserver-name: hub\nteleport-cooldown: 5\n"
    assert (ess / "userdata" / "u.yml").read_text() == "user\n" and not (ess / "stale.yml").exists()


def test_configmerge_formats():
    props = ["# voicechat\n", "port=24454\n", "bind_address=\n", "max_voice_distance=48.0\n"]
    out, kept = configmerge.merge("x.properties", props, ["port=24455\n", "max_voice_distance=32.0\n"], ["port"])
    assert out == ["# voicechat\n", "port=24455\n", "bind_address=\n", "max_voice_distance=48.0\n"] and kept == ["port"]
    toml = ["[server]\n", 'name = "survival"  # shown in tab\n', "port = 25565\n"]
    out, _ = configmerge.merge("x.toml", toml, ["[server]\n", 'name = "hub"\n', "port = 25566\n"], ["server.name"])
    assert out[1] == 'name = "hub"  # shown in tab\n' and out[2] == "port = 25565\n"
    with pytest.raises(configmerge.MergeUnsafe):
        configmerge.merge("x.yml", ["a: &x 1\n", "b: *x\n"], ["a: 2\n", "b: 2\n"], ["a"])


# ---------------------------------------------------------------- P0-B

def test_replace_rejects_non_jars_422(env):
    with pytest.raises(engine.DeployInvalid):
        engine.plan({"source": "elChapo01", "targets": ["M1-hub01"], "action": "replace",
                     "items": {"folders": ["Essentials"]}})
    with client_for(app) as c:
        r = c.post("/api/v2/deploy/plan", json={"source": "elChapo01", "targets": ["M1-hub01"], "action": "replace",
                                                "items": {"paths": ["Essentials/config.yml"]}})
        assert r.status_code == 422 and "only works on .jar" in r.json()["detail"]


def test_folder_mirror_excludes_data_unless_opted_in(env):
    for key, content in (("src", b"SOURCE-DB"), ("a", b"LIVE-TARGET-DB")):
        d = env[key] / "CoreProtect"
        d.mkdir()
        (d / "database.db").write_bytes(content)
        (d / "config.yml").write_text("verbose: true\n")
    (env["a"] / "CoreProtect" / "logs").mkdir()
    (env["a"] / "CoreProtect" / "logs" / "x.log").write_text("log\n")
    body = {"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync", "items": {"folders": ["CoreProtect"]}}
    plan = engine.plan(body, "t")
    row = plan["results"][0]
    assert row["data_excluded"] is True and "deletes" not in row and row["outcome"] == "unchanged"
    jobs.wait(actions.start_deploy("t", plan["plan_id"]), 30)
    assert (env["a"] / "CoreProtect" / "database.db").read_bytes() == b"LIVE-TARGET-DB"
    assert (env["a"] / "CoreProtect" / "logs" / "x.log").exists()

    plan = engine.plan({**body, "options": {"include_data": True}}, "t")
    assert any(w["type"] == "include_data" for w in plan["warnings"])
    row = plan["results"][0]
    assert row["data_excluded"] is False and row["deletes"] == ["logs/x.log", "logs/"]
    jobs.wait(actions.start_deploy("t", plan["plan_id"]), 30)
    assert (env["a"] / "CoreProtect" / "database.db").read_bytes() == b"SOURCE-DB"


# ---------------------------------------------------------------- P1-B, summaries, progress

def test_installed_on_and_check_progress(env):
    new_bytes = make_jar(env["tmp"] / "dl" / "x.jar", "CoreProtect", "24.1", extra=b"p").read_bytes()
    _mock_modrinth(env, new_bytes)
    job = jobs.wait(actions.start_check("t"), 30)
    assert job.to_dict()["progress"]["done"] == job.to_dict()["progress"]["total"] >= 3
    with client_for(app) as c:
        rows = c.get("/api/v2/servers/M1-hub01/plugins").json()
    cp = next(r for r in rows if r["key"] == "bukkit:coreprotect")
    assert cp["installed_on"] == ["elChapo01", "M1-hub01"]


# ---------------------------------------------------------------- regressions from the round-3 review

@pytest.mark.parametrize("rel,src,tgt,keep", [
    # JSON: value followed by a brace / a second key on the same line
    ("c.json", '{\n  "server": "global",\n  "port": 1\n}\n', '{\n  "port": 1,\n  "server": "hub"}\n', ["server"]),
    ("c.json", '{\n  "server": "global"\n}\n', '{"server": "hub", "debug": true}\n', ["server"]),
    # TOML multi-line strings and .properties continuations must not be read as keys
    ("c.toml", "motd = '''\nserver = \"a\"\n'''\nserver = \"g\"\n", "motd = '''\nserver = \"b\"\n'''\nserver = \"h\"\n",
     ["server"]),
    # keys the user asked for but that can't be addressed line-wise
    ("c.yml", "s:\n  server: global\n", "s: {server: hub}\n", ["s.server"]),
    ("c.yml", "a:\n  x: 1\n", "a:\n  - server: hub\n", ["a.server"]),
    ("c.yml", "server: global\n", "server:\n  hub\n", ["server"]),
    ("c.conf", "server = global\n", "server = hub\n", ["server"]),
])
def test_merge_refuses_ambiguous_shapes(rel, src, tgt, keep):
    s, t = src.splitlines(keepends=True), tgt.splitlines(keepends=True)
    try:
        out, kept = configmerge.merge(rel, s, t, keep)
    except configmerge.MergeUnsafe:
        return
    # If it did merge, it must be exactly source + target's value (never extra or broken content).
    if rel.endswith(".toml"):
        assert "".join(out) == src.replace('server = "g"', 'server = "h"') and 'server = "a"' in "".join(out)
    else:
        pytest.fail(f"merged ambiguous input: {''.join(out)!r}")


def test_properties_continuation_not_rewritten():
    src = ["motd=a \\\n", "  server=global\n", "server=global\n"]
    tgt = ["motd=b \\\n", "  server=zzz\n", "server=hub\n"]
    out, kept = configmerge.merge("s.properties", src, tgt, ["server"])
    assert out == ["motd=a \\\n", "  server=global\n", "server=hub\n"] and kept == ["server"]


def test_comment_after_apostrophe_kept():
    out, _ = configmerge.merge("c.yml", ["server: bob's # src note\n"], ["server: tim's # tgt note\n"], ["server"])
    assert out == ["server: tim's # src note\n"]


def test_yaml_semantic_check_catches_colon_keys():
    src = ["ctx:\n", "  a:b:\n", "    server: global\n"]
    tgt = ["ctx:\n", "  server: hub\n"]
    with pytest.raises(configmerge.MergeUnsafe):
        configmerge.merge("c.yml", src, tgt, ["ctx.server", "ctx.a:b.server"])


def test_data_excludes_any_case_and_more_names(env):
    src, dst = env["src"] / "GP", env["a"] / "GP"
    for d in (src, dst):
        d.mkdir()
        (d / "config.yml").write_text("x: 1\n")
    for rel, content in (("PlayerData/u.yml", "t"), ("ClaimData/1.yml", "t"), ("Data/only.yml", "t"),
                         ("yaml-storage/users/u.yml", "t"), ("latest.log", "t"), ("usercache.json", "t"),
                         ("0f8fad5b-d9cb-469f-a165-70867728950e.yml", "t"), ("store.DB", "t")):
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        (dst / rel).write_text("TARGET")
    (src / "PlayerData").mkdir()
    (src / "PlayerData" / "u.yml").write_text("SOURCE")
    before = snapshot_tree(dst)
    jobs.wait(actions.start_deploy("t", engine.plan({"source": "elChapo01", "targets": ["M1-hub01"],
                                                     "action": "sync", "items": {"folders": ["GP"]}}, "t")["plan_id"]), 30)
    assert snapshot_tree(dst) == before


def test_needs_decision_per_file_and_rechecked_at_apply(lp_env):
    # a preserve spec for another file does not count as a decision for LuckPerms
    plan = lp_plan(preserve_keys={"OtherPlugin": "server_specific"})
    assert plan["needs_decision"] is True
    # clean plan, then a target gains a server-specific value before apply → 409
    write_lp(lp_env, a=lp("survival"), b=lp("survival"))
    plan = lp_plan()
    assert plan["needs_decision"] is False
    write_lp(lp_env, a=lp("hub"))
    with pytest.raises(plans.PlanError) as e:
        actions.start_deploy("t", plan["plan_id"])
    assert any(c.get("reason") == "needs_decision" for c in e.value.detail["conflicts"])


def test_target_only_identity_key_flagged(lp_env):
    write_lp(lp_env, a=lp("hub") + "server-name: hub\n")
    plan = lp_plan()
    keys = {k["key"]: k["reason"] for w in plan["warnings"] if w["server"] == "M1-hub01" for k in w["keys"]}
    assert keys == {"server": "name", "server-name": "target_only"}


def test_flow_map_line_merged_and_verified():
    src = ['Channels: {"global": "111"}\n', "Other: 1\n"]
    tgt = ['Channels: {"global": "222"}\n', "Other: 2\n"]
    out, kept = configmerge.merge("c.yml", src, tgt, ["Channels"])
    assert out == ['Channels: {"global": "222"}\n', "Other: 1\n"] and kept == ["Channels"]


def test_diff_with_preserve_shows_merged_result(lp_env):
    with client_for(app) as c:
        q = {"source": "elChapo01", "target": "M1-hub01", "path": "LuckPerms/config.yml"}
        raw = c.get("/api/v2/diff", params=q).json()
        assert "-server: hub" in raw["diff"] and "+server: survival" in raw["diff"]
        kept = c.get("/api/v2/diff", params={**q, "preserve_keys": "server_specific"}).json()
        assert kept["kept_keys"] == ["server"] and kept["merge_error"] is None
        assert "server:" not in kept["diff"] and "+sync-minutes: 5" in kept["diff"]
        bad = c.get("/api/v2/diff", params={**q, "preserve_keys": "disabled-contexts"}).json()
        assert bad["merge_error"] and "+server: survival" in bad["diff"]
