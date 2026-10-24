# Running the demo in containers

Status: **design accepted, implementation pending.** Nothing in this document is
implemented yet; it records the decision and the acceptance tests that would close it.

## The problem

The cockpit (Mission Control + ParcelDesk) currently runs as a native process on the
operator's machine. The demo is therefore only reproducible on macOS, on that machine, and
the operator's host is part of the trust boundary. We want the whole demo to run under
Compose, on any Linux host with a Docker engine, without weakening the boundaries stated in
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
                │ loopback only, capability token, allowlisted ops
┌───────────────▼─────────────────────────────────────────────────┐
│  executor agent  (TRUSTED, runs on the host, outside Compose)    │
│  owns the Docker socket, the macOS sandbox and the budget ledger │
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
| `run_tests` | bounded source snapshot digest, timeout, expected executor identity | JUnit report (bounded, parsed) |
| `run_role` | role, model, prompt digest, lease id | structured result, bounded |
| `probe` | none | executor identity, image ids, limits |

Properties that make this safe enough to build:

- The agent binds `127.0.0.1` only and requires a capability token read from an owner-only
  file — the same pattern already used by `GatewayClient` for the inference gateway.
- The agent does not accept a command, a path or an image from the caller. It accepts a
  *digest* and resolves the snapshot itself; anything that does not match a prepared
  snapshot is refused.
- Every operation is bounded: bytes, seconds, output size. No streaming of unbounded data.
- The agent holds the budget reservation and the sealed evidence keys. The cockpit remains a
  view over the ledger; it cannot sign an evidence packet it did not obtain through the
  agent.
- Compromise of the cockpit yields: forged UI, refused operations, and a loud failure. It
  does not yield container launch.

## What this does *not* solve

- The host agent is still privileged. It is a smaller attack surface, not a safe one. It
  must run as the operator, with its own hardening and its own tests.
- A compromised host agent can start any container. That risk is inherent to "run Docker on
  this machine" and must be stated, not hidden.
- Multi-tenant hosting is out of scope: one operator, one host, one ledger.

## Acceptance tests to write before calling this done

1. The cockpit container has no `/var/run/docker.sock`, no host home and no credentials:
   inspect the running container in the test, do not trust the Compose file.
2. `run_tests` with a snapshot digest that was never prepared is refused, and nothing is
   launched.
3. A caller without the capability token is refused; a caller with a token from a
   world-readable file is refused at startup.
4. The worker launched by the agent has `--network none`, read-only root, non-root, dropped
   capabilities, and the documented limits — asserted from `docker inspect`.
5. Killing the agent mid-run leaves no orphan container, and the mission is recorded as
   blocked with a reason, not silently retried.
6. The cockpit container can serve Mission Control and ParcelDesk with the agent stopped;
   it reports `executor unavailable` instead of failing to boot.

## Migration order

1. Extract the executor calls behind an interface (`ExecutorClient`) with the current
   in-process implementation as the default.
2. Add the host agent with `probe` and `run_tests` only; keep inference local for now.
3. Add `run_role` once the two-operation path is qualified.
4. Add the cockpit service to Compose and make the native path an explicit, documented
   fallback (`--native-executor`), never an automatic one.
