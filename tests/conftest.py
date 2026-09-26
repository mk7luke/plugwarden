import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Never let an import fall back to production paths.
os.environ.setdefault("LGT_STATE_DIR", "/nonexistent-test-state")
os.environ.pop("LGT_BASE_OVERRIDE", None)

from app import config, inventory, updates  # noqa: E402


def make_jar(path: Path, name: str, version: str, kind: str = "bukkit", extra: bytes = b"") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if kind == "bukkit":
            zf.writestr("plugin.yml", f"name: {name}\nversion: {version}\nmain: x.Y\n")
        else:
            zf.writestr("velocity-plugin.json", json.dumps({"id": name.lower(), "name": name, "version": version}))
        zf.writestr("x/Y.class", b"\xca\xfe" + extra + version.encode())
    path.write_bytes(buf.getvalue())
    return path


def make_server(base: Path, sid: str, platform: str = "purpur", mc: str = "1.21.6") -> Path:
    mc_dir = base / sid / "Minecraft"
    (mc_dir / "plugins").mkdir(parents=True)
    marker = {"purpur": "purpur.jar", "paper": "paperclip.jar", "fabric": "fabric.jar",
              "velocity": "velocity.toml"}[platform]
    (mc_dir / marker).write_text("x")
    if platform not in ("velocity", "fabric"):
        (mc_dir / "version_history.json").write_text(json.dumps({"currentVersion": f"x (MC: {mc})"}))
    return mc_dir / "plugins"


@pytest.fixture()
def env(tmp_path):
    base = tmp_path / "base"
    state = tmp_path / "state"
    config.init(base=str(base), state_dir=str(state))
    inventory.reset_cache()
    updates.TRANSPORT = None

    src = make_server(base, "elChapo01")
    a = make_server(base, "M1-hub01")
    b = make_server(base, "M3-hunger01")
    proxy = make_server(base, "M0-proxy01", "velocity")
    make_server(base, "M9-homestead01", "fabric")

    make_jar(src / "CoreProtect-24.1.jar", "CoreProtect", "24.1")
    make_jar(a / "CoreProtect-23.1.jar", "CoreProtect", "23.1")
    make_jar(src / "Vault.jar", "Vault", "1.7.3")
    make_jar(a / "Vault.jar", "Vault", "1.7.0")
    make_jar(proxy / "LuckPerms-Velocity-5.5.jar", "LuckPerms", "5.5", kind="velocity")
    for d in (src, a):
        (d / "Essentials").mkdir()
    (src / "Essentials" / "config.yml").write_text("new-config\n")
    (src / "Essentials" / "messages").mkdir()
    (src / "Essentials" / "messages" / "en.yml").write_text("hello\n")
    (a / "Essentials" / "config.yml").write_text("old-config\n")
    (a / "Essentials" / "stale.yml").write_text("stale\n")
    (a / "Essentials" / "userdata").mkdir()
    (a / "Essentials" / "userdata" / "u.yml").write_text("user\n")
    yield {"base": base, "state": state, "src": src, "a": a, "b": b, "proxy": proxy, "tmp": tmp_path}
    updates.TRANSPORT = None


def snapshot_tree(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = p.read_bytes() if p.is_file() else "<dir>"
    return out
