#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PORT="${PORT:-8124}"

if [ ! -x .venv/bin/uvicorn ]; then
  echo "Creating the local virtual environment…"
  python3.12 -m venv .venv
  .venv/bin/python -m pip install --require-hashes -r requirements.lock
fi

echo "Æion Mission Control  →  http://127.0.0.1:${PORT}"
UVICORN_ARGS=(app.main:app --host 127.0.0.1 --port "${PORT}" --no-access-log)
if [[ "${AEION_RELOAD:-0}" == "1" ]]; then
  UVICORN_ARGS+=(--reload --reload-dir app)
fi
exec .venv/bin/uvicorn "${UVICORN_ARGS[@]}"
