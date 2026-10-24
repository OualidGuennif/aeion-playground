#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PORT="${PORT:-8123}"

if [ ! -x .venv/bin/uvicorn ]; then
  echo "Creating the local virtual environment…"
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/pip install -r requirements.txt
fi

echo "Æion Mission Control  →  http://127.0.0.1:${PORT}"
UVICORN_ARGS=(app.main:app --host 127.0.0.1 --port "${PORT}" --no-access-log)
if [[ "${AEION_RELOAD:-0}" == "1" ]]; then
  UVICORN_ARGS+=(--reload --reload-dir app)
fi
exec .venv/bin/uvicorn "${UVICORN_ARGS[@]}"
