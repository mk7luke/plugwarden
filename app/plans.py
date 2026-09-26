"""Server-side plans (changesets): what a preview showed is exactly what gets applied."""
from __future__ import annotations

import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config
from .storage import read_json, write_json

TTL_SECONDS = 30 * 60
PLAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")
lock = threading.Lock()  # guards check-and-consume


class PlanError(Exception):
    """status is the HTTP status the API should return; detail is JSON-serialisable."""

    def __init__(self, status: int, detail: Any):
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


def _file(plan_id: str) -> Path:
    return config.state("plans", f"{plan_id}.json")


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def create(kind: str, user: str, **payload) -> dict:
    now = time.time()
    plan = {"plan_id": secrets.token_hex(8), "kind": kind, "user": user, "created": now,
            "expires": now + TTL_SECONDS, "applied_by": None, **payload}
    save(plan)
    prune()
    return plan


def save(plan: dict) -> None:
    write_json(_file(plan["plan_id"]), plan)


def load(plan_id: Any, kind: str) -> dict:
    if not isinstance(plan_id, str) or not PLAN_ID_RE.match(plan_id):
        raise PlanError(400, "plan_id is required (create one with the matching /plan endpoint)")
    plan = read_json(_file(plan_id))
    if not plan or plan.get("kind") != kind:
        raise PlanError(404, "unknown plan")
    return plan


def ensure_usable(plan: dict) -> None:
    if time.time() > plan["expires"]:
        raise PlanError(409, {"message": "plan expired; create a new plan", "conflicts": []})
    if plan.get("applied_by"):
        raise PlanError(409, {"message": f"plan already applied by job {plan['applied_by']}", "conflicts": []})


def consume(plan: dict, job_id: str) -> None:
    plan["applied_by"] = job_id
    save(plan)


def prune(max_age: float = 24 * 3600) -> None:
    cutoff = time.time() - max_age
    for f in config.state("plans").glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass
