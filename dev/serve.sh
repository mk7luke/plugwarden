#!/usr/bin/env bash
# Dev server against a SANDBOX copy of the datastore. Never points at /mnt/storage_ssd.
# Auth is off (LGT_AUTH=none), which the app only allows on a loopback bind.
set -euo pipefail
cd "$(dirname "$0")/.."
SB="${LGT_SANDBOX:-/tmp/claude-1000/-home-luke-lgt-amp-sync/0cf2f751-d945-45e5-89cc-a0b56688015d/scratchpad/sandbox}"
export LGT_BASE="$SB/base" LGT_BASE_OVERRIDE="$SB/base" LGT_STATE_DIR="$SB/state"
export LGT_AUTH=none LGT_BIND=127.0.0.1
# AMP (optional):
#   LGT_AMP_MOCK=1  → start dev/mock_amp.py on 127.0.0.1:${AMP_MOCK_PORT:-18100} (writes fake startup logs into the sandbox)
#   LGT_AMP_REAL=1  → read the real ADS with credentials from .env.amp, READ-ONLY (no power actions/commands)
if [[ "${LGT_AMP_MOCK:-}" == "1" ]]; then
  MP="${AMP_MOCK_PORT:-18100}"
  if ! ss -lnt | grep -q "127.0.0.1:$MP "; then
    setsid .venv/bin/python dev/mock_amp.py --port "$MP" --log-root "$SB/base" > "$SB/../mock-amp.log" 2>&1 < /dev/null &
    sleep 1
  fi
  export LGT_AMP_URL="http://127.0.0.1:$MP" LGT_AMP_USER=admin LGT_AMP_PASSWORD=mock-password
elif [[ "${LGT_AMP_REAL:-}" == "1" && -f .env.amp ]]; then
  set -a; . ./.env.amp; set +a
  export LGT_AMP_URL="${LGT_AMP_URL:-http://127.0.0.1:8080}" LGT_AMP_READONLY=1
fi
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-18095}" "$@"
