"""Server discovery, platform/MC version detection, jar metadata and safe path resolution."""
from __future__ import annotations

import difflib
import hashlib
import itertools
import json
import os
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


def _bad_name(name: str) -> bool:
    """Names with control characters could spoof listings/logs; they are never shown or used."""
    return bool(re.search(r"[\x00-\x1f\x7f\\]", name))


def check_rel(rel: str, allow_empty: bool = False, single: bool = False) -> str:
    """Validate a client-supplied path relative to plugins/. Returns the normalized relative path."""
    if not isinstance(rel, str):
        raise PathError("path must be a string")
    if "\\" in rel or re.search(r"[\x00-\x1f\x7f]", rel):
        raise PathError(f"invalid path (control characters or backslash): {rel!r}")
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


class _DescriptorLoader(yaml.SafeLoader):
    """SafeLoader without aliases (no billion-laughs expansion) and with bounded nesting depth."""
    MAX_DEPTH = 40

    def compose_node(self, parent, index):
        if self.check_event(yaml.events.AliasEvent):
            raise yaml.composer.ComposerError(None, None, "YAML aliases are not allowed in plugin descriptors")
        self._depth = getattr(self, "_depth", 0) + 1
        try:
            if self._depth > self.MAX_DEPTH:
                raise yaml.composer.ComposerError(None, None, "plugin descriptor nested too deeply")
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1


def _yaml_fields(text: str) -> dict:
    try:
        data = yaml.load(text, Loader=_DescriptorLoader)  # noqa: S506 - SafeLoader subclass
        if isinstance(data, dict):
            return data
    except Exception:  # noqa: BLE001 - any failure (incl. RecursionError) falls back to line scan
        pass
    # Fallback for descriptors with unparseable sections: top-level scalar lines only.
    out: dict[str, Any] = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z_-]+):\s*['\"]?([^'\"#]*?)['\"]?\s*$", line)
        if m and m.group(2)[:1] in ("*", "&"):
            continue  # alias/anchor: never expanded
        if m and m.group(1) not in out:
            out[m.group(1)] = m.group(2)
    return out


def _scalar(v: Any, limit: int = 200) -> str | None:
    """Only plain scalars become strings (never str() of a container)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (str, int, float)):
        s = str(v)[:limit]
        return s or None
    return None


def _desc(d: dict, kind: str) -> dict | None:
    if not isinstance(d, dict):
        return None
    name = _scalar(d.get("name"), 100)
    if not name:
        return None
    authors = d.get("authors") or ([d["author"]] if d.get("author") else [])
    if not isinstance(authors, list):
        authors = [authors]
    return {
        "kind": kind,
        "name": name,
        "id": _scalar(d.get("id"), 100) or name,
        "version": _scalar(d.get("version"), 100),
        "description": _scalar(d.get("description"), 500),
        "website": _scalar(d.get("website") or d.get("url"), 300),
        "authors": [a for a in (_scalar(x, 100) for x in authors[:10]) if a],
        "api_version": _scalar(d.get("api-version"), 20),
        "folia_supported": d.get("folia-supported") is True,
        "main": _scalar(d.get("main"), 200),
        "depends": _dep_names(d),
    }


def _dep_names(d: dict) -> list[str]:
    """depend + softdepend (plugin.yml), dependencies (paper-plugin.yml / velocity-plugin.json)."""
    out: list[str] = []
    for field in ("depend", "softdepend"):
        v = d.get(field)
        if isinstance(v, list):
            out += [s for s in (_scalar(x, 100) for x in v[:200]) if s]
        elif _scalar(v, 100):
            out.append(_scalar(v, 100))
    deps = d.get("dependencies")
    if isinstance(deps, list):  # velocity: [{"id": ...}]
        out += [s for s in (_scalar(x.get("id"), 100) for x in deps[:200] if isinstance(x, dict)) if s]
    elif isinstance(deps, dict):  # paper-plugin.yml: {server: {Name: {...}}}
        for group in list(deps.values())[:10]:
            if isinstance(group, dict):
                out += [s for s in (_scalar(k, 100) for k in list(group)[:200]) if s]
    return sorted(set(out))[:100]


MAX_CENTRAL_DIR = 32 * 1024 * 1024  # bytes of zip central directory we are willing to load
MAX_ZIP_ENTRIES = 100_000


def _check_central_directory(path: Path) -> None:
    """Refuse zips whose central directory is huge (millions of entries) before zipfile loads it.
    Checks the zip64 end record too, since zipfile trusts it over the classic one."""
    size = path.stat().st_size
    with open(path, "rb") as f:
        f.seek(max(0, size - 65557))
        tail_start = f.tell()
        tail = f.read()
        i = tail.rfind(b"PK\x05\x06")
        if i < 0 or len(tail) < i + 22:
            raise zipfile.BadZipFile("no end of central directory record")
        entries = int.from_bytes(tail[i + 10:i + 12], "little")
        cd_size = int.from_bytes(tail[i + 12:i + 16], "little")
        loc = i - 20
        if loc >= 0 and tail[loc:loc + 4] == b"PK\x06\x07":
            off = int.from_bytes(tail[loc + 8:loc + 16], "little")
            if off >= size:
                raise zipfile.BadZipFile("bad zip64 locator")
            f.seek(off)
            rec = f.read(56)
            if rec[:4] != b"PK\x06\x06" or len(rec) < 56:
                raise zipfile.BadZipFile("bad zip64 end record")
            entries = max(entries, int.from_bytes(rec[32:40], "little"))
            cd_size = max(cd_size, int.from_bytes(rec[40:48], "little"))
        elif entries == 0xFFFF or cd_size == 0xFFFFFFFF:
            raise zipfile.BadZipFile("zip64 sentinel without a zip64 record")
    del tail_start
    if cd_size > MAX_CENTRAL_DIR or entries > MAX_ZIP_ENTRIES:
        raise zipfile.BadZipFile("zip has too many entries")


def read_descriptors(path: Path) -> dict[str, dict]:
    """Return {'bukkit': desc, 'velocity': desc, 'bungee': desc} for descriptors present in the jar.

    Raises zipfile.BadZipFile for non-zip files."""
    out: dict[str, dict] = {}
    _check_central_directory(path)
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
            except Exception:  # noqa: BLE001 - malformed or hostile JSON (incl. RecursionError)
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
    ck = f"v4:{st.st_size}:{st.st_mtime_ns}"  # bump the prefix when descriptor fields change
    key = str(path)
    with _meta_lock:
        cached = _load_cache().get(key)
        if cached and cached.get("ck") == ck:
            return cached
    sha1 = file_hash(path)
    error = None
    try:
        desc = read_descriptors(path)
        valid = True
    except Exception as e:  # noqa: BLE001 - one hostile jar must never break listings; cached below
        desc, valid = {}, False
        error = f"unreadable descriptor ({type(e).__name__})"
    entry = {"ck": ck, "sha1": sha1, "valid_zip": valid, "descriptors": desc, "error": error}
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
        "error": meta.get("error"),
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
        if not (p.is_file() and not p.is_symlink() and p.name.lower().endswith(".jar")) or _bad_name(p.name):
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
            "depends": desc.get("depends") or [],
            "valid": info["valid_zip"] and not info["foreign"],
            "descriptor_error": info["error"],
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
        if _bad_name(p.name):
            continue
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


# ---------------------------------------------------------------- search / diff

# Player/world data folders: huge and never what someone deploys.
SEARCH_SKIP_DIRS = {"userdata", "playerdata", "players", "data", "logs", "cache", "backups", "backup",
                    "libs", "libraries", "database", "stats", "tiles", "web", "schematics", ".git"}
SEARCH_MAX_DEPTH = 6
SEARCH_MAX_VISITED = 50_000
SEARCH_MAX_DIR_ENTRIES = 2_000
DIFF_MAX_BYTES = 256 * 1024
DIFF_MAX_LINES = 8_000  # difflib is quadratic in the worst case


class NotFound(PathError):
    pass


def search(server: Server, q: str, limit: int = 200) -> dict:
    """Case-insensitive substring match on paths inside plugins/, breadth-first, bounded."""
    needle = q.strip().lower()
    if len(needle) < 2:
        raise PathError("search query must have at least 2 non-space characters")
    root = server.plugins_dir.resolve()
    results: list[dict] = []
    visited = 0
    truncated = False
    queue: list[tuple[Path, int]] = [(root, 0)]
    while queue and not truncated:
        d, depth = queue.pop(0)
        try:
            with os.scandir(d) as it:
                entries = list(itertools.islice(it, SEARCH_MAX_DIR_ENTRIES + 1))
        except OSError:
            continue
        if len(entries) > SEARCH_MAX_DIR_ENTRIES:
            continue  # huge data folder: never what gets deployed
        entries.sort(key=lambda e: e.name.lower())
        for e in entries:
            visited += 1
            if _bad_name(e.name):
                continue
            if visited > SEARCH_MAX_VISITED:
                truncated = True
                break
            rel = Path(e.path).relative_to(root).as_posix()
            is_dir = e.is_dir(follow_symlinks=False)
            if needle in rel.lower():
                try:
                    size = None if is_dir else e.stat(follow_symlinks=False).st_size
                except OSError:
                    size = None
                results.append({"path": rel, "name": e.name, "type": "dir" if is_dir else
                                ("symlink" if e.is_symlink() else "file"), "size": size})
                if len(results) >= limit:
                    truncated = True
                    break
            if is_dir and depth + 1 < SEARCH_MAX_DEPTH and e.name.lower() not in SEARCH_SKIP_DIRS:
                queue.append((Path(e.path), depth + 1))
    return {"server": server.id, "q": q, "results": results, "truncated": truncated}


def redact(lines: list[str], rel: str = "") -> tuple[list[str], dict[str, str]]:
    from .redact import redact_lines
    return redact_lines(lines, rel)


def diff_file(source: Server, target: Server, rel: str, transform=None) -> dict:
    """Unified diff of one text file between two servers (≤ 256 KB each).

    transform(src_lines, tgt_lines, out) may return replacement source lines (e.g. the merged file a
    preserve_keys push would write), so the diff shows exactly what the target will become."""
    src = resolve_in(source, rel)
    dst = resolve_in(target, rel)
    rel = check_rel(rel)
    out: dict[str, Any] = {"path": rel, "source": source.id, "target": target.id,
                           "source_exists": src.is_file(), "target_exists": dst.is_file(),
                           "identical": False, "binary": False, "too_large": False, "diff": "",
                           "redacted": [], "redacted_changed": False}
    if (src.exists() and not src.is_file()) or (dst.exists() and not dst.is_file()):
        raise PathError(f"not a file: {rel!r}")
    if not src.is_file() and not dst.is_file():
        raise NotFound(f"{rel} exists on neither server")
    texts = []
    for p in (src, dst):
        if not p.is_file():
            texts.append([])
            continue
        with open(p, "rb") as f:
            data = f.read(DIFF_MAX_BYTES + 1)  # bounded even if the file grows meanwhile
        if len(data) > DIFF_MAX_BYTES:
            out["too_large"] = True
            return out
        try:
            texts.append(data.decode("utf-8").splitlines(keepends=True))
        except UnicodeDecodeError:
            out["binary"] = True
            return out
        if b"\x00" in data:
            out["binary"] = True
            return out
        if len(texts[-1]) > DIFF_MAX_LINES:
            out["too_large"] = True
            return out
    if transform is not None and out["source_exists"] and out["target_exists"]:
        texts[0] = transform(texts[0], texts[1], out)
    (texts[0], secrets_src), (texts[1], secrets_dst) = redact(texts[0], rel), redact(texts[1], rel)
    out["redacted"] = [{"key": k, "changed": secrets_src.get(k) != secrets_dst.get(k)}
                       for k in sorted(set(secrets_src) | set(secrets_dst))]
    out["redacted_changed"] = any(r["changed"] for r in out["redacted"])
    out["identical"] = (texts[0] == texts[1] and out["source_exists"] == out["target_exists"]
                        and not out["redacted_changed"])
    # "what the target will become": target (a) → source (b)
    out["diff"] = "".join(difflib.unified_diff(texts[1], texts[0], fromfile=f"{target.id}/{rel}",
                                               tofile=f"{source.id}/{rel}"))
    return out
