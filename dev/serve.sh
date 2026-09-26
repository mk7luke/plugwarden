#!/usr/bin/env bash
# Dev server against a SANDBOX copy of the datastore. Never point it at your live datastore.
# Auth is off (LGT_AUTH=none), which the app only allows on a loopback bind.
set -euo pipefail
cd "$(dirname "$0")/.."
SB="${LGT_SANDBOX:-$PWD/sandbox}"   # a COPY of your datastore: sandbox/base/<Instance>/Minecraft/plugins, sandbox/state/
export LGT_BASE="$SB/base" LGT_BASE_OVERRIDE="$SB/base" LGT_STATE_DIR="$SB/state"
export LGT_AUTH=none LGT_BIND=127.0.0.1
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-18095}" "$@"
