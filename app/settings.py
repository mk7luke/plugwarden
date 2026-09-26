"""Persistent settings (STATE_DIR/settings.json) with validation."""
from __future__ import annotations

import copy
import re
import threading
from typing import Any

from . import config, inventory
from .storage import read_json, write_json

MODES = ("off", "notify", "apply")
SOURCE_KINDS = ("modrinth", "hangar", "spiget", "github")
WINDOW_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")
KEY_RE = re.compile(r"^(bukkit|velocity):[a-z0-9]{1,80}$")
BUILTIN_GROUPS = ("Game servers", "M1–M8")

DEFAULTS: dict[str, Any] = {
    "groups": {},
    "default_source": "elChapo01",
    "auto_update": {"mode": "off", "interval_hours": 24, "window": None, "dry_run_first": True},
    "pins": {},      # key -> {"version": str, "servers": [ids] | "*"}
    "ignores": {},   # key -> {"servers": [ids] | "*"}
    "source_map": {},
    "backup_keep_jobs": 100,
}

_lock = threading.Lock()


class SettingsError(ValueError):
    pass


def _path():
    return config.state("settings.json")


def builtin_groups(servers: list[inventory.Server] | None = None) -> dict[str, list[str]]:
    servers = inventory.discover() if servers is None else servers
    return {
        "Game servers": [s.id for s in servers if s.family == "bukkit"],
        "M1–M8": [s.id for s in servers if re.match(r"^M[1-8]-", s.id)],
    }


def load_raw() -> dict:
    data = read_json(_path(), {}) or {}
    out = copy.deepcopy(DEFAULTS)
    for k in DEFAULTS:
        if k in data:
            out[k] = data[k]
    out["pins"], out["ignores"] = migrate_holds(out["pins"], out["ignores"])
    au = dict(DEFAULTS["auto_update"])
    au.update(out.get("auto_update") or {})
    out["auto_update"] = au
    return out


def load() -> dict:
    """Settings as exposed by the API: stored values plus computed/read-only fields."""
    raw = load_raw()
    groups = builtin_groups()
    groups.update({k: v for k, v in raw["groups"].items() if k not in BUILTIN_GROUPS})
    raw["groups"] = groups
    raw["builtin_groups"] = list(BUILTIN_GROUPS)
    raw["base"] = str(config.BASE)
    raw["base_readonly"] = True
    raw["base_overridden"] = config.BASE_OVERRIDDEN
    return raw


def _validate(new: dict, known_ids: set[str]) -> dict:
    out = load_raw()
    if "groups" in new:
        g = new["groups"]
        if not isinstance(g, dict):
            raise SettingsError("groups must be an object")
        groups = {}
        for name, ids in g.items():
            if name in BUILTIN_GROUPS:
                continue
            if not isinstance(name, str) or not name.strip() or len(name) > 60:
                raise SettingsError(f"invalid group name: {name!r}")
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise SettingsError(f"group {name!r} must be a list of server ids")
            unknown = [i for i in ids if i not in known_ids]
            if unknown:
                raise SettingsError(f"group {name!r} has unknown servers: {unknown}")
            groups[name.strip()] = list(dict.fromkeys(ids))
        out["groups"] = groups
    if "default_source" in new:
        if new["default_source"] not in known_ids:
            raise SettingsError(f"unknown default_source: {new['default_source']!r}")
        out["default_source"] = new["default_source"]
    if "auto_update" in new:
        au = new["auto_update"]
        if not isinstance(au, dict):
            raise SettingsError("auto_update must be an object")
        cur = dict(out["auto_update"])
        if "mode" in au:
            if au["mode"] not in MODES:
                raise SettingsError(f"auto_update.mode must be one of {MODES}")
            cur["mode"] = au["mode"]
        if "interval_hours" in au:
            try:
                ih = float(au["interval_hours"])
            except (TypeError, ValueError):
                raise SettingsError("interval_hours must be a number")
            if not 1 <= ih <= 24 * 30:
                raise SettingsError("interval_hours must be between 1 and 720")
            cur["interval_hours"] = ih
        if "window" in au:
            w = au["window"]
            if w in (None, ""):
                cur["window"] = None
            elif isinstance(w, str) and WINDOW_RE.match(w):
                cur["window"] = w
            else:
                raise SettingsError("window must be null or 'HH:MM-HH:MM'")
        if "dry_run_first" in au:
            cur["dry_run_first"] = bool(au["dry_run_first"])
        out["auto_update"] = cur
    if "pins" in new or "ignores" in new:
        pins, ignores = migrate_holds(new.get("pins", out["pins"]), new.get("ignores", out["ignores"]))
        for kind, holds in (("pins", pins), ("ignores", ignores)):
            for k, v in holds.items():
                if not KEY_RE.match(str(k)):
                    raise SettingsError(f"{kind}: invalid plugin key {k!r}")
                sv = v.get("servers")
                if sv != "*" and not (isinstance(sv, list) and sv and all(s in known_ids for s in sv)):
                    raise SettingsError(f"{kind}[{k}].servers must be '*' or a non-empty list of known server ids")
                if kind == "pins" and (not isinstance(v.get("version"), str) or len(v["version"]) > 100):
                    raise SettingsError(f"pins[{k}].version must be a string")
        out["pins"], out["ignores"] = pins, ignores
    if "source_map" in new:
        sm = new["source_map"]
        if not isinstance(sm, dict):
            raise SettingsError("source_map must be an object")
        clean = {}
        for k, v in sm.items():
            if not KEY_RE.match(str(k)):
                raise SettingsError(f"invalid plugin key: {k!r}")
            if v is None:
                continue
            if not isinstance(v, dict) or v.get("kind") not in SOURCE_KINDS:
                raise SettingsError(f"source_map[{k}].kind must be one of {SOURCE_KINDS}")
            sid = str(v.get("id") or "").strip()
            if not sid or len(sid) > 200 or not re.match(r"^[A-Za-z0-9._\-/]+$", sid) or ".." in sid:
                raise SettingsError(f"source_map[{k}].id is invalid")
            if v["kind"] == "github" and sid.count("/") != 1:
                raise SettingsError("github id must be 'owner/repo'")
            if v["kind"] == "spiget" and not sid.isdigit():
                raise SettingsError("spiget id must be the numeric resource id")
            entry = {"kind": v["kind"], "id": sid}
            if v.get("asset"):
                try:
                    re.compile(str(v["asset"]))
                except re.error:
                    raise SettingsError(f"source_map[{k}].asset is not a valid regex")
                entry["asset"] = str(v["asset"])[:200]
            clean[k] = entry
        out["source_map"] = clean
    if "backup_keep_jobs" in new:
        try:
            n = int(new["backup_keep_jobs"])
        except (TypeError, ValueError):
            raise SettingsError("backup_keep_jobs must be an integer")
        out["backup_keep_jobs"] = max(5, min(n, 1000))
    return out


def migrate_holds(pins: Any, ignores: Any) -> tuple[dict, dict]:
    """Accept legacy shapes (pins {key: "ver"}, ignores [key]) and return the scoped shapes."""
    out_p: dict[str, dict] = {}
    if isinstance(pins, dict):
        for k, v in pins.items():
            if isinstance(v, str):
                out_p[k] = {"version": v, "servers": "*"}
            elif isinstance(v, dict):
                out_p[k] = {"version": v.get("version"), "servers": _scope(v.get("servers", "*"))}
    out_i: dict[str, dict] = {}
    if isinstance(ignores, list):
        out_i = {k: {"servers": "*"} for k in ignores if isinstance(k, str)}
    elif isinstance(ignores, dict):
        for k, v in ignores.items():
            out_i[k] = {"servers": _scope((v or {}).get("servers", "*") if isinstance(v, dict) else "*")}
    return out_p, out_i


def _scope(v: Any) -> Any:
    if v == "*" or v is None:
        return "*"
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return sorted(set(v))
    return v  # left for validation to reject


def hold_applies(hold: dict | None, server_id: str) -> bool:
    return bool(hold) and (hold["servers"] == "*" or server_id in hold["servers"])


def set_hold(holds: dict, key: str, servers: Any, on: bool, version: str | None = None,
             all_ids: list[str] | None = None) -> None:
    """Add or remove a pin/ignore for a key on some servers ('*' = network-wide)."""
    cur = holds.get(key)
    if on:
        if cur and cur.get("version") != version and servers != "*" and \
                (cur["servers"] == "*" or not set(cur["servers"]) <= set(servers)):
            where = "all servers" if cur["servers"] == "*" else ", ".join(cur["servers"])
            raise SettingsError(f"{key} is already pinned at {cur.get('version')} on {where}; "
                                f"unpin it there first (one pinned version per plugin)")
        if cur and cur.get("version") == version and servers != "*":
            servers = "*" if cur["servers"] == "*" else sorted(set(cur["servers"]) | set(servers))
        holds[key] = {"servers": _scope(servers), **({"version": version} if version is not None else {})}
        return
    if not cur:
        return
    if servers == "*":
        holds.pop(key, None)
    else:
        base = set(all_ids or []) if cur["servers"] == "*" else set(cur["servers"])
        left = sorted(base - set(servers))
        if left:
            cur["servers"] = left
        else:
            holds.pop(key, None)


def update(new: dict) -> dict:
    if not isinstance(new, dict):
        raise SettingsError("settings body must be an object")
    known = {s.id for s in inventory.discover()}
    with _lock:
        out = _validate(new, known)
        write_json(_path(), out)
    return load()


def mutate(fn) -> dict:
    """Apply fn(raw_settings) under the lock and persist."""
    with _lock:
        raw = load_raw()
        fn(raw)
        write_json(_path(), raw)
    return load()
