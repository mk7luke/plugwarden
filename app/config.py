"""Runtime configuration. Values are module attributes read at call time so tests can re-init."""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "LGT AMP Sync"
VERSION = "2.0"
USER_AGENT = "lgt-amp-sync/2.0 (luke@interactep.com)"
REL_PLUGINS = Path("Minecraft/plugins")
DEFAULT_BASE = "/mnt/storage_ssd/ssd-live"
USER_HEADER = "cf-access-authenticated-user-email"
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

BASE: Path = Path(DEFAULT_BASE)
BASE_OVERRIDDEN: bool = False
STATE_DIR: Path = Path("/var/lib/lgt-amp-sync")

# Security (see app/auth.py and dev/DEPLOY_NOTES.md)
BIND = "127.0.0.1"
AUTH_MODE = "none"
CF_TEAM_DOMAIN = ""
CF_AUD = ""
ALLOWED_HOSTS: set[str] = {"localhost", "127.0.0.1"}
HOSTNAME = ""
ALLOW_INSECURE = False  # LGT_AUTH_ALLOW_INSECURE=1: auth "none" on a non-loopback bind (dev container)
DOCS_ENABLED = False
MIN_FREE_BYTES = 2 * 1024 ** 3


def init(base: str | None = None, state_dir: str | None = None, create: bool = True) -> None:
    """(Re)load configuration. The base path only ever comes from env (or tests), never from clients."""
    global BASE, BASE_OVERRIDDEN, STATE_DIR
    override = os.environ.get("LGT_BASE_OVERRIDE", "").strip()
    BASE_OVERRIDDEN = bool(override)
    BASE = Path(base or override or os.environ.get("LGT_BASE", "").strip() or DEFAULT_BASE)
    STATE_DIR = Path(state_dir or os.environ.get("LGT_STATE_DIR", "").strip() or "/var/lib/lgt-amp-sync")
    global BIND, AUTH_MODE, CF_TEAM_DOMAIN, CF_AUD, ALLOWED_HOSTS, DOCS_ENABLED, MIN_FREE_BYTES, HOSTNAME
    global ALLOW_INSECURE
    ALLOW_INSECURE = os.environ.get("LGT_AUTH_ALLOW_INSECURE", "").strip() == "1"
    BIND = os.environ.get("LGT_BIND", "127.0.0.1").strip() or "127.0.0.1"
    CF_TEAM_DOMAIN = os.environ.get("LGT_CF_TEAM_DOMAIN", "").strip().removeprefix("https://").rstrip("/")
    CF_AUD = os.environ.get("LGT_CF_AUD", "").strip()
    AUTH_MODE = os.environ.get("LGT_AUTH", "").strip() or ("cf-access" if CF_AUD else "none")
    hosts = {"localhost", "127.0.0.1"}
    HOSTNAME = os.environ.get("LGT_HOSTNAME", "").strip().lower()
    if HOSTNAME:
        hosts.add(HOSTNAME)
    hosts |= {h.strip().lower() for h in os.environ.get("LGT_ALLOWED_HOSTS", "").split(",") if h.strip()}
    ALLOWED_HOSTS = hosts
    DOCS_ENABLED = os.environ.get("LGT_DOCS", "").strip() == "1"
    MIN_FREE_BYTES = int(float(os.environ.get("LGT_MIN_FREE_GB", "2")) * 1024 ** 3)
    if create:
        ensure_dirs()


def ensure_dirs() -> None:
    for sub in ("jobs", "uploads", "backups", "staging", "cache", "plans"):
        (STATE_DIR / sub).mkdir(parents=True, exist_ok=True)


def state(*parts: str) -> Path:
    return STATE_DIR.joinpath(*parts)


init(create=False)
