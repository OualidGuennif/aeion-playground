# Local service stack

Requires Docker Engine 28+ (or current Docker Desktop), Python 3.12 and the locked venv.
`scripts/prepare_stack.py` generates separate owner-only local secret files once;
subsequent runs preserve them. Never rotate signing secrets as a startup side effect.
The secrets, provider state and named-volume contents are excluded from Git/build contexts.

```sh
.venv/bin/python scripts/prepare_stack.py
docker compose up -d --wait --wait-timeout 180 postgres paperclip
docker compose ps
```

Paperclip listens through **127.0.0.1:3101**. PostgreSQL has no published port. The
accepted native demo can keep ports 8123/3100; the new controller defaults to 8124.
The images are pinned by digest. Both services have read-only roots, bounded scratch,
logging, CPU/RAM/PIDs, health checks and persistent named volumes. Trusted official
entrypoints use a limited capability set to initialize permissions, then drop to their
application users. These are trusted services, not the boundary for untrusted model code.
Only Paperclip joins the control network for loopback port publication; the database
network is internal. Model workers do not join either network.

Paperclip runs in **authenticated/private** mode. Its official `local_trusted` mode
requires a loopback bind and cannot be used for an ordinary container port mapping.
Create a real browser account and choose **Claim this instance**. No factory process
claims human ownership. Keep this first-claim setup on the local host. See the
[upstream bootstrap contract](https://github.com/paperclipai/paperclip/blob/412914cfa78d5abefcc34d3eb8d4bb86e461caac/doc/DEPLOYMENT-MODES.md).

Paperclip listens on **3101 inside the container as well as on the host**. The pinned
[upstream server](https://github.com/paperclipai/paperclip/blob/412914cfa78d5abefcc34d3eb8d4bb86e461caac/server/src/index.ts#L721)
rewrites loopback authentication URLs to its actual listening port,
even in explicit URL mode. Mapping host 3101 to container 3100 therefore caused
**Invalid origin**; changing the public URL or mode alone did not fix it. Keep the
listening port, published port and canonical URL aligned when changing this setup.
Qualification checks a same-origin signup with a deliberately too-short password
(no account created) and confirms a foreign origin remains rejected. Use the canonical
`http://127.0.0.1:3101` address when registering or signing in.

Health alone does not establish company/task access. Until the board grant exists, the
factory reports authenticated API operations as unavailable/denied. Never copy a browser
cookie into model context or Git. An authenticated workboard account/token is separate
from a Gate approval to merge code.

The supported local connection flow avoids copying a token manually:

```sh
.venv/bin/python scripts/connect_paperclip.py
```

This uses Paperclip's [board CLI authorization protocol](https://github.com/paperclipai/paperclip/blob/412914cfa78d5abefcc34d3eb8d4bb86e461caac/cli/src/client/board-auth.ts#L208).
It opens the short-lived pairing page directly in your browser, requests board access
(not instance administration), and waits at most five minutes for your real account to
approve. The application never calls the approval endpoint. Only after approval and an
authenticated identity check does it save the board credential in an owner-only file at
`runtime/secrets/paperclip-board-auth.json`.

For the containerised cockpit, the same command also derives
`runtime/secrets/paperclip_api_token` (mode `0600`). The cockpit mounts that file
read-only at `/run/secrets/paperclip_api_token` and reads it through
`PAPERCLIP_API_TOKEN_FILE`; the bearer is not an environment variable, is not visible in
`docker inspect`, and is never printed. Recreate the cockpit after the first connection:

```sh
docker compose up -d --wait cockpit
```

`PAPERCLIP_API_TOKEN` remains available for a native controller, but it is not needed by
the Docker stack. `PAPERCLIP_AUTH_STORE` can select a separate private board profile; the
board credential is bound to its exact loopback API origin, while the derived container
token is used against the Compose service name. The controller disables environment
proxies and redirects for workboard requests. The derived file is rewritten in place so
its inode survives a rotation; recreate the cockpit after creating it for the first time
(or after replacing it manually).

Measured on 2026-10-25 after the mount was added: the cockpit's Paperclip component is
`ready` and authenticated, `PD-037` created **AEI-2**, and the second free baseline
`PD-038` created **AEI-3** with `http://127.0.0.1:3101/AEI/issues/AEI-3`. Both were
`PD00` drills with no model call. See [`live-inference.md`](live-inference.md#paperclip-workboard-link-2026-10-25).

## Cockpit and executor agent

The cockpit runs as a container with no Docker socket, no host home and no provider
credentials. It asks a host agent for the only operations that need Docker: running the
disposable test worker and the author/reviewer roles.

```sh
.venv/bin/python scripts/prepare_stack.py          # creates runtime/secrets/executor_agent
.venv/bin/python -m app.executor_agent --bind 127.0.0.1 --port 8131 &
AEION_OPERATOR_UID=$(id -u) AEION_OPERATOR_GID=$(id -g) \
  docker compose up -d --wait postgres paperclip otel-collector inference-gateway cockpit
```

The cockpit is published on **127.0.0.1:8124**. The token is mounted read-only at
`/run/secrets/executor_agent`; `AEION_EXECUTOR_URL` defaults to
`http://host.docker.internal:8131`, which Docker Desktop forwards to the host loopback.
On Linux, start the agent with `--bind 172.17.0.1` and set
`AEION_EXECUTOR_URL=http://172.17.0.1:8131` for Compose.

Verify the boundary from the running container rather than from the Compose file:

```sh
docker compose exec -T cockpit sh -c 'test ! -e /var/run/docker.sock && echo "no socket"'
docker inspect aeion-playground-cockpit-1 --format '{{json .HostConfig.Binds}}'
```

The cockpit keeps its ledger, sandbox checkouts and evidence packets in
`runtime/cockpit/` (owner-only, a bind mount). Back it up with the other runtime state; it
is not the same database as a native run. The executor token is a read-only bind mount of
`runtime/secrets/executor_agent`; the cockpit runs as the host operator's uid. The agent is trusted and can launch containers: run it as the
operator, keep its token private and treat its logs as operator data. It is not a sandbox.

Paperclip is the workboard in this setup. The Factory owns model routing, disposable
author/reviewer execution, tests, Gate and proofs. Paperclip's generic first-agent/provider
wizard is for its own execution engine; it is not required to configure Factory model
access. The existing **ParcelDesk** organization is reused, including its returned issue
prefix. A browser authorization grant does not approve a model-generated code change.

```sh
# Opt-in: restarts ONLY this Compose project's services, creates/restores throwaway DBs.
AEION_STACK_TESTS=1 .venv/bin/python -m pytest -q tests/test_compose_stack.py
# Stop while preserving data and secrets:
docker compose down
```

Qualification exercises the actual HTTP health/UI, refusal of unauthenticated company
access, runtime mount/resource settings, persistence across restart, and a real PostgreSQL
custom-format dump/restore into a separate database. It does not create a fake operator,
rebuild old proof, or establish complete disaster recovery for application assets.

Back up **both named volumes, `runtime/cockpit/` and `runtime/secrets`** to private owner-controlled storage.
PostgreSQL requires a consistent `pg_dump`/`pg_restore` (or an offline data-volume backup),
not a live filesystem copy. Restore into a separate stack first and verify account,
company, issue links and application assets before replacing an installation. A missing
recent database backup appears explicitly in Paperclip health. Automatic off-host backup
and a complete application-assets restore drill remain operator qualification work.

`docker compose down --volumes` deletes this stack's database/application volumes. Use it
only for a disposable CI instance or intentional reset; it is never a normal upgrade step.
