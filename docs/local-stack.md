# Local service stack

Requires Docker Engine 28+ (or current Docker Desktop), Python 3.12 and the locked venv.
`scripts/prepare_stack.py` generates three separate owner-only local secret files once;
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

Until an operator supplies a scoped `PAPERCLIP_API_TOKEN` in local configuration, the
factory reports authenticated API operations as unavailable/denied. Health alone does
not establish company/task access. Never copy a browser cookie into model context or Git.
An authenticated workboard account/token is separate from a Gate approval to merge code.

The supported local connection flow also avoids copying a token manually:

```sh
.venv/bin/python scripts/connect_paperclip.py
```

This uses Paperclip's [board CLI authorization protocol](https://github.com/paperclipai/paperclip/blob/412914cfa78d5abefcc34d3eb8d4bb86e461caac/cli/src/client/board-auth.ts#L208).
It opens the short-lived pairing page directly in your browser, requests board access
(not instance administration), and waits at most five minutes for your real account to
approve. The application never calls the approval endpoint. Only after approval and an
authenticated identity check does it save the board credential in an owner-only file at
`runtime/secrets/paperclip-board-auth.json`. No API key is printed or copied into Git,
model context, worker environment or Docker image. `PAPERCLIP_AUTH_STORE` can select a
separate private profile; the credential is bound to its exact loopback API origin.
The controller disables environment proxies and redirects for workboard requests.

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

Back up **both named volumes and runtime/secrets** to private owner-controlled storage.
PostgreSQL requires a consistent `pg_dump`/`pg_restore` (or an offline data-volume backup),
not a live filesystem copy. Restore into a separate stack first and verify account,
company, issue links and application assets before replacing an installation. A missing
recent database backup appears explicitly in Paperclip health. Automatic off-host backup
and a complete application-assets restore drill remain operator qualification work.

`docker compose down --volumes` deletes this stack's database/application volumes. Use it
only for a disposable CI instance or intentional reset; it is never a normal upgrade step.
