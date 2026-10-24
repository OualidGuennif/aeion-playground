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

Until an operator supplies a scoped `PAPERCLIP_API_TOKEN` in local configuration, the
factory reports authenticated API operations as unavailable/denied. Health alone does
not establish company/task access. Never copy a browser cookie into model context or Git.
An authenticated workboard account/token is separate from a Gate approval to merge code.

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
