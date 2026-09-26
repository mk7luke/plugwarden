#!/usr/bin/env bash
# Dev server against a SANDBOX copy of the datastore. Never points at /mnt/storage_ssd.
# Auth is off (LGT_AUTH=none), which the app only allows on a loopback bind.
set -euo pipefail
cd "$(dirname "$0")/.."
SB="${LGT_SANDBOX:-/tmp/claude-1000/-home-luke-lgt-amp-sync/0cf2f751-d945-45e5-89cc-a0b56688015d/scratchpad/sandbox}"
export LGT_BASE="$SB/base" LGT_BASE_OVERRIDE="$SB/base" LGT_STATE_DIR="$SB/state"
export LGT_AUTH=none LGT_BIND=127.0.0.1
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8095}" "$@"
