"""Update checks (Modrinth by sha1; Hangar/Spiget/GitHub for manually mapped plugins) and applying updates."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from . import config, engine, inventory, plans, settings
from .storage import read_json, write_json

MODRINTH = "https://api.modrinth.com/v2"
HANGAR = "https://hangar.papermc.io/api/v1"
SPIGET = "https://api.spiget.org/v2"
GITHUB = "https://api.github.com"
LOADERS = {
    "bukkit": ["paper", "purpur", "spigot", "bukkit", "folia"],
    "velocity": ["velocity"],
}
HANGAR_PLATFORM = {"bukkit": "PAPER", "velocity": "VELOCITY"}
MAX_DOWNLOAD = config.MAX_UPLOAD_BYTES

# Tests replace this with httpx.MockTransport.
TRANSPORT: httpx.BaseTransport | None = None

_cache_lock = threading.Lock()


# Every request (API calls, downloads and each redirect hop) must go to one of these hosts over https:
# a hostile API response can't make the server fetch internal or arbitrary URLs.
ALLOWED_FETCH_HOSTS = {
    "api.modrinth.com", "cdn.modrinth.com",
    "hangar.papermc.io", "hangarcdn.papermc.io",
    "api.spiget.org", "www.spigotmc.org", "spigotmc.org",
    "api.github.com", "github.com", "codeload.github.com",
    "objects.githubusercontent.com", "release-assets.githubusercontent.com",
}


def _https_only(request: httpx.Request) -> None:
    if request.url.scheme != "https":
        raise httpx.UnsupportedProtocol(f"refusing non-https URL: {request.url}", request=request)
    if request.url.host not in ALLOWED_FETCH_HOSTS:
        raise httpx.UnsupportedProtocol(f"refusing download host {request.url.host!r}", request=request)


_HASH_LEN = {"sha1": 40, "sha256": 64, "sha512": 128}


def _clean_hashes(hashes) -> dict:
    """Keep only well-formed hex digests (API data is untrusted: it also names the staging folder)."""
    out = {}
    for algo, n in _HASH_LEN.items():
        v = (hashes or {}).get(algo) if isinstance(hashes, dict) else None
        if isinstance(v, str) and re.fullmatch(rf"[0-9a-fA-F]{{{n}}}", v):
            out[algo] = v.lower()
    return out


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"},
                        timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True,
                        transport=TRANSPORT, event_hooks={"request": [_https_only]})


# ---------------------------------------------------------------- cache

def _cache_file() -> Path:
    return config.state("cache", "updates.json")


def load_cache() -> dict:
    return read_json(_cache_file(), None) or {"checked_at": None, "entries": {}}


def cache_key(sha1: str, family: str, mc: str | None) -> str:
    return f"{sha1}|{family}|{mc or '*'}"


# ---------------------------------------------------------------- version helpers

def _parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def compare_versions(a: str | None, b: str | None) -> int | None:
    """Loose numeric comparison: 1 if a>b, -1 if a<b, 0 if equal, None if not comparable."""
    if not a or not b:
        return None
    na = [int(x) for x in re.findall(r"\d+", a)]
    nb = [int(x) for x in re.findall(r"\d+", b)]
    if not na or not nb:
        return None if a.strip().lower() != b.strip().lower() else 0
    n = max(len(na), len(nb))
    na += [0] * (n - len(na))
    nb += [0] * (n - len(nb))
    return (na > nb) - (na < nb)


_LOADER_PREFIX = re.compile(r"^(bukkit|paper|spigot|purpur|folia|velocity|bungee|bungeecord|waterfall)[-_ ]+(?=v?\d)", re.I)


def display_version(v: str | None) -> str | None:
    """Modrinth version numbers sometimes carry a loader prefix ('bukkit-2.6.24'); drop it."""
    return _LOADER_PREFIX.sub("", v) if v else v


def _modrinth_file(version: dict) -> dict | None:
    files = version.get("files") or []
    for f in files:
        if f.get("primary"):
            return f
    jars = [f for f in files if str(f.get("filename", "")).endswith(".jar")]
    return jars[0] if jars else (files[0] if files else None)


def _modrinth_latest(v: dict) -> dict:
    f = _modrinth_file(v) or {}
    pid, vid = v.get("project_id"), v.get("id")
    return {
        "version": display_version(v.get("version_number")), "version_id": vid, "type": v.get("version_type"),
        "published": v.get("date_published"),
        "url": f"https://modrinth.com/project/{pid}/version/{vid}",
        "changelog_url": f"https://modrinth.com/project/{pid}/version/{vid}",
        "download_url": f.get("url"), "filename": f.get("filename"),
        "hashes": {k: v2 for k, v2 in (f.get("hashes") or {}).items() if k in ("sha1", "sha512")},
        "verified": bool(f.get("hashes")), "size": f.get("size"),
        "compat": _compat(v),
    }


def _compat(v: dict) -> dict:
    return {"mc_versions": list(v.get("game_versions") or []), "loaders": list(v.get("loaders") or [])}


def _is_newer(new: dict | None, cur: dict) -> bool:
    if not new:
        return False
    t_new, t_cur = _parse_time(new.get("date_published")), _parse_time(cur.get("date_published"))
    return (new.get("id") != cur.get("id") and new.get("version_number") != cur.get("version_number")
            and (t_new is None or t_cur is None or t_new > t_cur))


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ---------------------------------------------------------------- source resolvers (mapped plugins)

def _resolve_modrinth_project(c: httpx.Client, pid: str, family: str, mc: str | None) -> dict | None:
    params = {"loaders": _json_list(LOADERS[family])}
    if mc:
        params["game_versions"] = _json_list([mc])
    r = c.get(f"{MODRINTH}/project/{pid}/version", params=params)
    r.raise_for_status()
    versions = r.json()
    if not versions:
        return None
    releases = [v for v in versions if v.get("version_type") == "release"]
    return _modrinth_latest((releases or versions)[0])


def _resolve_hangar(c: httpx.Client, slug: str, family: str, mc: str | None) -> dict | None:
    platform = HANGAR_PLATFORM[family]
    r = c.get(f"{HANGAR}/projects/{slug}/latestrelease")
    r.raise_for_status()
    ver = r.text.strip().strip('"')
    r = c.get(f"{HANGAR}/projects/{slug}/versions/{ver}")
    r.raise_for_status()
    d = r.json()
    dl = (d.get("downloads") or {}).get(platform)
    if not dl:
        return None
    deps = (d.get("platformDependencies") or {}).get(platform) or []
    if mc and family == "bukkit" and deps and not any(_mc_matches(mc, x) for x in deps):
        return None
    fi = dl.get("fileInfo") or {}
    return {
        "version": ver, "version_id": str(d.get("id") or ver), "type": "release",
        "published": d.get("createdAt"),
        "url": f"https://hangar.papermc.io/{slug}", "changelog_url": f"https://hangar.papermc.io/{slug}/versions/{ver}",
        "download_url": dl.get("downloadUrl") or dl.get("externalUrl"), "filename": fi.get("name"),
        "hashes": {"sha256": fi["sha256Hash"]} if fi.get("sha256Hash") else {},
        "verified": bool(fi.get("sha256Hash")) and bool(dl.get("downloadUrl")),
        "size": fi.get("sizeBytes"), "compat": {"mc_versions": list(deps), "loaders": [platform.lower()]},
    }


def _mc_matches(mc: str, spec: str) -> bool:
    """Hangar platform dependency entries are exact versions, 'a-b' ranges or 'x.y.x' wildcards."""
    spec = spec.strip()
    if "-" in spec:
        lo, hi = spec.split("-", 1)
        return compare_versions(mc, lo) in (0, 1) and compare_versions(mc, hi) in (0, -1)
    if spec.endswith(".x"):
        return mc == spec[:-2] or mc.startswith(spec[:-1])
    return mc == spec


def _resolve_spiget(c: httpx.Client, rid: str, family: str, mc: str | None) -> dict | None:
    r = c.get(f"{SPIGET}/resources/{rid}")
    r.raise_for_status()
    res = r.json()
    r = c.get(f"{SPIGET}/resources/{rid}/versions/latest")
    r.raise_for_status()
    v = r.json()
    external = bool(res.get("external")) or (res.get("file") or {}).get("type") == "external"
    published = None
    if v.get("releaseDate"):
        published = datetime.fromtimestamp(int(v["releaseDate"]), timezone.utc).isoformat()
    return {
        "version": v.get("name"), "version_id": str(v.get("id")), "type": "release", "published": published,
        "url": f"https://www.spigotmc.org/resources/{rid}/",
        "changelog_url": f"https://www.spigotmc.org/resources/{rid}/updates",
        "download_url": None if external or res.get("premium") else f"{SPIGET}/resources/{rid}/download",
        "filename": None, "hashes": {}, "verified": False, "size": None, "compat": None,
    }


def _resolve_github(c: httpx.Client, repo: str, family: str, mc: str | None, asset_re: str | None = None) -> dict | None:
    r = c.get(f"{GITHUB}/repos/{repo}/releases/latest", headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    d = r.json()
    assets = [a for a in d.get("assets") or [] if str(a.get("name", "")).endswith(".jar")]
    if asset_re:
        assets = [a for a in assets if re.search(asset_re, a["name"])]
    if len(assets) > 1:
        words = ("velocity",) if family == "velocity" else ("paper", "bukkit", "spigot", "purpur")
        pref = [a for a in assets if any(w in a["name"].lower() for w in words)]
        if family == "bukkit" and not pref:
            pref = [a for a in assets if not any(w in a["name"].lower() for w in ("velocity", "bungee", "fabric", "forge", "sponge"))]
        assets = pref or assets
    a = assets[0] if assets else None
    digest = str((a or {}).get("digest") or "")
    hashes = {"sha256": digest.split(":", 1)[1]} if digest.startswith("sha256:") else {}
    tag = str(d.get("tag_name") or "")
    # A release's assets can be replaced later without changing published_at: age from the asset.
    published = max(filter(None, [d.get("published_at"), (a or {}).get("updated_at"), (a or {}).get("created_at")]),
                    default=None)
    return {
        "version": re.sub(r"^v(?=\d)", "", tag), "version_id": str(d.get("id")),
        "type": "prerelease" if d.get("prerelease") else "release",
        "published": published, "url": d.get("html_url"), "changelog_url": d.get("html_url"),
        "download_url": (a or {}).get("browser_download_url"), "filename": (a or {}).get("name"),
        "hashes": hashes, "verified": bool(hashes), "size": (a or {}).get("size"), "compat": None,
    }


def _json_list(items: list[str]) -> str:
    return json.dumps(items)


def _source_info(kind: str, sid: str, extra: dict | None = None) -> dict:
    url = {
        "modrinth": f"https://modrinth.com/project/{sid}",
        "hangar": f"https://hangar.papermc.io/{sid}",
        "spiget": f"https://www.spigotmc.org/resources/{sid}/",
        "github": f"https://github.com/{sid}",
    }[kind]
    return {"kind": kind, "id": sid, "url": url, **(extra or {})}


# ---------------------------------------------------------------- check

def check(job=None) -> dict:
    """Query sources for every installed jar. Writes the cache and returns a summary."""
    log = job.write if job else (lambda s: None)
    smap = settings.load_raw()["source_map"]
    servers = [s for s in inventory.discover() if s.family in LOADERS]
    # (family, mc) -> {sha1: plugin}
    groups: dict[tuple[str, str | None], dict[str, dict]] = {}
    for srv in servers:
        for p in inventory.list_plugins(srv):
            groups.setdefault((srv.family, srv.mc_version), {})[p["sha1"]] = p
    inventory.flush_cache()
    all_hashes = sorted({h for g in groups.values() for h in g})
    log(f"Checking {len(all_hashes)} unique jars across {len(servers)} servers")
    mapped_steps = len({(smap[p["key"]]["kind"], smap[p["key"]]["id"], fam, mc)
                        for (fam, mc), g in groups.items() for p in g.values() if p["key"] in smap})
    progress = {"done": 0, "total": 2 + len(groups) + mapped_steps}

    def step(n: int = 1) -> None:
        progress["done"] = min(progress["total"], progress["done"] + n)
        if job:
            job.progress = dict(progress)
            job.save()

    step(0)

    entries: dict[str, dict] = {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    errors = 0
    previous = load_cache().get("entries", {})
    with _client() as c:
        identified: dict[str, dict] = {}
        try:
            for chunk in _chunks(all_hashes, 500):
                r = c.post(f"{MODRINTH}/version_files", json={"hashes": chunk, "algorithm": "sha1"})
                r.raise_for_status()
                identified.update(r.json())
            log(f"Modrinth recognised {len(identified)} jar(s) by hash")
            step()
        except httpx.HTTPError as e:
            # Without identification every result would be wrong; keep the previous cache untouched.
            raise RuntimeError(f"Modrinth hash lookup failed, previous results kept: {e}") from e

        # Project titles for nicer source labels.
        titles: dict[str, dict] = {}
        pids = sorted({v["project_id"] for v in identified.values() if v.get("project_id")})
        try:
            for chunk in _chunks(pids, 100):
                r = c.get(f"{MODRINTH}/projects", params={"ids": _json_list(chunk)})
                r.raise_for_status()
                for p in r.json():
                    titles[p["id"]] = {"name": p.get("title"), "slug": p.get("slug")}
        except httpx.HTTPError as e:
            log(f"Modrinth project lookup failed: {e}")
        step()

        mapped_cache: dict[tuple, Any] = {}
        for (family, mc), plugins in groups.items():
            hashes = [h for h in plugins if h in identified]
            latest_rel: dict[str, dict] = {}
            latest_any: dict[str, dict] = {}
            group_failed = False
            body = {"algorithm": "sha1", "loaders": LOADERS[family], "game_versions": [mc] if mc else []}
            try:
                for chunk in _chunks(hashes, 500):
                    r = c.post(f"{MODRINTH}/version_files/update", json={**body, "hashes": chunk, "version_types": ["release"]})
                    r.raise_for_status()
                    latest_rel.update(r.json())
                # Pre-release installs may move to a newer pre-release when no newer release exists.
                pre = [h for h in hashes if identified[h].get("version_type") != "release"
                       and not _is_newer(latest_rel.get(h), identified[h])]
                for chunk in _chunks(pre, 500):
                    r = c.post(f"{MODRINTH}/version_files/update", json={**body, "hashes": chunk})
                    r.raise_for_status()
                    latest_any.update(r.json())
            except httpx.HTTPError as e:
                errors += 1
                group_failed = True
                log(f"Modrinth update lookup failed for {family}/{mc or 'any'}: {e}")
            step()

            for sha1, p in plugins.items():
                ck = cache_key(sha1, family, mc)
                mapping = smap.get(p["key"])
                entry: dict[str, Any] = {"key": p["key"], "source": None, "current": None, "latest": None,
                                         "outdated": False, "error": None, "checked_at": now}
                if sha1 in identified and (not mapping or mapping["kind"] == "modrinth") and group_failed:
                    old = previous.get(ck)
                    entry = dict(old) if old else entry
                    entry["error"] = None if old else "update lookup failed"
                    entry["stale"] = True
                elif sha1 in identified and (not mapping or mapping["kind"] == "modrinth"):
                    cur = identified[sha1]
                    pid = cur.get("project_id")
                    entry["source"] = _source_info("modrinth", pid, titles.get(pid))
                    entry["current"] = {"version": display_version(cur.get("version_number")), "version_id": cur.get("id"),
                                        "compat": _compat(cur),
                                        "type": cur.get("version_type"), "published": cur.get("date_published")}
                    rel = latest_rel.get(sha1)
                    new = rel if _is_newer(rel, cur) or not latest_any.get(sha1) else latest_any[sha1]
                    if new:
                        entry["latest"] = _modrinth_latest(new)
                        entry["outdated"] = _is_newer(new, cur)
                    else:
                        entry["latest"] = None  # no newer compatible release; treat as current
                elif mapping:
                    kind, sid = mapping["kind"], mapping["id"]
                    entry["source"] = _source_info(kind, sid, {"manual": True,
                                                               "auto_apply": bool(mapping.get("auto_apply"))})
                    if sha1 in identified:
                        # An explicit mapping wins over a Modrinth hash match: make that visible.
                        pid = identified[sha1].get("project_id")
                        entry["source"]["overrides_modrinth"] = {"id": pid, **(titles.get(pid) or {})}
                    mk = (kind, sid, family, mc)
                    if mk not in mapped_cache:
                        step()
                        try:
                            if kind == "modrinth":
                                mapped_cache[mk] = _resolve_modrinth_project(c, sid, family, mc)
                            elif kind == "hangar":
                                mapped_cache[mk] = _resolve_hangar(c, sid, family, mc)
                            elif kind == "spiget":
                                mapped_cache[mk] = _resolve_spiget(c, sid, family, mc)
                            else:
                                mapped_cache[mk] = _resolve_github(c, sid, family, mc, mapping.get("asset"))
                        except (httpx.HTTPError, ValueError, KeyError) as e:
                            errors += 1
                            mapped_cache[mk] = ValueError(str(e).splitlines()[0])
                            log(f"{kind} lookup for {p['name']} ({sid}) failed: {str(e).splitlines()[0]}")
                    res = mapped_cache[mk]
                    if isinstance(res, Exception):
                        entry["error"] = str(res)
                    elif res:
                        entry["latest"] = res
                        entry["current"] = {"version": p["version"]}
                        cmpv = compare_versions(res["version"], p["version"])
                        entry["outdated"] = cmpv == 1 if cmpv is not None else (
                            (res["version"] or "").lower() != (p["version"] or "").lower())
                entries[ck] = entry
    with _cache_lock:
        cache = load_cache()
        # Keep entries for other (family, mc) combos only if their jars still exist.
        cache["entries"] = entries
        cache["checked_at"] = now
        cache["errors"] = errors
        write_json(_cache_file(), cache)
    n_src = sum(1 for e in entries.values() if e["source"])
    counts = update_counts(pending_updates())
    summary = check_summary(len(entries), n_src, counts, errors)
    with _cache_lock:
        cache = load_cache()
        cache["stats"] = {"jars": len(entries), "identified": n_src, "errors": errors}
        write_json(_cache_file(), cache)
    log(summary)
    step(progress["total"])
    return {"summary": summary, "outdated": counts["installs"], "errors": errors, "counts": counts}


def check_summary(jars: int, identified: int, counts: dict, errors: int) -> str:
    """One wording everywhere: '24 plugins outdated (64 installs on 10 servers) · 46 of 78 jars identified'."""
    s = (f"{counts['plugins']} plugin{'s' * (counts['plugins'] != 1)} outdated "
         f"({counts['installs']} install{'s' * (counts['installs'] != 1)} on {counts['servers']} "
         f"server{'s' * (counts['servers'] != 1)}) · {identified} of {jars} jars identified")
    if errors:
        s += f" · {errors} source{'s' * (errors != 1)} failed"
    return s


# ---------------------------------------------------------------- status views

def status_for(p: dict, srv: inventory.Server, st: dict, cache: dict) -> tuple[str, dict | None, dict | None]:
    entry = cache["entries"].get(cache_key(p["sha1"], srv.family, srv.mc_version))
    source = entry["source"] if entry else None
    latest = entry["latest"] if entry else None
    if settings.hold_applies(st["ignores"].get(p["key"]), srv.id):
        return "ignored", source, latest
    if settings.hold_applies(st["pins"].get(p["key"]), srv.id):
        return "pinned", source, latest
    if not entry or not entry["source"] or entry.get("error"):
        return "unknown", source, latest
    return ("outdated" if entry["outdated"] else "current"), source, latest


def current_compat(p: dict, srv: inventory.Server, cache: dict) -> dict | None:
    """Compat of the *installed* jar (Modrinth only), and whether it lists this server's MC version."""
    entry = cache["entries"].get(cache_key(p["sha1"], srv.family, srv.mc_version))
    comp = ((entry or {}).get("current") or {}).get("compat")
    if not comp:
        return None
    ok = None if not srv.mc_version or srv.family == "velocity" else srv.mc_version in comp["mc_versions"]
    return {**comp, "supports_server": ok, "mc": srv.mc_version, "ok": ok}


def compat_for(compat: dict | None, srv: inventory.Server) -> dict | None:
    """Adds the server's MC version and whether the version supports it (None when unknown)."""
    if not compat:
        return None
    ok = None
    if srv.family == "velocity":
        ok = None if not compat["loaders"] else "velocity" in compat["loaders"]
    elif srv.mc_version and compat["mc_versions"]:
        ok = srv.mc_version in compat["mc_versions"]
    return {**compat, "mc": srv.mc_version, "ok": ok}


def public_latest(latest: dict | None) -> dict | None:
    if not latest:
        return None
    return {k: latest.get(k) for k in ("version", "url", "download_url", "published", "changelog_url",
                                       "type", "verified", "filename", "size", "compat")}


def pending_updates(servers: list[inventory.Server] | None = None,
                    plugins_by_server: dict[str, list[dict]] | None = None) -> list[dict]:
    """Available updates grouped by plugin key (excludes pinned/ignored installs)."""
    st = settings.load_raw()
    cache = load_cache()
    servers = servers if servers is not None else inventory.discover()
    out: dict[str, dict] = {}
    for srv in servers:
        plugins = plugins_by_server[srv.id] if plugins_by_server else inventory.list_plugins(srv)
        for p in plugins:
            status, source, latest = status_for(p, srv, st, cache)
            if status != "outdated" or not latest:
                continue
            u = out.setdefault(p["key"], {
                "key": p["key"], "name": p["name"], "from_versions": [], "to_version": latest["version"],
                "servers": [], "targets": [], "changelog_url": latest.get("changelog_url"),
                "download_url": latest.get("download_url"), "published": latest.get("published"),
                "source": source, "verified": latest.get("verified", False),
                "compat": latest.get("compat"), "size": latest.get("size"),
            })
            if srv.id in u["servers"]:
                continue  # duplicate jars of one plugin on a server count as one install
            if p["version"] not in u["from_versions"]:
                u["from_versions"].append(p["version"])
            u["servers"].append(srv.id)
            u["targets"].append({"server": srv.id, "jar": p["jar"], "from": p["version"], "to": latest["version"],
                                 "to_jar": latest.get("filename"), "compat": compat_for(latest.get("compat"), srv)})
    return sorted(out.values(), key=lambda u: u["name"].lower())


def update_counts(pending: list[dict]) -> dict:
    """The single definition of update counts used by /overview, /updates, /matrix and job summaries.

    plugins  = distinct plugin keys with at least one outdated install
    installs = outdated (server, plugin key) pairs; duplicate jars of one plugin on a server count once
    servers  = distinct servers with at least one outdated install
    Pinned/ignored installs are not outdated and never counted."""
    servers = {s for u in pending for s in u["servers"]}
    return {"plugins": len(pending), "installs": sum(len(u["servers"]) for u in pending), "servers": len(servers)}


# ---------------------------------------------------------------- plans (changesets)

def _parse_items(items: Any, pending: list[dict]) -> tuple[list[tuple[str, str]], list[dict]]:
    """Resolve {items} to outdated (key, server) pairs plus skipped entries with reasons."""
    by_key = {u["key"]: u for u in pending}
    if items == "all":
        return [(u["key"], s) for u in pending for s in u["servers"]], []
    if not isinstance(items, list) or not items:
        raise engine.DeployError("items must be 'all' or a non-empty list of {key, servers?}")
    known = inventory.servers_by_id()
    pairs: list[tuple[str, str]] = []
    skipped: list[dict] = []
    for it in items:
        if not isinstance(it, dict) or not isinstance(it.get("key"), str):
            raise engine.DeployError("each item needs a key")
        servers = it.get("servers")
        if servers is not None and (not isinstance(servers, list) or not all(isinstance(s, str) for s in servers)):
            raise engine.DeployError("servers must be a list of ids")
        for s in servers or []:
            if s not in known:
                raise engine.DeployError(f"unknown server: {s!r}")
        candidates = by_key[it["key"]]["servers"] if it["key"] in by_key else []
        for s in (candidates if servers is None else servers):
            if s in candidates:
                pairs.append((it["key"], s))
            else:
                skipped.append({"server": s, "key": it["key"], "reason": "no update available (current, pinned or ignored)"})
        if servers is None and not candidates:
            skipped.append({"server": None, "key": it["key"], "reason": "no update available"})
    return list(dict.fromkeys(pairs)), skipped


def create_plan(items: Any, user: str) -> dict:
    """Freeze exactly which jar on which server becomes which new jar. Stored server-side."""
    st = settings.load_raw()
    cache = load_cache()
    pending = pending_updates()
    pairs, skipped = _parse_items(items, pending)
    known = inventory.servers_by_id()
    rows = []
    for key, sid in pairs:
        srv = known[sid]
        for p in inventory.list_plugins(srv):
            if p["key"] != key:
                continue
            status, source, latest = status_for(p, srv, st, cache)
            if status != "outdated" or not latest:
                continue
            same_key = [q for q in inventory.list_plugins(srv) if q["key"] == key]
            rows.append({
                "row": len(rows), "server": sid, "key": key, "name": p["name"], "action": "update",
                "from_jar": p["jar"], "from_version": p["version"], "from_sha1": p["sha1"],
                "from_jars": sorted([q["jar"], inventory.file_hash(srv.plugins_dir / q["jar"])] for q in same_key),
                "also_removes": sorted(q["jar"] for q in same_key if q["jar"] != p["jar"]),
                "to_jar": _safe_filename(latest.get("filename"), f"{p['name']}-{latest['version']}"),
                "to_version": latest["version"], "compat": compat_for(latest.get("compat"), srv),
                "size": latest.get("size"), "published": latest.get("published"),
                "verified": bool(latest.get("verified")), "changelog_url": latest.get("changelog_url"),
                "type": latest.get("type"),
                "source": source, "_latest": latest,
            })
            break
    inventory.flush_cache()
    return public_plan(plans.create("updates", user, items=items, rows=rows, skipped=skipped))


def public_plan(plan: dict) -> dict:
    rows = [{k: v for k, v in r.items() if not k.startswith("_") and k not in ("from_sha1", "from_jars")}
            for r in plan["rows"]]
    servers = {r["server"] for r in rows}
    return {
        "plan_id": plan["plan_id"], "rows": rows, "skipped": plan["skipped"],
        "summary": {"rows": len(rows), "plugins": len({r["key"] for r in rows}), "servers": len(servers),
                    "download_bytes": sum(r["size"] or 0 for r in {r["to_jar"]: r for r in rows}.values()),
                    "unverified": sum(1 for r in rows if not r["verified"])},
        "created": plans.iso(plan["created"]), "expires": plans.iso(plan["expires"]),
        "applied_by": plan.get("applied_by"),
    }


def check_plan(plan: dict, exclude: Any = None) -> list[dict]:
    """Validate a stored update plan before applying it; returns the rows to apply.

    409 if expired, already applied, or any from_jar changed since planning."""
    plans.ensure_usable(plan)
    excl = set()
    if exclude is not None:
        if not isinstance(exclude, list) or not all(
                isinstance(e, list) and len(e) == 2 and all(isinstance(x, str) for x in e) for e in exclude):
            raise plans.PlanError(400, "exclude must be a list of [server, key] pairs")
        excl = {tuple(e) for e in exclude}
    rows = [r for r in plan["rows"] if (r["server"], r["key"]) not in excl]
    if not rows:
        raise plans.PlanError(400, "nothing to apply (all rows excluded)")
    conflicts = row_conflicts(rows)
    if conflicts:
        raise plans.PlanError(409, {"message": "servers changed since the plan was made; re-plan",
                                    "conflicts": conflicts})
    return rows


def row_conflicts(rows: list[dict]) -> list[dict]:
    known = inventory.servers_by_id()
    out = []
    for r in rows:
        srv = known.get(r["server"])
        if srv is None:
            out.append({"server": r["server"], "key": r["key"], "from_jar": r["from_jar"], "reason": "server gone"})
            continue
        path = srv.plugins_dir / r["from_jar"]
        if not path.is_file():
            out.append({"server": r["server"], "key": r["key"], "from_jar": r["from_jar"], "reason": "jar missing"})
            continue
        # Hash directly (not the size/mtime cache): a same-size copy with preserved mtime must not slip by.
        now = sorted([q["jar"], inventory.file_hash(srv.plugins_dir / q["jar"])]
                     for q in inventory.list_plugins(srv) if q["key"] == r["key"])
        planned = r.get("from_jars") or [[r["from_jar"], r["from_sha1"]]]
        if [r["from_jar"], inventory.file_hash(path)] not in now or \
                now != [list(x) for x in planned]:
            reason = "jar changed" if inventory.file_hash(path) != r["from_sha1"] else "other versions changed"
            out.append({"server": r["server"], "key": r["key"], "from_jar": r["from_jar"], "reason": reason})
    return out


# ---------------------------------------------------------------- apply

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._+\-]")


def _safe_filename(name: str | None, fallback: str) -> str:
    n = _SAFE_NAME.sub("_", (name or "").split("/")[-1]).lstrip(".")
    if not n.lower().endswith(".jar"):
        n = _SAFE_NAME.sub("_", fallback).lstrip(".") + ".jar"
    return n[:150]


def _download(c: httpx.Client, latest: dict, fallback_name: str, log) -> Path:
    url = latest.get("download_url")
    if not url or not url.startswith("https://"):
        raise ValueError("no direct https download available")
    hashes = _clean_hashes(latest.get("hashes"))
    if latest.get("verified") and not hashes:
        raise ValueError("source reported an invalid file hash")
    ident = hashes.get("sha512") or hashes.get("sha256") or hashes.get("sha1") or url
    fname = _safe_filename(latest.get("filename"), fallback_name)
    dest_dir = config.state("staging", hashlib.sha256(ident.encode()).hexdigest()[:40])
    dest = dest_dir / fname
    if dest.is_file() and _verify(dest, hashes):
        return dest
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / (fname + ".part")
    log(f"Downloading {url}")
    size = 0
    with c.stream("GET", url, headers={"Accept": "*/*"}) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                size += len(chunk)
                if size > MAX_DOWNLOAD:
                    raise ValueError("download exceeds size limit")
                f.write(chunk)
    expected = latest.get("size")
    if isinstance(expected, int) and expected > 0 and size != expected:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"size mismatch on downloaded file ({size} bytes, expected {expected})")
    if hashes and not _verify(tmp, hashes):
        tmp.unlink(missing_ok=True)
        raise ValueError("hash mismatch on downloaded file")
    tmp.replace(dest)
    return dest


def _verify(path: Path, hashes: dict) -> bool:
    for algo in ("sha512", "sha256", "sha1"):
        if hashes.get(algo):
            return inventory.file_hash(path, algo) == hashes[algo].lower()
    return True


def apply_rows(ctx: engine.Ctx, rows: list[dict], require_verified: bool = False) -> None:
    """Apply exactly the planned rows: each from_jar (same sha1 as planned) → the planned new jar."""
    known = inventory.servers_by_id()
    with _client() as c:
        for r in rows:
            sid, key, latest = r["server"], r["key"], r["_latest"]
            label = f"{r['name']} {r['from_version']} → {r['to_version']}"
            ctx.log(f"==> {sid}: {label}")
            srv = known.get(sid)
            if srv is None:
                ctx.result(sid, label, "update", "error", "server no longer exists")
                continue
            if row_conflicts([r]):
                ctx.result(sid, label, "update", "error", f"{r['from_jar']} changed since the plan; not applied")
                continue
            if require_verified and not latest.get("verified"):
                ctx.result(sid, label, "update", "skipped", "source provides no hash; not auto-applied")
                continue
            try:
                staged = _download(c, latest, f"{r['name']}-{r['to_version']}", ctx.log)
            except (httpx.HTTPError, ValueError, OSError) as e:
                ctx.result(sid, label, "update", "error", f"download failed: {e}")
                continue
            if not latest.get("verified"):
                ctx.log("  warning: source provides no hash; installing unverified jar")
            ctx.guard(sid, label, "update", engine.replace_jar, ctx, srv, staged, install=False, label=label,
                      expect_key=key, action="update")
