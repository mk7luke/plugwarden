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
    "auto_update": {"mode": "off", "interval_hours": 24, "window": None, "dry_run_first": True,
                    # unattended "apply" safety policy
                    "min_release_age_hours": 48, "canary_server": None, "canary_soak_hours": 24,
                    "max_changes_per_run": 20,
                    # restart servers in the maintenance window after an unattended apply (needs AMP)
                    "auto_restart_canary": False, "auto_restart_rest": False},
    # console commands that need an explicit confirm (fnmatch patterns, case-insensitive)
    "amp_command_denylist": ["stop", "restart", "reload*", "op *", "deop *", "lp user * permission set *",
                             "luckperms user * permission set *", "whitelist off", "ban-ip *", "pardon *"],
    "backup_max_age_days": 30,
    "backup_max_gb": 5,
    "pins": {},      # key -> {"version": str, "servers": [ids] | "*"}
    "ignores": {},   # key -> {"servers": [ids] | "*"}
    "source_map": {},
    "backup_keep_jobs": 100,
}

_lock = threading.Lock()


class SettingsError(ValueError):
    """Invalid settings. `fields` maps a field path (e.g. "auto_update.max_changes_per_run") to a
    message the UI can show inline; the API returns 422 {detail: {message, fields}}."""

    def __init__(self, message: str = "", fields: dict[str, str] | None = None):
        self.fields = dict(fields or {})
        super().__init__(message or "; ".join(f"{k}: {v}" for k, v in self.fields.items()))


# Numeric settings: (min, max, integer, nullable). None for max_changes_per_run means "no limit".
NUMERIC = {
    "auto_update.interval_hours": (1, 720, False, False),
    "auto_update.min_release_age_hours": (0, 24 * 90, False, False),
    "auto_update.canary_soak_hours": (0, 24 * 30, False, False),
    "auto_update.max_changes_per_run": (1, 500, True, True),
    "backup_keep_jobs": (5, 1000, True, False),
    "backup_max_age_days": (1, 3650, False, False),
    "backup_max_gb": (0.1, 10000, False, False),
}


def _number(path: str, value: Any, errors: dict[str, str]):
    lo, hi, integer, nullable = NUMERIC[path]
    kind = "a whole number" if integer else "a number"
    if value is None or value == "":
        if nullable:
            return None
        errors[path] = f"Enter {kind} between {lo:g} and {hi:g}"
        return _MISSING
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        errors[path] = f"Enter {kind} between {lo:g} and {hi:g}"
        return _MISSING
    try:
        v = float(value)
    except ValueError:
        errors[path] = f"Enter {kind} between {lo:g} and {hi:g}"
        return _MISSING
    if v != v or (integer and v != int(v)) or not lo <= v <= hi:
        errors[path] = f"Enter {kind} between {lo:g} and {hi:g}" + (" (or leave empty for no limit)" if nullable else "")
        return _MISSING
    return int(v) if integer else v


_MISSING = object()


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
            raise SettingsError("groups must be an object", {"groups": "groups must be an object"})
        groups = {}
        for name, ids in g.items():
            if name in BUILTIN_GROUPS:
                continue
            if not isinstance(name, str) or not name.strip() or len(name) > 60:
                raise SettingsError(f"invalid group name: {name!r}", {"groups": f"invalid group name: {name!r}"})
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise SettingsError(f"group {name!r} must be a list of server ids", {"groups": f"group {name!r} must be a list of server ids"})
            unknown = [i for i in ids if i not in known_ids]
            if unknown:
                raise SettingsError(f"group {name!r} has unknown servers: {unknown}", {"groups": f"group {name!r} has unknown servers: {unknown}"})
            groups[name.strip()] = list(dict.fromkeys(ids))
        out["groups"] = groups
    if "default_source" in new:
        if new["default_source"] not in known_ids:
            raise SettingsError(fields={"default_source": f"Unknown server: {new['default_source']}"})
        out["default_source"] = new["default_source"]
    if "auto_update" in new:
        au = new["auto_update"]
        if not isinstance(au, dict):
            raise SettingsError(fields={"auto_update": "must be an object"})
        cur = dict(out["auto_update"])
        if "mode" in au and au["mode"] in MODES:
            cur["mode"] = au["mode"]
        errors: dict[str, str] = {}
        for fld in ("interval_hours", "min_release_age_hours", "canary_soak_hours", "max_changes_per_run"):
            if fld in au:
                v = _number(f"auto_update.{fld}", au[fld], errors)
                if v is not _MISSING:
                    cur[fld] = v
        if "window" in au:
            w = au["window"]
            if w in (None, ""):
                cur["window"] = None
            elif isinstance(w, str) and WINDOW_RE.match(w):
                cur["window"] = w
            else:
                errors["auto_update.window"] = "Use HH:MM-HH:MM (24 h), or leave empty for any time"
        if "dry_run_first" in au:
            cur["dry_run_first"] = bool(au["dry_run_first"])
        for flag in ("auto_restart_canary", "auto_restart_rest"):
            if flag in au:
                cur[flag] = au[flag] is True
        if "canary_server" in au:
            if au["canary_server"] not in (None, "") and au["canary_server"] not in known_ids:
                errors["auto_update.canary_server"] = f"Unknown server: {au['canary_server']}"
            else:
                cur["canary_server"] = au["canary_server"] or None
        if "mode" in au and au["mode"] not in MODES:
            errors["auto_update.mode"] = f"One of {', '.join(MODES)}"
        if errors:
            raise SettingsError(fields=errors)
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
            if v.get("auto_apply") is True:
                entry["auto_apply"] = True  # scheduler may auto-apply from this manual source
            if v.get("asset"):
                try:
                    re.compile(str(v["asset"]))
                except re.error:
                    raise SettingsError(f"source_map[{k}].asset is not a valid regex")
                entry["asset"] = str(v["asset"])[:200]
            clean[k] = entry
        out["source_map"] = clean
    if "amp_command_denylist" in new:
        dl = new["amp_command_denylist"]
        if not isinstance(dl, list) or len(dl) > 100 or not all(
                isinstance(x, str) and 0 < len(x.strip()) <= 100 for x in dl):
            raise SettingsError(fields={"amp_command_denylist": "A list of up to 100 command patterns"})
        out["amp_command_denylist"] = [x.strip() for x in dl]
    errors = {}
    for fld in ("backup_keep_jobs", "backup_max_age_days", "backup_max_gb"):
        if fld in new:
            v = _number(fld, new[fld], errors)
            if v is not _MISSING:
                out[fld] = v
    if errors:
        raise SettingsError(fields=errors)
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
