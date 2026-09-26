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


def init(base: str | None = None, state_dir: str | None = None, create: bool = True) -> None:
    """(Re)load configuration. The base path only ever comes from env (or tests), never from clients."""
    global BASE, BASE_OVERRIDDEN, STATE_DIR
    override = os.environ.get("LGT_BASE_OVERRIDE", "").strip()
    BASE_OVERRIDDEN = bool(override)
    BASE = Path(base or override or os.environ.get("LGT_BASE", "").strip() or DEFAULT_BASE)
    STATE_DIR = Path(state_dir or os.environ.get("LGT_STATE_DIR", "").strip() or "/var/lib/lgt-amp-sync")
    if create:
        ensure_dirs()


def ensure_dirs() -> None:
    for sub in ("jobs", "uploads", "backups", "staging", "cache", "plans"):
        (STATE_DIR / sub).mkdir(parents=True, exist_ok=True)


def state(*parts: str) -> Path:
    return STATE_DIR.joinpath(*parts)


init(create=False)
