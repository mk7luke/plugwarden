#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export LGT_STATE_DIR="${LGT_STATE_DIR:-/var/lib/lgt-amp-sync}"
# Loopback by default: cloudflared on this host reaches it; nothing else can. See dev/DEPLOY_NOTES.md.
export LGT_BIND="${LGT_BIND:-127.0.0.1}"
exec uvicorn app.main:app --host "$LGT_BIND" --port "${LGT_PORT:-8078}" --proxy-headers --forwarded-allow-ips 127.0.0.1
