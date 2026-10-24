#!/usr/bin/env bash
# OpenCode credential setup for the Æion ParcelDesk POC.
#
# Usage:
#   scripts/opencode_auth.sh        # interactive /connect (OAuth or API key)
#   scripts/opencode_auth.sh list   # show stored credentials
#
# Credentials are stored under runtime/opencode/data/opencode/auth.json.
# They never enter the mission sandbox and are never written to the ledger.
set -euo pipefail

POC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$HOME/.nvm/versions/node/v24.22.1/bin:$PATH"
export XDG_DATA_HOME="$POC_DIR/runtime/opencode/data"
export XDG_CONFIG_HOME="$POC_DIR/runtime/opencode/config"
export XDG_STATE_HOME="$POC_DIR/runtime/opencode/state"
export XDG_CACHE_HOME="$POC_DIR/runtime/opencode/cache"
mkdir -p "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" "$XDG_STATE_HOME" "$XDG_CACHE_HOME"

BIN="$POC_DIR/vendor/opencode/node_modules/.bin/opencode"
if [[ ! -x "$BIN" ]]; then
  echo "OpenCode binary not found: $BIN" >&2
  echo "Install it with: npm install --prefix vendor/opencode opencode-ai@1.19.4" >&2
  exit 1
fi

if [[ "${1:-}" == "list" ]]; then
  exec "$BIN" auth list
fi

exec "$BIN" auth login "$@"
