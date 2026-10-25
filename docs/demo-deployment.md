# Running the demo in containers

Status: **executor boundary and Compose cockpit implemented, live inference exercised.**
The `ExecutorClient` interface, the host agent (`probe`, `run_tests`, `run_role`), the
cockpit image and Compose service exist. A cockpit container ran PD00 and PD03 end to end
through the agent, with the worker's real Docker flags. A full live PD01 mission and a
bounded Factory ticket then ran through the containerised cockpit, host agent and paid
models; the ticket published a local Git release. A same-host backup/restore drill and the
agent-kill/orphan drill have also passed. A fresh-host install and an off-host restore are
still missing; each remaining item is listed below and none is implied by a configuration
file.

## The problem

The cockpit (Mission Control + ParcelDesk) currently runs as a native process on the
operator's machine. The demo is therefore only reproducible on macOS, on that machine, and
the operator's host is part of the trust boundary. We want the demo to run under Compose,
on any host with a Docker engine, without weakening the boundaries stated in
[`SECURITY.md`](../SECURITY.md).

The difficulty is that the trusted controller **launches containers**: the test executor
and the two inference roles. A container that launches containers needs Docker access.

## Options considered

### A. Mount the Docker socket into the cockpit container

Rejected. The socket is root-equivalent: anything that can write to it can start a
privileged container with a host bind mount. `SECURITY.md` states that no container
receives a Docker socket, and the inference roles are explicitly untrusted. A single
template-injection bug in the cockpit would become host compromise.

### B. Ship the cockpit read-only, keep execution native

Rejected as a *goal*, kept as a *fallback*. It makes the demo viewable anywhere but the
mission still needs the operator's host, which is exactly what we are trying to remove.

### C. Host executor agent + narrow remote API — chosen

Split the system at the boundary that already exists in the code:

```
┌──────────────────────────── Compose ────────────────────────────┐
│  cockpit (untrusted-ish)   workboard   postgres   collector     │
└───────────────┬─────────────────────────────────────────────────┘
                │ loopback or private bridge, capability token
┌───────────────▼─────────────────────────────────────────────────┐
│  executor agent  (TRUSTED, runs on the host, outside Compose)    │
│  owns Docker access; never receives a caller path or command    │
└───────────────┬─────────────────────────────────────────────────┘
                │ docker run --rm, no socket, no host home
┌───────────────▼─────────────────────────────────────────────────┐
│  disposable test worker      ·      author / reviewer roles      │
└─────────────────────────────────────────────────────────────────┘
```

The cockpit never sees a socket. It asks the executor agent to perform one of a small set
of **allowlisted operations**, each with its own request shape:

| Operation | Request | Response |
|---|---|---|
| `run_tests` | content-addressed snapshot digest, timeout, expected executor identity | bounded test result, host paths redacted |
| `run_role` | bounded role parameters and prompt material, no working directory | bounded agent result, no host paths |
| `probe` | none | executor identity, image ids, limits |

Implemented properties:

- The agent binds a loopback or private address (validated at startup; `0.0.0.0`, public
  addresses and hostnames are refused) and requires a 64-hex capability token from a
  regular, operator-owned, mode-0600 file.
- The agent does not accept a command, a path or an image from the caller. `run_tests`
  accepts a `sha256:` digest; the snapshot must have been uploaded first, its digest is
  recomputed on every read, and extraction uses `tarfile`'s `data` filter. An unknown
  digest is refused before a worker starts.
- `run_role` accepts only the `forge` and `sentinel` agent identities, only the text-only
  patch protocol, and never a `cwd`; it creates its own scratch directory.
- Every operation is bounded: request bytes, snapshot bytes (compressed and unpacked),
  entry count, timeout, response size. No streaming of unbounded data.
- Responses redact agent-local paths. The raw JUnit XML and the role event streams stay on
  the agent; the cockpit receives the bounded parsed report and the role summary.
- The cockpit does not receive a Docker socket, a host home or provider credentials.

## What this does *not* solve

- The host agent is still privileged. It is a smaller attack surface, not a safe one. It
  must run as the operator, with its own hardening and monitored logs.
- A compromised host agent can start any container. That risk is inherent to "run Docker
  on this machine" and must be stated, not hidden.
- **Budget and ledger authority stay in the cockpit.** The agent does not hold the budget
  reservation, does not sign evidence and does not own the SQLite ledger. A compromised
  cockpit can therefore undermine budget accounting and can build a packet containing
  values the agent never produced. Evidence is digest-sealed, not key-signed. What the
  cockpit cannot do is launch a container, read the agent's raw reports or obtain provider
  credentials.
- The agent's local logs are not tamper-evident. They are operator-readable bounded files,
  not a second ledger.
- Multi-tenant hosting is out of scope: one operator, one host, one ledger.

## Acceptance status

| # | Test | Status |
|---|---|---|
| 1 | cockpit container has no socket, host home or credentials, asserted from `docker inspect` | covered by the opt-in `tests/test_compose_stack.py` |
| 2 | `run_tests` with an unprepared digest is refused and nothing is launched | covered by `tests/test_executor_agent.py` (fake workspace) |
| 3 | missing/wrong token refused; a world-readable host token is refused at startup | covered by `tests/test_executor_agent.py` |
| 4 | worker launched by the agent has `--network none`, read-only root, non-root, dropped caps, documented limits | covered by the opt-in `tests/test_docker_isolation.py`; the hosted `docker-tests` job builds the worker image and runs it with `AEION_DOCKER_TESTS=1` |
| 5 | killing the agent mid-run records a blocked mission without replay and leaves no orphan | measured 2026-10-25: `PD-007` blocked `EXECUTION_FAILED` in the same second (0 runs, 0 cost, one `mission.started`), still blocked after the restart; the worker stayed running and the restart logged `removed 1 container(s) left by a previous run`; opt-in `tests/test_agent_kill.py` repeats the kill/reap half |
| 6 | cockpit serves Mission Control and ParcelDesk with the agent stopped, reporting `executor unavailable` | covered by the hosted `stack-tests` job: it starts the cockpit with no host agent and checks `/`, `/parceldesk`, `/warehouse` and `/api/health` |
| 7 | backup and restore of the cockpit state directory on the same host and image | measured 2026-10-25: 228,094-byte archive, SQLite integrity ok, 6 missions / 8 attempts / 3 tickets restored, PD-006 packet valid, `product_head` unchanged; off-host restore not tested |
| 8 | fresh-host install and first mission | **not tested** |

The unit tests use a fake workspace and a fake role runner. They prove the protocol and
the refusals; they do not prove container isolation. What was measured with real
containers: the cockpit image boots healthy under Compose, serves `/` and `/parceldesk`,
reads the remote inference probe, and ran PD00 and PD03 through the agent. PD03's worker
command showed `--network none`, `--read-only`, `--user 65532:65532`, `--cap-drop ALL`,
the memory/CPU/PID limits and no Docker socket. PD00 recorded the baseline `12 passed /
7 failed` and Gate `NO_PATCH`; PD03 recorded `19 passed` and Gate `PROTECTED_PATH`.

### Backup and restore drill — 2026-10-25

Measured on the live stack, same host and same image:

1. Captured the state before touching it: 6 missions, 81 events, 8 attempts, 3 tickets
   (0 open), the PD-006 packet valid, `product_head` at `fc6ca0d`.
2. Stopped the cockpit. With no process attached, checkpointed the WAL and ran
   `PRAGMA integrity_check` on `aeion.db` and `parceldesk.db`; both returned `ok`.
3. Created `runtime/backups/cockpit-20261025T010347Z.tgz` (228,094 bytes, mode 0600) with
   `tar -czf ... -C runtime cockpit`. The archive stays local and gitignored.
4. Extracted it to a temporary directory and checked the copy: integrity ok, 6 missions,
   81 events, 8 attempts, 3 tickets, 0 open; the PD-006 packet still verified.
5. Moved the live `runtime/cockpit` aside, extracted the archive in place, and restarted
   the Compose stack. `/api/health` reported ok, `/warehouse` returned 200, the counts
   matched (82 events: the restarted engine adds one `engine.started`), the PD-006 packet
   verified, `product_head` was still `fc6ca0d`, and `open_tickets` was 0.
6. Removed the pre-restore copy; the archive remains as the rollback artifact.

Limits: this is a same-host, same-image restore, not a fresh-host install or an off-host
backup. It does not simulate disk corruption. The archive contains `runtime/cockpit` only;
`runtime/secrets/executor_agent` and the gateway credential are separate files that a new
host must provision. The archive includes the local ledger, including the bound model
outputs stored in `agent_runs`, so it must stay outside Git and off shared storage.

### Agent-kill drill — 2026-10-25

Measured on the live stack, with no model call and no provider credential:

1. Added one temporary sleeping test to the baked sandbox, rebuilt the cockpit and launched
   `PD03` (`request_id qual-agent-kill-20261025T012706Z`) as `PD-007`.
2. At `01:27:11Z`, while its worker container had been up for three seconds, killed the host
   executor agent (`SIGKILL`, pid 87613) and its Docker client.
3. The mission blocked in the same second: `EXECUTION_FAILED`,
   `ExecutorUnavailable: Executor agent unavailable: Remote end closed connection without
   response`; no model run, no token, no cost. Its events hold exactly one
   `mission.started` and one `mission.blocked`.
4. The worker (`aeion-test-2cf3fca9bace46429d62104f99981442`, id `fa525fab2d85`) stayed
   running: `--rm` only removes a container at exit, and the killed agent's cleanup never
   ran.
5. Restarted the agent (pid 94383) on the same state directory. Before serving, it listed
   only its own owner label and logged `removed 1 container(s) left by a previous run`; the
   worker was removed while its 30-second test was still running. The restart landed just
   before that deadline; the opt-in test below repeats the reap with a strict 10-second
   bound.
6. Waited, then re-read the mission: still `blocked` / `EXECUTION_FAILED`, one
   `mission.started`, zero runs. No automatic replay.
7. Removed the temporary test, rebuilt, and verified `template_digest` returned to
   `c3ce129af915ca90d11e68daf55a605c7032bafb811a8767bbf089105c2e3c02` (the PD-006 value),
   `/warehouse` returned 200 and `open_tickets` was 0.

The hosted Docker job that repeats the drill exposed a second defect: the agent's
`umask 077` left the worker snapshot directories at 0700, so the container's uid 65532
could not traverse `/work` on Linux (`Permission denied: '/work'`). Docker Desktop masked
it on macOS. Snapshot directories are now normalized to 0755 while files stay 0444, with a
unit test that builds the snapshot under the same umask.

The ledger records `operator.agent_kill_drill` on `PD-007` with the container identity, both
agent pids, zero model calls and the reap count. `PD-007` stays blocked in the demo
history as a drill: it is not an acceptance, has no sealed packet and publishes nothing.

Limits: the killed container was a test worker, not a paid role. Role containers carry the
same owner label and take the same cleanup path, but that path was not exercised with a
live model. Reaping runs only at agent start: while the agent is down, the orphan survives.
A killed agent can leave owner-only scratch directories under
`runtime/executor-agent/{roles,runs}`, which are not pruned. The opt-in
`tests/test_agent_kill.py` (`AEION_DOCKER_TESTS=1`) repeats the kill/reap half on a
throwaway agent and worker, without the live cockpit or a model.

## ParcelDesk product services

`parceldesk-api` (Quarkus/Hibernate/H2, Java 25.0.4.1) and `parceldesk-web` (Deno 2.5,
Fresh, Three.js) are separate Compose services built from `sandbox/parceldesk/product`. They
have the same read-only, capability-free, socket-free posture as the cockpit and publish only
on loopback (`8081` and `8125`). The web reaches the cockpit through its server-side proxy;
the browser never needs a cross-origin call.

The trusted worker image is now polyglot: Python, Maven/JDK 25 with an offline `.m2`, and
Deno with a pre-populated cache. A mission carries `test_profile=legacy` (Python contract) or
`test_profile=product` (Maven and Deno suites); the Gate acceptance and local delivery use
the same profile. The product release advances `product_head:product`; the legacy Python
release stays on `product_head`.

The JVM suites need larger bounds than the original Python worker: **1 GiB memory, 1.5 CPU,
256 PIDs, 512 MiB scratch and 4096 open files**. The opt-in
`tests/test_product_worker.py` runs the product profile with those limits; the local live
PD-010 run used them for 13 worker tests and 12 Gate-owned tests. See
[`parceldesk-product.md`](parceldesk-product.md) for the API, the UI, the measured ticket and
the fixed integration gaps.

## Running the agent today

```sh
.venv/bin/python scripts/prepare_stack.py          # creates runtime/secrets/executor_agent
.venv/bin/python -m app.executor_agent --bind 127.0.0.1 --port 8131
```

On Docker Desktop for macOS, `127.0.0.1` is reachable from a container through
`host.docker.internal`. On Linux, bind the Docker bridge gateway instead
(`--bind 172.17.0.1`) and point the cockpit at it. The agent refuses a public bind.

The native cockpit does not need the agent: without `AEION_EXECUTOR_URL` it uses the
in-process executor. With `AEION_EXECUTOR_URL` it uses the remote client and
`AEION_EXECUTOR_TOKEN_FILE` for the token.

## Migration order

1. Extract the executor calls behind an interface (`ExecutorClient`) with the current
   in-process implementation as the default. **Done.**
2. Add the host agent with `probe` and `run_tests`; keep inference local. **Done, plus
   `run_role` because a containerised cockpit cannot run a mission without it.**
3. Qualify `run_role` against the real gateway in a disposable container. **Done (live):**
   a full PD01 mission ran through the containerised cockpit, host executor and gateway
   (paid DeepSeek author, paid GLM reviewer, 19/19 trusted tests, Gate checkpoint,
   operator approval, sealed packet, `$0.001406`, 2,139/1,841 tokens). A bounded Factory
   ticket then ran the same path, closed the ticket on completion and published local
   release `fc6ca0d` (`$0.001763`, 2,495/2,372 tokens). A hosted CI provider-backed run,
   independent providers and the qualification items in P7 are still missing. See
   [`live-inference.md`](live-inference.md).
4. Add the cockpit service to Compose and make the native path an explicit, documented
   fallback. **Done:** without `AEION_EXECUTOR_URL` the engine uses the in-process
   executor; the Compose service sets the remote URL, and a missing agent never falls
   back to a socket.
