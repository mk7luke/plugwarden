"""File operations on servers: sync / install / replace jar / delete, dry-run plans, backups and undo.

Semantics follow the original amp-plugin-sync script:
  * jar/file  : copied only if already present on the target, unless install
  * folder    : rsync -a --delete mirror; only if present on the target, unless install
  * path      : file or subfolder inside plugins/, rsync without --delete
  * delete    : rm -rf of the item on the target (here: moved into the job's backup)
Every real change is backed up to STATE_DIR/backups/<job_id>/ first, so jobs can be undone.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from . import config, inventory, plans
from .inventory import PathError, Server
from .storage import read_json, write_json

ACTIONS = ("sync", "install", "replace", "delete")
MAX_CHANGE_LINES = 200


class DeployError(ValueError):
    pass


# ---------------------------------------------------------------- rsync

def rsync(src: str, dest: str, *, dry_run: bool, delete: bool = False, mkpath: bool = False) -> tuple[int, list[str], str]:
    # --checksum: size+mtime quick-check misses same-size config edits made within the same second.
    # --safe-links: never copy source symlinks that point outside the copied tree.
    cmd = ["rsync", "-a", "--itemize-changes", "--checksum", "--safe-links"]
    if dry_run:
        cmd.append("--dry-run")
    if delete:
        cmd.append("--delete")
    if mkpath:
        cmd.append("--mkpath")
    if not (src.startswith("/") and dest.startswith("/")):
        raise ValueError("rsync paths must be absolute")
    cmd += [src, dest]  # absolute paths can never be read as options or host:path
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    # Lines starting with "." are attribute-only updates (no content transferred); ignore them.
    changes = [ln for ln in proc.stdout.splitlines()
               if ln and not ln.startswith("created director") and not ln.startswith(".")]
    return proc.returncode, changes, proc.stderr.strip()


def _count_files(path: Path) -> int:
    if path.is_file() or path.is_symlink():
        return 1
    return sum(len(files) for _, _, files in os.walk(path))


# ---------------------------------------------------------------- backups

class BackupError(OSError):
    pass


class Backup:
    """Per-job backup store. Each entry records a plugins-relative path and whether it existed before
    the job touched it; undo (in reverse order) removes whatever is there now and restores the saved
    copy (if any). Every entry gets its own store directory, so overlapping paths never collide."""

    def __init__(self, job_id: str, on_record=None):
        self.job_id = job_id
        self.root = config.state("backups", job_id)
        self.manifest_path = self.root / "manifest.json"
        self.entries: list[dict] = (read_json(self.manifest_path, {}) or {}).get("entries", [])
        self._lock = threading.Lock()
        self._on_record = on_record

    def _covered(self, server: str, rel: str) -> bool:
        return any(e["server"] == server and (rel == e["rel"] or rel.startswith(e["rel"] + "/"))
                   for e in self.entries)

    def store_path(self, entry: dict) -> Path:
        if entry.get("store"):
            return self.root / entry["store"]
        return self.root / "files" / entry["server"] / entry["rel"]

    def stored(self, server: str, rel: str) -> Path | None:
        for e in self.entries:
            if e["server"] == server and e["rel"] == rel and e["existed"]:
                return self.store_path(e)
        return None

    def _record(self, entry: dict) -> None:
        self.entries.append(entry)
        write_json(self.manifest_path, {"job_id": self.job_id, "entries": self.entries})
        if self._on_record:
            self._on_record(entry)

    def save(self, srv: Server, rel: str, move: bool = False) -> None:
        """Back up plugins/<rel> before it is modified (copy) or removed (move)."""
        path = srv.plugins_dir / rel
        with self._lock:
            if self._covered(srv.id, rel):
                if move and _exists(path):
                    _remove(path)
                return
            exists = _exists(path)
            if not exists:
                # Record the top-most missing ancestor so undo also removes directories we create.
                parts = rel.split("/")
                for i in range(1, len(parts)):
                    if not (srv.plugins_dir / "/".join(parts[:i])).exists():
                        rel = "/".join(parts[:i])
                        break
                if self._covered(srv.id, rel):
                    return
                return self._record({"server": srv.id, "rel": rel, "existed": False, "type": None})
            entry = {"server": srv.id, "rel": rel, "existed": True,
                     "type": "dir" if path.is_dir() and not path.is_symlink() else "file",
                     "store": f"files/{len(self.entries)}/{srv.id}/{rel}"}
            dst = self.store_path(entry)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if _exists(dst):
                raise BackupError(f"backup destination already exists: {dst}")
            self._check_space(path, dst, move)
            if move and os.stat(path, follow_symlinks=False).st_dev == os.stat(dst.parent).st_dev:
                os.rename(path, dst)
            elif move:
                _copy(path, dst)
                _remove(path)
            else:
                _copy(path, dst)
            self._record(entry)

    @staticmethod
    def _check_space(path: Path, dst: Path, move: bool) -> None:
        if move and os.stat(path, follow_symlinks=False).st_dev == os.stat(dst.parent).st_dev:
            return
        need = _tree_size(path)
        free = shutil.disk_usage(dst.parent).free
        if free < need + 256 * 1024 * 1024:
            raise BackupError(f"not enough free space in state dir for backup ({need >> 20} MiB needed)")

    @property
    def used(self) -> bool:
        return bool(self.entries)


def _tree_size(path: Path) -> int:
    if not path.is_dir() or path.is_symlink():
        return os.stat(path, follow_symlinks=False).st_size
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.stat(os.path.join(root, f), follow_symlinks=False).st_size
            except OSError:
                pass
    return total


def _copy(src: Path, dst: Path) -> None:
    subprocess.run(["cp", "-a", "--", str(src), str(dst)], check=True, capture_output=True, text=True)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def prune_backups(keep: int, keep_ids: set[str] = frozenset()) -> None:
    dirs = sorted((d for d in config.state("backups").iterdir() if d.is_dir()), reverse=True)
    for d in dirs[keep:]:
        if d.name not in keep_ids:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- context

class Ctx:
    """Collects results for a plan or a job. `job` is None for synchronous plans."""

    def __init__(self, dry_run: bool, job=None, backup: Backup | None = None):
        self.dry_run = dry_run
        self.job = job
        self.backup = backup
        self.results: list[dict] = []
        self.warnings: list[dict] = []
        self.fps: dict[tuple, str] = {}  # (server, item, action) -> digest of the full planned change list

    def remember(self, server: str, item: str, action: str, lines: list[str]) -> None:
        self.fps[(server, item, action)] = hashlib.sha1("\n".join(lines).encode()).hexdigest()

    def log(self, line: str) -> None:
        if self.job:
            self.job.write(line)

    def result(self, server: str, item: str, action: str, outcome: str, detail: str = "",
               changes: list[str] | None = None, **fields) -> None:
        extra: dict[str, Any] = dict(fields)
        code = extra.pop("reason_code", None) or _reason_code(outcome, detail)
        if code:
            extra["reason_code"] = code
        if changes is not None:
            extra["changes"] = changes[:MAX_CHANGE_LINES]
            extra["change_count"] = len(changes)
        if self.job:
            self.job.add_result(server, item, action, outcome, detail, **extra)
            self.log(f"  [{outcome}] {item}: {detail}")
            for ln in (changes or [])[:MAX_CHANGE_LINES]:
                self.log(f"      {ln}")
        self.results.append({"server": server, "item": item, "action": action, "outcome": outcome,
                             "detail": detail, **extra})

    def guard(self, server: str, item: str, action: str, fn, /, *args, **kwargs) -> None:
        """Run one item operation; an unexpected failure becomes an error result instead of aborting the job."""
        try:
            fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - per-item boundary
            self.result(server, item, action, "error", f"{type(e).__name__}: {e}")


_REASONS = (("missing on source", "missing_on_source"), ("not on target", "not_installed"),
            ("not installed", "not_installed"), ("shared with", "shared_folder"), ("not present", "not_present"),
            ("no update available", "no_update"), ("not outdated", "no_update"),
            ("source provides no hash", "unverified"), ("changed since", "changed_since_plan"))


def _reason_code(outcome: str, detail: str) -> str | None:
    """Machine-readable reason for non-change rows (the UI keys copy off it)."""
    if outcome == "unchanged":
        return "identical"
    if outcome not in ("skipped", "error"):
        return None
    for needle, code in _REASONS:
        if needle in detail:
            return code
    return None if outcome == "error" else "other"


# ---------------------------------------------------------------- operations

def _exists(p: Path) -> bool:
    return p.exists() or p.is_symlink()


def sync_file(ctx: Ctx, src_srv: Server, tgt: Server, name: str, install: bool) -> None:
    item = name
    try:
        src = inventory.resolve_in(src_srv, name, single=True)
        dest = inventory.resolve_in(tgt, name, single=True)
    except PathError as e:
        return ctx.result(tgt.id, item, "sync", "error", str(e))
    if not src.is_file() or src.is_symlink():
        return ctx.result(tgt.id, item, "sync", "skipped", "missing on source")
    if _exists(dest) and (dest.is_dir() or dest.is_symlink()):
        return ctx.result(tgt.id, item, "sync", "error", "target exists and is not a regular file")
    if not install and not dest.exists():
        return ctx.result(tgt.id, item, "sync", "skipped", "not on target (install is off)")
    _rsync_item(ctx, tgt, item, "install" if not dest.exists() else "sync", str(src), str(dest),
                rel=name)


def sync_folder(ctx: Ctx, src_srv: Server, tgt: Server, name: str, install: bool) -> None:
    item = name + "/"
    try:
        src = inventory.resolve_in(src_srv, name, single=True)
        dest = inventory.resolve_in(tgt, name, single=True)
    except PathError as e:
        return ctx.result(tgt.id, item, "mirror", "error", str(e))
    if not src.is_dir() or src.is_symlink():
        return ctx.result(tgt.id, item, "mirror", "skipped", "missing on source")
    if _exists(dest) and (not dest.is_dir() or dest.is_symlink()):
        return ctx.result(tgt.id, item, "mirror", "error", "target exists and is not a folder")
    if not install and not dest.is_dir():
        return ctx.result(tgt.id, item, "mirror", "skipped", "not on target (install is off)")
    _rsync_item(ctx, tgt, item, "mirror", str(src) + "/", str(dest) + "/", rel=name, delete=True)


def sync_path(ctx: Ctx, src_srv: Server, tgt: Server, rel: str, install: bool) -> None:
    try:
        rel = inventory.check_rel(rel)
        src = inventory.resolve_in(src_srv, rel)
        dest = inventory.resolve_in(tgt, rel)
    except PathError as e:
        return ctx.result(tgt.id, str(rel), "sync", "error", str(e))
    if src.is_symlink() or not _exists(src):
        return ctx.result(tgt.id, rel, "sync", "skipped", "missing on source")
    if not install and not _exists(dest):
        return ctx.result(tgt.id, rel, "sync", "skipped", "not on target (install is off)")
    if dest.is_symlink() or (_exists(dest) and dest.is_dir() != src.is_dir()):
        return ctx.result(tgt.id, rel, "sync", "error", "target exists with a different type")
    if src.is_dir():
        _rsync_item(ctx, tgt, rel + "/", "sync", str(src) + "/", str(dest) + "/", rel=rel)
    else:
        _rsync_item(ctx, tgt, rel, "sync", str(src), str(dest), rel=rel)


def _rsync_item(ctx: Ctx, tgt: Server, item: str, action: str, src: str, dest: str, *, rel: str,
                delete: bool = False) -> None:
    rc, changes, err = rsync(src, dest, dry_run=True, delete=delete, mkpath=True)
    if rc != 0:
        return ctx.result(tgt.id, item, action, "error", err or f"rsync exited {rc}")
    if not changes:
        return ctx.result(tgt.id, item, action, "unchanged", "already up to date")
    if ctx.dry_run:
        # A mirror of a live folder: the server keeps writing files, so only promise the deletions.
        ctx.remember(tgt.id, item, action, [ln for ln in changes if ln.startswith("*deleting")] if delete else changes)
        return ctx.result(tgt.id, item, action, "changed", f"{len(changes)} change(s)", changes)
    if ctx.backup:
        ctx.backup.save(tgt, rel)
    rc, done, err = rsync(src, dest, dry_run=False, delete=delete, mkpath=True)
    if rc != 0:
        return ctx.result(tgt.id, item, action, "error", err or f"rsync exited {rc}", done)
    ctx.result(tgt.id, item, action, "changed", f"{len(done)} change(s)", done)


def delete_item(ctx: Ctx, tgt: Server, rel: str) -> None:
    try:
        rel = inventory.check_rel(rel)
        path = inventory.resolve_in(tgt, rel)
    except PathError as e:
        return ctx.result(tgt.id, str(rel), "delete", "error", str(e))
    if not _exists(path):
        return ctx.result(tgt.id, rel, "delete", "skipped", "not present")
    n, size = _count_files(path), _tree_size(path)
    if ctx.dry_run:
        return ctx.result(tgt.id, rel, "delete", "changed", f"would delete ({n} file(s))", [f"*deleting {rel}"],
                          files=n, size=size)
    if ctx.backup:
        ctx.backup.save(tgt, rel, move=True)
    else:
        _remove(path)
    ctx.result(tgt.id, rel, "delete", "changed", f"deleted ({n} file(s))", [f"*deleting {rel}"],
               files=n, size=size)


def folder_sharers(tgt: Server, folder: str, removing_jars: set[str]) -> list[str]:
    """Names of plugins that stay installed on tgt and still use plugins/<folder>.

    A plugin uses the folder if it is its data folder, or if it depends on a plugin named like the
    folder and its own name starts with that name (EssentialsChat/EssentialsSpawn → Essentials)."""
    f = inventory.norm_name(folder)
    out = []
    for p in inventory.list_plugins(tgt):
        if p["jar"] in removing_jars:
            continue
        owns = p["folder"] is not None and p["folder"].lower() == folder.lower()
        addon = (any(inventory.norm_name(d) == f for d in p.get("depends") or [])
                 and inventory.norm_name(p["name"]).startswith(f))
        if owns or addon:
            out.append(p["name"])
    return sorted(set(out), key=str.lower)


def delete_folder_checked(ctx: Ctx, tgt: Server, folder: str, removing_jars: set[str], force: bool) -> None:
    """Delete a top-level folder unless another installed plugin still uses it (override with force)."""
    try:
        path = inventory.resolve_in(tgt, folder, single=True)
    except PathError as e:
        return ctx.result(tgt.id, folder, "delete", "error", str(e))
    if not path.is_dir() or path.is_symlink():
        return delete_item(ctx, tgt, folder)
    shared = folder_sharers(tgt, folder, removing_jars)
    if shared:
        ctx.warnings.append({"server": tgt.id, "folder": folder, "shared_with": shared})
        if not force:
            return ctx.result(tgt.id, folder, "delete", "skipped",
                              f"shared with {', '.join(shared)} — pass force:true", shared_with=shared,
                              files=_count_files(path), size=_tree_size(path))
        ctx.log(f"  warning: {folder} is shared with {', '.join(shared)}; deleting anyway (force)")
    delete_item(ctx, tgt, folder)


def replace_jar(ctx: Ctx, tgt: Server, new_jar: Path, install: bool, label: str | None = None,
                expect_key: str | None = None, action: str = "replace") -> None:
    """Install new_jar on tgt, removing every other jar of the same plugin (by key).

    Order: back up old jars → atomically place the new jar → remove old jars with other names.
    The server is never left with zero or two versions of the plugin."""
    item = label or new_jar.name
    try:
        inventory.check_rel(new_jar.name, single=True)
    except PathError as e:
        return ctx.result(tgt.id, item, action, "error", str(e))
    info = inventory.describe_jar(new_jar, tgt.family)
    if not info["valid_zip"] or info["descriptor"] is None:
        return ctx.result(tgt.id, item, action, "error",
                          f"jar has no {tgt.family} plugin descriptor; not installable on {tgt.platform}")
    key = info["key"]
    if expect_key and key != expect_key:
        return ctx.result(tgt.id, item, action, "error", f"jar identifies as {key}, expected {expect_key}")
    existing = [p for p in inventory.list_plugins(tgt) if p["key"] == key]
    dest = tgt.plugins_dir / new_jar.name
    if _exists(dest) and not any(p["jar"] == new_jar.name for p in existing):
        return ctx.result(tgt.id, item, action, "error",
                          f"{new_jar.name} already exists on target but is a different plugin")
    if not existing and not install:
        return ctx.result(tgt.id, item, action, "skipped", f"{info['name']} not installed (install is off)")
    if len(existing) == 1 and existing[0]["jar"] == new_jar.name and existing[0]["sha1"] == info["sha1"]:
        return ctx.result(tgt.id, item, action, "unchanged", f"{new_jar.name} already installed")
    old_names = [p["jar"] for p in existing]
    changes = [f"*deleting {n}" for n in old_names if n != new_jar.name]
    changes.append(f">f{'.' if new_jar.name in old_names else '+'} {new_jar.name}")
    olds = ", ".join(f"{p['jar']} ({p['version']})" for p in existing) or "none"
    detail = f"{olds} → {new_jar.name} ({info['version']})"
    jar_fields = {"old_jars": old_names, "new_jar": new_jar.name, "old_versions": [p["version"] for p in existing],
                  "new_version": info["version"]}
    if ctx.dry_run:
        ctx.remember(tgt.id, item, action, changes + sorted(p["jar"] + ":" + p["sha1"] for p in existing))
        return ctx.result(tgt.id, item, action, "changed", detail, changes, **jar_fields)

    for n in old_names:
        if ctx.backup:
            ctx.backup.save(tgt, n)
    if ctx.backup and new_jar.name not in old_names:
        ctx.backup.save(tgt, new_jar.name)  # records "did not exist" so undo removes it
    tmp = tgt.plugins_dir / f".{new_jar.name}.lgt-tmp"
    try:
        shutil.copy2(new_jar, tmp)
        os.replace(tmp, dest)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        return ctx.result(tgt.id, item, action, "error", f"could not write new jar: {e}")
    removed = []
    try:
        for n in old_names:
            if n != new_jar.name:
                (tgt.plugins_dir / n).unlink()
                removed.append(n)
    except OSError as e:
        # Roll back to exactly the previous jars: restore removed/overwritten ones, drop a new name.
        detail = f"could not remove old jar: {e}"
        try:
            if ctx.backup:
                for n in removed + ([new_jar.name] if new_jar.name in old_names else []):
                    stored = ctx.backup.stored(tgt.id, n)
                    if stored:
                        tmp_back = tgt.plugins_dir / f".{n}.lgt-tmp"
                        _copy(stored, tmp_back)
                        os.replace(tmp_back, tgt.plugins_dir / n)
            if new_jar.name not in old_names:
                dest.unlink(missing_ok=True)
            detail += "; rolled back"
        except OSError as e2:
            detail += f"; ROLLBACK FAILED ({e2}) — use Undo on this job"
        return ctx.result(tgt.id, item, action, "error", detail)
    ctx.result(tgt.id, item, action, "changed", detail, changes, **jar_fields)


def remove_plugin(ctx: Ctx, tgt: Server, key: str, remove_folder: bool, force: bool = False) -> None:
    found = [p for p in inventory.list_plugins(tgt) if p["key"] == key]
    if not found:
        return ctx.result(tgt.id, key, "delete", "skipped", "not installed")
    removing = {p["jar"] for p in found}
    if remove_folder:  # decide sharing before the jars are gone
        for folder in sorted({p["folder"] for p in found if p["folder"]}):
            ctx.guard(tgt.id, folder, "delete", delete_folder_checked, ctx, tgt, folder, removing, force)
    for p in found:
        ctx.guard(tgt.id, p["jar"], "delete", delete_item, ctx, tgt, p["jar"])


# ---------------------------------------------------------------- deploy requests

def _str_list(v: Any, field: str) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
        raise DeployError(f"{field} must be a list of non-empty strings")
    return list(dict.fromkeys(v))


def validate_deploy(body: dict) -> dict:
    """Normalize and validate a deploy/plan request. Returns a dict with resolved Server objects."""
    if not isinstance(body, dict):
        raise DeployError("body must be an object")
    action = body.get("action", "sync")
    if action not in ACTIONS:
        raise DeployError(f"action must be one of {ACTIONS}")
    items = body.get("items") or {}
    if not isinstance(items, dict):
        raise DeployError("items must be an object")
    jars = _str_list(items.get("jars"), "items.jars")
    folders = _str_list(items.get("folders"), "items.folders")
    paths = _str_list(items.get("paths"), "items.paths")
    uploads = _str_list(items.get("uploads"), "items.uploads")
    options = body.get("options") or {}
    if not isinstance(options, dict):
        raise DeployError("options must be an object")
    for n in jars + folders:
        inventory.check_rel(n, single=True)
    paths = [inventory.check_rel(p) for p in paths]
    # A top-level jar given as a path is a jar item (so it gets jar-replace semantics).
    jars += [p for p in paths if "/" not in p and p.lower().endswith(".jar") and p not in jars]
    paths = [p for p in paths if not ("/" not in p and p.lower().endswith(".jar"))]
    # Drop items already covered by a parent item, so one job never touches a path twice.
    parents = folders + paths
    paths = [p for p in paths if p not in folders
             and not any(p.startswith(q + "/") for q in parents)]
    if not (jars or folders or paths or uploads):
        raise DeployError("select at least one item")
    if action == "delete" and uploads:
        raise DeployError("uploads cannot be deleted; delete by jar name instead")

    known = inventory.servers_by_id()
    source_id = body.get("source")
    source = None
    if source_id not in (None, ""):
        source = known.get(source_id) if isinstance(source_id, str) else None
        if source is None:
            raise DeployError(f"unknown source server: {source_id!r}")
    elif action != "delete" and (jars or folders or paths):
        raise DeployError("source is required")

    target_ids = _str_list(body.get("targets"), "targets")
    if not target_ids:
        raise DeployError("select at least one target")
    targets = []
    for tid in target_ids:
        srv = known.get(tid)
        if srv is None:
            raise DeployError(f"unknown target server: {tid!r}")
        if source and srv.id == source.id:
            raise DeployError(f"{tid} is the source; it cannot also be a target")
        if srv.family == "fabric":
            raise DeployError(f"{tid} is a Fabric server; it has no plugins")
        if source and srv.family != source.family:
            raise DeployError(f"{tid} is a {srv.platform} server; it cannot receive items from "
                              f"{source.id} ({source.platform})")
        targets.append(srv)
    if action == "delete" and not source and len({t.family for t in targets}) > 1:
        raise DeployError("cannot mix proxy and game servers in one delete")

    upload_files = [upload_path(u) for u in uploads]
    return {
        "action": action, "source": source, "targets": targets,
        "jars": jars, "folders": folders, "paths": paths, "uploads": list(zip(uploads, upload_files)),
        "install": action == "install" or bool(options.get("install")),
        "force": body.get("force") is True,
    }


def run_deploy(ctx: Ctx, req: dict) -> None:
    action, source, install = req["action"], req["source"], req["install"]
    for tgt in req["targets"]:
        ctx.log(f"==> {tgt.id}")
        if action == "delete":
            removing = set(req["jars"])
            for n in req["folders"] + [p for p in req["paths"] if "/" not in p]:
                ctx.guard(tgt.id, n, "delete", delete_folder_checked, ctx, tgt, n, removing, req["force"])
            for n in req["jars"] + [p for p in req["paths"] if "/" in p]:
                ctx.guard(tgt.id, n, "delete", delete_item, ctx, tgt, n)
            continue
        for n in req["jars"]:
            ctx.guard(tgt.id, n, action, _deploy_jar, ctx, source, tgt, n, action, install)
        for _uid, path in req["uploads"]:
            label = f"{path.name} (upload)"
            ctx.guard(tgt.id, label, "replace", replace_jar, ctx, tgt, path, install, label=label)
        for n in req["folders"]:
            ctx.guard(tgt.id, n + "/", "mirror", sync_folder, ctx, source, tgt, n, install)
        for rel in req["paths"]:
            ctx.guard(tgt.id, rel, "sync", sync_path, ctx, source, tgt, rel, install)


def _deploy_jar(ctx: Ctx, source: Server, tgt: Server, name: str, action: str, install: bool) -> None:
    """Plugin jars go through replace_jar whenever a new file may be added (replace or install), so a
    target never ends up with two versions side by side. Plain sync keeps the script's by-name update."""
    src = inventory.resolve_in(source, name, single=True)
    if not name.lower().endswith(".jar") or not (action == "replace" or install):
        return sync_file(ctx, source, tgt, name, install)
    if not src.is_file() or src.is_symlink():
        return ctx.result(tgt.id, name, action, "skipped", "missing on source")
    if action != "replace" and inventory.describe_jar(src, tgt.family)["descriptor"] is None:
        return sync_file(ctx, source, tgt, name, install)  # library jar without a plugin descriptor
    replace_jar(ctx, tgt, src, install, label=name, action=action)


def _dry_run(body: dict) -> tuple[dict, Ctx, dict]:
    req = validate_deploy(body)
    ctx = Ctx(dry_run=True)
    run_deploy(ctx, req)
    out = {"action": req["action"], "source": req["source"].id if req["source"] else None,
           "targets": [t.id for t in req["targets"]], "results": ctx.results, "summary": summarize(ctx.results),
           "warnings": ctx.warnings}
    return out, ctx, req


def dry_run(body: dict) -> dict:
    return _dry_run(body)[0]


def plan(body: dict, user: str = "local") -> dict:
    """Dry run a deploy and store it; POST /deploy {plan_id} later applies exactly this."""
    out, ctx, req = _dry_run(body)
    p = plans.create("deploy", user, body=body, fingerprint=fingerprint(ctx), sources=source_hashes(req))
    return {"plan_id": p["plan_id"], "expires": plans.iso(p["expires"]), **out}


def fingerprint(ctx: Ctx) -> list[list]:
    """What a plan promises, per row: outcome + digest of the full change list (for mirrors only the
    deletions, since a running server keeps writing its own folders). Delete rows: just the outcome."""
    return [[r["server"], r["item"], r["action"], r["outcome"],
             "" if r["action"] == "delete" else ctx.fps.get((r["server"], r["item"], r["action"]), "")]
            for r in ctx.results]


def source_hashes(req: dict) -> list[list]:
    """Content identity of every source item, so a source edited after the preview is caught even when
    rsync's itemized output would look the same (same-size edits, same-name jar rebuilds)."""
    out: list[list] = []
    src = req["source"]
    for _uid, path in req["uploads"]:
        out.append(["upload", path.name, inventory.file_hash(path)])
    if src is None or req["action"] == "delete":
        return out
    for rel in req["jars"] + req["folders"] + req["paths"]:
        try:
            p = inventory.resolve_in(src, rel)
        except PathError:
            out.append(["item", rel, "invalid"])
            continue
        out.append(["item", rel, _content_sig(p)])
    return out


def _content_sig(p: Path) -> str:
    if p.is_symlink() or not p.exists():
        return "missing"
    if p.is_file():
        return inventory.file_hash(p)
    h = hashlib.sha1()
    for root, dirs, files in os.walk(p):
        dirs.sort()
        for f in sorted(files):
            fp = os.path.join(root, f)
            try:
                st = os.stat(fp, follow_symlinks=False)
            except OSError:
                continue
            h.update(f"{os.path.relpath(fp, p)}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def plan_drift(plan: dict) -> list[dict]:
    """Rows whose fresh dry run (or source content) no longer matches what was previewed."""
    out_now, ctx, req = _dry_run(plan["body"])
    out = []
    old_src = {(k, n): h for k, n, h in plan.get("sources", [])}
    for k, n, h in source_hashes(req):
        if old_src.get((k, n)) != h:
            out.append({"server": None, "item": n, "action": "source", "planned": "previewed content",
                        "now": "source changed since preview", "reason": "source content changed"})
    fresh, old = fingerprint(ctx), plan["fingerprint"]
    if fresh == old:
        return out
    old_map = {(r[0], r[1], r[2]): r for r in old}
    new_map = {(r[0], r[1], r[2]): r for r in fresh}
    for k in sorted(set(old_map) | set(new_map), key=str):
        a, b = old_map.get(k), new_map.get(k)
        if a != b:
            out.append({"server": k[0], "item": k[1], "action": k[2],
                        "planned": a[3] if a else None, "now": b[3] if b else None,
                        "reason": "different changes than previewed" if a and b and a[3] == b[3]
                        else "outcome changed"})
    return out or [{"server": None, "item": None, "action": None, "planned": "order", "now": "changed"}]


def summarize(results: list[dict]) -> dict:
    out = {"changed": 0, "unchanged": 0, "skipped": 0, "error": 0}
    for r in results:
        out[r["outcome"]] = out.get(r["outcome"], 0) + 1
    return out


# ---------------------------------------------------------------- uploads

def upload_path(upload_id: str) -> Path:
    if not isinstance(upload_id, str) or not upload_id.isalnum() or len(upload_id) > 40:
        raise DeployError(f"invalid upload id: {upload_id!r}")
    d = config.state("uploads", upload_id)
    jars = list(d.glob("*.jar")) if d.is_dir() else []
    if len(jars) != 1:
        raise DeployError(f"unknown upload: {upload_id}")
    return jars[0]


# ---------------------------------------------------------------- undo

def run_undo(ctx: Ctx, original_job_id: str) -> None:
    src = Backup(original_job_id)
    if not src.entries:
        raise DeployError("that job has no backups to restore")
    known = inventory.servers_by_id()
    for e in reversed(src.entries):
        srv = known.get(e["server"])
        if srv is None:
            ctx.result(e["server"], e["rel"], "restore", "error", "server no longer exists")
            continue
        ctx.guard(srv.id, e["rel"], "restore", _restore_entry, ctx, src, srv, e)


def _restore_entry(ctx: Ctx, src: Backup, srv: Server, e: dict) -> None:
    try:
        path = inventory.resolve_in(srv, e["rel"])
    except PathError as err:
        return ctx.result(srv.id, e["rel"], "restore", "error", str(err))
    stored = src.store_path(e)
    if e["existed"] and not _exists(stored):
        return ctx.result(srv.id, e["rel"], "restore", "error", "backup copy is missing")
    if ctx.dry_run:
        return ctx.result(srv.id, e["rel"], "restore", "changed",
                          "would restore previous version" if e["existed"] else "would remove (did not exist before)")
    had_current = _exists(path)
    # Move the current state aside (into this undo job's backup), so the undo is itself undoable.
    ctx.backup.save(srv, e["rel"], move=had_current)
    if not e["existed"]:
        return ctx.result(srv.id, e["rel"], "restore", "changed", "removed (did not exist before)")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _copy(stored, path)
    except (OSError, subprocess.CalledProcessError) as err:
        # Put the current state back rather than leave nothing there.
        cur = ctx.backup.stored(srv.id, e["rel"])
        if had_current and cur and not _exists(path):
            _copy(cur, path)
        return ctx.result(srv.id, e["rel"], "restore", "error", f"restore failed: {err}")
    ctx.result(srv.id, e["rel"], "restore", "changed", "restored previous version")


def overlapping_later_jobs(job_id: str) -> list[str]:
    """Later, still-effective jobs whose backups touch the same server paths as job_id."""
    mine = Backup(job_id).entries
    if not mine:
        return []
    from . import jobs  # local import: jobs does not depend on engine
    out = []
    for d in sorted(config.state("backups").iterdir()):
        other = d.name
        if other <= job_id or not (d / "manifest.json").is_file():
            continue
        j = jobs.get(other)
        if j is None or j.undone_by or (j.undo_of and j.undo_of > job_id):
            continue
        for e in Backup(other).entries:
            if any(e["server"] == m["server"] and (e["rel"] == m["rel"] or e["rel"].startswith(m["rel"] + "/")
                                                  or m["rel"].startswith(e["rel"] + "/")) for m in mine):
                out.append(other)
                break
    return out
