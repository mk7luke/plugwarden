"""setup.sh --dry-run against a fake AMP host: a temp datastore and a fake ampinstmgr. No docker, no sudo."""
import os
import pwd
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SEP = "│"
HEADER = ("[Info/1] AMP Instance Manager v2.8.0.6 build 20260923.2 built 23/09/2026 16:25\n"
          "[Info/1] Stream: Mainline / Release - built by CUBECODERS/buildbot on CCL-DEV\n")

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def block(name: str, module: str, path: str | None) -> str:
    rows = [("Instance ID", "6445673d-e57b-4732-971e-ad1d2be6c147"), ("Module", module),
            ("Instance Name", name), ("Friendly Name", name.lower()), ("URL", "http://127.0.0.1:8081/"),
            ("Running", "Yes"), ("AMP Version", "2.8.0.6")]
    if path:
        rows.append(("Data Path", path))
    return "".join(f"{k:<19}{SEP} {v}\n" for k, v in rows) + "\n"


def make_instance(root: Path, name: str, marker: str | None, plugins: bool = True) -> Path:
    mc = root / name / "Minecraft"
    mc.mkdir(parents=True)
    if plugins:
        (mc / "plugins").mkdir()
    if marker:
        (mc / marker).write_text("")
    return root / name


def fake_amp(tmp: Path, instances: list[tuple[str, str, str | None]], info_paths: dict | None = None) -> Path:
    """instances: (name, module, data path or None to omit it from -l). Returns the fake bin dir."""
    d = tmp / "fakeamp"
    d.mkdir()
    (d / "l.txt").write_text(HEADER + "".join(block(*i) for i in instances))
    table = f"{'Instance Name':<19}{SEP} {'Module':<10}\n"
    (d / "t.txt").write_text(HEADER + table)
    for name, module, path in instances:
        (d / f"i-{name}.txt").write_text(HEADER + block(name, module, path or (info_paths or {}).get(name)))
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    script = bin_dir / "ampinstmgr"
    script.write_text(f"""#!/usr/bin/env bash
case $1 in
  -l) cat '{d}/l.txt' ;;
  -t) cat '{d}/t.txt' ;;
  -i) cat '{d}/i-'"$2"'.txt' ;;
  *) exit 2 ;;
esac
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def run_setup(tmp: Path, *args: str, bin_dir: Path | None = None, env_extra: dict | None = None,
              existing_env: str | None = None) -> subprocess.CompletedProcess:
    work = tmp / "checkout"
    work.mkdir(exist_ok=True)
    shutil.copy(REPO / "setup.sh", work / "setup.sh")
    shutil.copy(REPO / ".env.example", work / ".env.example")
    if existing_env is not None:
        (work / ".env").write_text(existing_env)
    env = {k: v for k, v in os.environ.items() if not k.startswith("PLUGWARDEN_")}
    env.update(PLUGWARDEN_SETUP_NO_SUDO="1", NO_COLOR="1",
               PLUGWARDEN_SETUP_AMP_HOME=str(tmp / "amphome"))
    if bin_dir:
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["PLUGWARDEN_SETUP_AMPINSTMGR"] = str(bin_dir / "ampinstmgr")
    else:
        env["PLUGWARDEN_SETUP_AMPINSTMGR"] = ""
    env.update(env_extra or {})
    return subprocess.run(["bash", str(work / "setup.sh"), "--dry-run", "--yes",
                           "--amp-user", pwd.getpwuid(os.getuid()).pw_name, *args],
                          cwd=tmp, env=env, capture_output=True, text=True, timeout=60)


def env_of(out: str) -> dict:
    """The .env setup.sh would write, as {key: value} for active lines."""
    body = out.split("----- .env (would write, chmod 600) -----\n", 1)[1].split("  ----- end .env -----", 1)[0]
    return dict(line.split("=", 1) for line in body.splitlines() if line and not line.startswith("#"))


def ids():
    return str(os.getuid()), str(os.getgid())


@pytest.fixture
def network(tmp_path):
    ds = tmp_path / "ssd-live"
    make_instance(ds, "M0-proxy01", "velocity.toml")
    make_instance(ds, "M1-hub01", "purpur.jar")
    make_instance(ds, "M9-homestead01", "fabric.jar")
    make_instance(ds, "M2-vanilla01", None, plugins=False)
    (ds / "Enshrouded01").mkdir()
    ads = tmp_path / "amphome" / ".ampdata" / "instances" / "ADS01"
    ads.mkdir(parents=True)
    bin_dir = fake_amp(tmp_path, [
        ("ADS01", "ADS", str(ads)),
        ("M0-proxy01", "Minecraft", f"{ds}/M0-proxy01"),
        ("M1-hub01", "Minecraft", f"{ds}/M1-hub01"),
        ("M2-vanilla01", "Minecraft", f"{ds}/M2-vanilla01"),
        ("M9-homestead01", "Minecraft", None),       # only `-i` knows its Data Path
        ("Enshrouded01", "GenericModule", f"{ds}/Enshrouded01"),
    ], info_paths={"M9-homestead01": f"{ds}/M9-homestead01"})
    return ds, bin_dir


def test_local_mode_detects_datastore_and_writes_nothing(tmp_path, network):
    ds, bin_dir = network
    r = run_setup(tmp_path, "--mode", "local", bin_dir=bin_dir)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert f"Datastore    {ds}\n" in out
    assert "3 Minecraft server(s)" in out
    assert "M0-proxy01                   Velocity" in out
    assert "M1-hub01                     Purpur" in out
    assert "M9-homestead01               Fabric" in out
    assert "skipped M2-vanilla01: no Minecraft/plugins folder" in out
    assert "Enshrouded01" not in out and "ADS01" not in out
    uid, gid = ids()
    assert f"(uid {uid}, gid {gid})" in out
    env = env_of(out)
    assert env["AMP_DATASTORE"] == env["LGT_BASE_OVERRIDE"] == str(ds)
    assert (env["PLUGWARDEN_UID"], env["PLUGWARDEN_GID"]) == (uid, gid)
    assert env["LGT_AUTH"] == "none" and env["LGT_AUTH_ALLOW_INSECURE"] == "1"
    for key in ("LGT_HOSTNAME", "LGT_CF_AUD", "LGT_CF_TEAM_DOMAIN", "LGT_CONTACT"):
        assert key not in env
    checkout = tmp_path / "checkout"
    assert not (checkout / ".env").exists() and not (checkout / "data").exists()
    assert "Nothing was changed" in out


def test_cf_mode_env(tmp_path, network):
    ds, bin_dir = network
    aud = "a" * 64
    r = run_setup(tmp_path, "--mode", "cf", "--cf-team", "https://myteam/", "--cf-aud", aud,
                  "--hostname", "Plugins.Example.org", "--contact", "ops@example.org", "--skip-cf-check",
                  bin_dir=bin_dir)
    assert r.returncode == 0, r.stdout + r.stderr
    env = env_of(r.stdout)
    assert env["LGT_AUTH"] == "cf-access"
    assert env["LGT_CF_TEAM_DOMAIN"] == "myteam.cloudflareaccess.com"
    assert env["LGT_CF_AUD"] == aud
    assert env["LGT_HOSTNAME"] == "plugins.example.org"
    assert env["LGT_CONTACT"] == "ops@example.org"
    assert "LGT_AUTH_ALLOW_INSECURE" not in env


def test_cf_mode_needs_values_when_non_interactive(tmp_path, network):
    _, bin_dir = network
    r = run_setup(tmp_path, "--mode", "cf", "--cf-team", "myteam.cloudflareaccess.com", "--skip-cf-check",
                  bin_dir=bin_dir)
    assert r.returncode != 0
    assert "--cf-aud is missing" in r.stderr


def test_mode_is_required_without_existing_env(tmp_path, network):
    _, bin_dir = network
    r = run_setup(tmp_path, bin_dir=bin_dir)
    assert r.returncode != 0
    assert "--mode local" in r.stderr


def test_existing_env_is_kept_and_diffed(tmp_path, network):
    ds, bin_dir = network
    uid, gid = ids()
    old = (f"AMP_DATASTORE=/old\nPLUGWARDEN_UID={uid}\nPLUGWARDEN_GID={gid}\nLGT_BASE_OVERRIDE=/old\n"
           "LGT_AUTH=none\nLGT_AUTH_ALLOW_INSECURE=1\nLGT_MIN_FREE_GB=5\n")
    r = run_setup(tmp_path, bin_dir=bin_dir, existing_env=old)   # mode comes from the existing .env
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"-AMP_DATASTORE=/old\n+AMP_DATASTORE={ds}" in r.stdout
    env = env_of(r.stdout)
    assert env["LGT_MIN_FREE_GB"] == "5" and env["LGT_AUTH"] == "none"
    assert (tmp_path / "checkout" / ".env").read_text() == old       # dry run: untouched

    new = r.stdout.split("----- .env (would write, chmod 600) -----\n", 1)[1].split("  ----- end .env -----")[0]
    r2 = run_setup(tmp_path, bin_dir=bin_dir, existing_env=new)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert ".env is already up to date" in r2.stdout


def test_multiple_datastores_picks_the_biggest(tmp_path):
    big, small = tmp_path / "big", tmp_path / "small"
    for n in ("S1", "S2", "S3"):
        make_instance(big, n, "purpur.jar")
    make_instance(small, "Lonely01", "paperclip.jar")
    bin_dir = fake_amp(tmp_path, [("Lonely01", "Minecraft", f"{small}/Lonely01")] +
                       [(n, "Minecraft", f"{big}/{n}") for n in ("S1", "S2", "S3")])
    r = run_setup(tmp_path, "--mode", "local", bin_dir=bin_dir)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"Datastore    {big}\n" in r.stdout and "3 Minecraft server(s)" in r.stdout
    assert f"{small} (1 server(s))" in r.stderr and "--datastore" in r.stderr
    assert env_of(r.stdout)["AMP_DATASTORE"] == str(big)

    r = run_setup(tmp_path, "--mode", "local", "--datastore", f"{small}/", bin_dir=bin_dir)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 Minecraft server(s)" in r.stdout and "Lonely01                     Paper" in r.stdout
    assert env_of(r.stdout)["AMP_DATASTORE"] == str(small)


def test_fallback_scan_without_ampinstmgr(tmp_path):
    default = tmp_path / "amphome" / ".ampdata" / "instances"
    make_instance(default, "Hub01", "velocity-3.4.0-558.jar")
    make_instance(default, "Survival01", "purpur.jar")
    (default / "ADS01").mkdir()
    r = run_setup(tmp_path, "--mode", "local")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ampinstmgr not found" in r.stdout
    assert f"Datastore    {default}\n" in r.stdout and "2 Minecraft server(s)" in r.stdout
    assert "Hub01                        Velocity" in r.stdout

    custom = tmp_path / "custom"
    make_instance(custom, "Only01", None)
    r = run_setup(tmp_path, "--mode", "local", "--datastore", str(custom))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Only01                       unknown" in r.stdout
    assert env_of(r.stdout)["LGT_BASE_OVERRIDE"] == str(custom)


def test_no_instances_is_a_clear_error(tmp_path):
    r = run_setup(tmp_path, "--mode", "local", "--datastore", str(tmp_path / "empty"))
    assert r.returncode != 0
    assert "no Minecraft servers" in r.stderr and "--datastore" in r.stderr


def test_help_and_bad_flags(tmp_path):
    r = run_setup(tmp_path, "--help")
    assert r.returncode == 0 and "--dry-run" in r.stdout
    r = run_setup(tmp_path, "--mode", "public")
    assert r.returncode != 0 and "--mode must be" in r.stderr



def test_table_fallback_when_list_has_no_blocks(tmp_path):
    ds = tmp_path / "ds"
    make_instance(ds, "Hub01", "purpur.jar")
    make_instance(ds, "Lobby01", "purpur.jar")
    bin_dir = fake_amp(tmp_path, [("Hub01", "Minecraft", f"{ds}/Hub01"), ("Lobby01", "Minecraft", f"{ds}/Lobby01")])
    fake = tmp_path / "fakeamp"
    (fake / "l.txt").write_text(HEADER)   # older ampinstmgr: -l prints nothing useful
    rule = "─" * 19 + "┼" + "─" * 18 + "┼" + "─" * 12
    (fake / "t.txt").write_text(HEADER + f"{'Instance Name':<19}{SEP} {'Friendly Name':<17}{SEP} Module\n{rule}\n"
                                f"{'Hub01':<19}{SEP} {'hub':<17}{SEP} Minecraft\n"
                                f"{'Lobby01':<19}{SEP} {'lobby':<17}{SEP} Minecraft\n"
                                f"{'Ghost01':<19}{SEP} {'gone':<17}{SEP} Minecraft\n"      # -i fails for this one
                                f"{'Enshrouded01':<19}{SEP} {'e':<17}{SEP} GenericModule\n")
    r = run_setup(tmp_path, "--mode", "local", bin_dir=bin_dir)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"Datastore    {ds}\n" in r.stdout and "2 Minecraft server(s)" in r.stdout
