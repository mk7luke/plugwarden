"""Server discovery, platform/MC version detection, jar metadata and safe path resolution."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import config
from .storage import read_json, write_json

SERVER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\- ]{0,79}$")
BUKKIT_PLATFORMS = {"purpur", "paper", "unknown"}
MC_VERSION_RE = re.compile(r"\(MC:\s*([0-9][0-9A-Za-z.\-]*)\)")


class PathError(ValueError):
    """Raised for unknown servers or unsafe paths. Mapped to HTTP 400/404 by the API layer."""


class UnknownServer(PathError):
    pass


@dataclass(frozen=True)
class Server:
    id: str
    root: Path
    plugins_dir: Path
    platform: str      # velocity | purpur | paper | fabric | unknown
    family: str        # velocity | bukkit | fabric
    mc_version: str | None


# ---------------------------------------------------------------- discovery

def detect_platform(mc_dir: Path) -> str:
    if (mc_dir / "velocity.toml").is_file() or any(mc_dir.glob("velocity-*.jar")):
        return "velocity"
    if (mc_dir / "purpur.jar").is_file():
        return "purpur"
    if (mc_dir / "paperclip.jar").is_file():
        return "paper"
    if (mc_dir / "fabric.jar").is_file():
        return "fabric"
    return "unknown"


def family_of(platform: str) -> str:
    if platform == "velocity":
        return "velocity"
    if platform == "fabric":
        return "fabric"
    return "bukkit"


def detect_mc_version(mc_dir: Path) -> str | None:
    data = read_json(mc_dir / "version_history.json", None)
    if isinstance(data, dict):
        m = MC_VERSION_RE.search(str(data.get("currentVersion", "")))
        if m:
            return m.group(1)
    return None


def discover() -> list[Server]:
    base = config.BASE
    if not base.is_dir():
        return []
    base_real = base.resolve()
    out: list[Server] = []
    for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if not SERVER_ID_RE.match(child.name) or not child.is_dir():
            continue
        plugins = child / config.REL_PLUGINS
        if not plugins.is_dir():
            continue
        # A symlinked instance or plugins dir must still live inside the base.
        if not _is_within(plugins.resolve(), base_real):
            continue
        mc_dir = child / "Minecraft"
        platform = detect_platform(mc_dir)
        out.append(Server(
            id=child.name, root=child, plugins_dir=plugins, platform=platform,
            family=family_of(platform),
            mc_version=None if platform == "velocity" else detect_mc_version(mc_dir),
        ))
    return out


def servers_by_id() -> dict[str, Server]:
    return {s.id: s for s in discover()}


def get_server(server_id: str) -> Server:
    if not isinstance(server_id, str) or not SERVER_ID_RE.match(server_id):
        raise UnknownServer(f"unknown server: {server_id!r}")
    srv = servers_by_id().get(server_id)
    if srv is None:
        raise UnknownServer(f"unknown server: {server_id!r}")
    return srv


# ---------------------------------------------------------------- path safety

def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def check_rel(rel: str, allow_empty: bool = False, single: bool = False) -> str:
    """Validate a client-supplied path relative to plugins/. Returns the normalized relative path."""
    if not isinstance(rel, str):
        raise PathError("path must be a string")
    if "\x00" in rel or "\\" in rel:
        raise PathError(f"invalid path: {rel!r}")
    if rel.startswith("/") or rel.startswith("~"):
        raise PathError(f"absolute paths are not allowed: {rel!r}")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise PathError(f"'..' is not allowed: {rel!r}")
    if not parts and not allow_empty:
        raise PathError("empty path")
    if single and len(parts) > 1:
        raise PathError(f"expected a single file or folder name: {rel!r}")
    return "/".join(parts)


def resolve_in(server: Server, rel: str, allow_empty: bool = False, single: bool = False) -> Path:
    """Join a relative path onto a server's plugins dir, refusing anything that escapes it (incl. symlinks)."""
    rel = check_rel(rel, allow_empty=allow_empty, single=single)
    root = server.plugins_dir.resolve()
    target = server.plugins_dir / rel if rel else server.plugins_dir
    try:
        real = target.resolve()
    except (OSError, RuntimeError) as e:  # symlink loops
        raise PathError(f"cannot resolve path {rel!r}: {e}")
    if not _is_within(real, root):
        raise PathError(f"path escapes plugins dir: {rel!r}")
    return root / rel if rel else root


# ---------------------------------------------------------------- jar metadata

_meta_lock = threading.Lock()
_meta_cache: dict[str, dict] | None = None
_meta_dirty = False


def _cache_path() -> Path:
    return config.state("cache", "jarmeta.json")


def _load_cache() -> dict[str, dict]:
    global _meta_cache
    if _meta_cache is None:
        _meta_cache = read_json(_cache_path(), {}) or {}
    return _meta_cache


def flush_cache() -> None:
    global _meta_dirty
    with _meta_lock:
        if _meta_dirty and _meta_cache is not None:
            for k in [k for k in _meta_cache if not Path(k).exists()]:
                del _meta_cache[k]
            write_json(_cache_path(), _meta_cache)
            _meta_dirty = False


def reset_cache() -> None:
    global _meta_cache, _meta_dirty
    with _meta_lock:
        _meta_cache = None
        _meta_dirty = False


def file_hash(path: Path, algo: str = "sha1") -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _yaml_fields(text: str) -> dict:
    try:
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            return data
    except yaml.YAMLError:
        pass
    # Fallback for descriptors with unparseable sections: top-level scalar lines only.
    out: dict[str, Any] = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z_-]+):\s*['\"]?([^'\"#]*?)['\"]?\s*$", line)
        if m and m.group(1) not in out:
            out[m.group(1)] = m.group(2)
    return out


def _desc(d: dict, kind: str) -> dict | None:
    name = d.get("name")
    if not name:
        return None
    authors = d.get("authors") or ([d["author"]] if d.get("author") else [])
    if not isinstance(authors, list):
        authors = [str(authors)]
    return {
        "kind": kind,
        "name": str(name),
        "id": str(d.get("id") or name),
        "version": None if d.get("version") is None else str(d.get("version")),
        "description": str(d.get("description") or "")[:500] or None,
        "website": str(d.get("website") or d.get("url") or "") or None,
        "authors": [str(a) for a in authors if a][:10],
        "api_version": None if d.get("api-version") is None else str(d.get("api-version")),
        "folia_supported": bool(d.get("folia-supported", False)),
    }


def read_descriptors(path: Path) -> dict[str, dict]:
    """Return {'bukkit': desc, 'velocity': desc, 'bungee': desc} for descriptors present in the jar.

    Raises zipfile.BadZipFile for non-zip files."""
    out: dict[str, dict] = {}
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())

        def text(n: str) -> str:
            info = zf.getinfo(n)
            if info.file_size > 2 * 1024 * 1024:
                return ""
            return zf.read(n).decode("utf-8", errors="replace")

        for fname in ("paper-plugin.yml", "plugin.yml"):
            if fname in names and "bukkit" not in out:
                d = _desc(_yaml_fields(text(fname)), fname)
                if d:
                    out["bukkit"] = d
        if "velocity-plugin.json" in names:
            try:
                d = _desc(json.loads(text("velocity-plugin.json")), "velocity-plugin.json")
                if d:
                    out["velocity"] = d
            except (json.JSONDecodeError, AttributeError):
                pass
        for fname in ("bungee.yml",):
            if fname in names:
                d = _desc(_yaml_fields(text(fname)), fname)
                if d:
                    out["bungee"] = d
    return out


def jar_meta(path: Path) -> dict:
    """sha1 + descriptors, cached by (path, size, mtime_ns)."""
    global _meta_dirty
    st = path.stat()
    ck = f"{st.st_size}:{st.st_mtime_ns}"
    key = str(path)
    with _meta_lock:
        cached = _load_cache().get(key)
        if cached and cached.get("ck") == ck:
            return cached
    sha1 = file_hash(path)
    try:
        desc = read_descriptors(path)
        valid = True
    except (zipfile.BadZipFile, OSError, KeyError):
        desc, valid = {}, False
    entry = {"ck": ck, "sha1": sha1, "valid_zip": valid, "descriptors": desc}
    with _meta_lock:
        _load_cache()[key] = entry
        _meta_dirty = True
    return entry


def norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


_FILE_VERSION_RE = re.compile(r"^(?P<name>.+?)[-_ ]v?(?P<ver>\d[\w.+\-]*)$")


def guess_from_filename(filename: str) -> tuple[str, str | None]:
    stem = filename[:-4] if filename.lower().endswith(".jar") else filename
    m = _FILE_VERSION_RE.match(stem)
    if m:
        return m.group("name"), m.group("ver")
    return stem, None


def plugin_key(family: str, name: str) -> str:
    return f"{family}:{norm_name(name) or 'unnamed'}"


def describe_jar(path: Path, family: str) -> dict:
    """Identity + version of a jar as seen by a server of the given family."""
    meta = jar_meta(path)
    descs = meta["descriptors"]
    desc = descs.get(family)
    if desc is not None:
        name, version = desc["name"], desc["version"]
        ident = desc["id"] if family == "velocity" else desc["name"]
    else:
        name, version = guess_from_filename(path.name)
        ident = name
    return {
        "key": plugin_key(family, ident),
        "name": name,
        "version": version,
        "sha1": meta["sha1"],
        "descriptor": desc,
        "valid_zip": meta["valid_zip"],
        "foreign": desc is None and bool(descs),  # has descriptors, but none for this platform
    }


def list_plugins(server: Server) -> list[dict]:
    """All top-level *.jar files in a server's plugins dir, with metadata."""
    pdir = server.plugins_dir
    if not pdir.is_dir() or server.family == "fabric":
        return []
    entries = sorted(pdir.iterdir(), key=lambda p: p.name.lower())
    dirs = {p.name.lower(): p.name for p in entries if p.is_dir() and not p.is_symlink()}
    out: list[dict] = []
    for p in entries:
        if not (p.is_file() and not p.is_symlink() and p.name.lower().endswith(".jar")):
            continue
        st = p.stat()
        info = describe_jar(p, server.family)
        desc = info["descriptor"] or {}
        folder = None
        for cand in (desc.get("name"), desc.get("id"), info["name"]):
            if cand and cand.lower() in dirs:
                folder = dirs[cand.lower()]
                break
        out.append({
            "key": info["key"],
            "name": info["name"],
            "jar": p.name,
            "version": info["version"],
            "sha1": info["sha1"],
            "folder": folder,
            "size": st.st_size,
            "mtime": int(st.st_mtime),
            "descriptor": desc.get("kind"),
            "website": desc.get("website"),
            "authors": desc.get("authors") or [],
            "valid": info["valid_zip"] and not info["foreign"],
        })
    keys: dict[str, int] = {}
    for pl in out:
        keys[pl["key"]] = keys.get(pl["key"], 0) + 1
    for pl in out:
        pl["duplicate"] = keys[pl["key"]] > 1
    return out


def list_tree(server: Server, rel: str) -> dict:
    target = resolve_in(server, rel, allow_empty=True)
    if not target.is_dir():
        raise PathError(f"not a directory: {rel!r}")
    root = server.plugins_dir.resolve()
    entries = []
    for p in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        try:
            st = p.lstat()
        except OSError:
            continue
        if p.is_symlink():
            kind = "symlink"
        elif p.is_dir():
            kind = "dir"
        else:
            kind = "file"
        rp = p.relative_to(root).as_posix()
        entry = {"name": p.name, "path": rp, "type": kind, "size": st.st_size if kind == "file" else None,
                 "mtime": int(st.st_mtime)}
        if kind == "file" and p.name.lower().endswith(".jar") and target == root:
            entry["jar"] = True
        entries.append(entry)
    rel_norm = check_rel(rel, allow_empty=True)
    return {"server": server.id, "path": rel_norm,
            "parent": None if not rel_norm else "/".join(rel_norm.split("/")[:-1]),
            "entries": entries}
