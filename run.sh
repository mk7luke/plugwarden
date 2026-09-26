#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export LGT_STATE_DIR="${LGT_STATE_DIR:-/var/lib/lgt-amp-sync}"
exec uvicorn app.main:app --host 0.0.0.0 --port 8078
