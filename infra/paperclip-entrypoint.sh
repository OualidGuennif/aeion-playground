#!/bin/sh
set -eu
# These files are scoped only to the trusted workboard, never a model/test worker.
postgres_password="$(cat /run/secrets/postgres_password)"
export DATABASE_URL="postgres://paperclip:${postgres_password}@postgres:5432/paperclip"
export BETTER_AUTH_SECRET="$(cat /run/secrets/paperclip_auth)"
export PAPERCLIP_TOOL_ACTION_SIGNING_SECRET="$(cat /run/secrets/paperclip_actions)"
unset postgres_password
exec docker-entrypoint.sh node server/dist-runtime/index.mjs
