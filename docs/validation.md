# Validation checkpoints

The public baseline's macOS and secret-history CI passed. Dependencies and CI actions are
pinned; the installation uses hash-enforced locks and excludes runtime/provider state.

Disposable Docker execution is qualified with real containers for non-root identity,
read-only sources/root, no host/Git/socket/credentials, network refusal, cgroup CPU/RAM/PID
limits, bounded tmpfs/files/descriptors, deadline cleanup and executor/image binding.
Worker and delivery use distinct containers with the same frozen identity. Controlled
model answers exercise the full local Git/test/review/Gate/seal/delivery loop; they do not
establish live provider availability or isolated model inference.

The inherited pytest pin had GHSA-6w46-j5rx-g56g, fixed in 9.0.3. Both controller and worker
now use 9.0.3. A fresh audit of the controller, worker and scanner locks reported no known
vulnerabilities across 55 dependency entries at this checkpoint. This is a dated database
result, not a guarantee against unknown vulnerabilities.

Local complete suites with the corrected dependency: **264 Docker cases passed, 7 native
cases skipped**; **266 macOS cases passed, 5 Docker cases skipped**. The CI workflow runs
both plus full Git history secret scanning and dependency auditing. Hosted CI must pass on
the exact PR revision before merge. PR #1 passed all four hosted checks on
`aeed0bb5f65198dd98d4a3b00159f747582c87c8` and was squash-merged as
`2dce9f24dbb96277a0dacfd37d3d73f437f85e82`. Private local run data is not published as evidence.

PR #3 passed all five hosted checks on `cd14b945ae3cfe92e4f9be21b4f22e0b28a9963f`
and was merged as `e8fd867`. Its real-stack tests cover authenticated API refusal,
restart persistence and a PostgreSQL dump/restore into a throwaway database.

The isolated-inference checkpoint passed **335 Docker cases with 7 native-only skips**
(including four real CLI/role checks and two actual-stack checks), and **331 macOS cases
with 11 opt-in container/stack skips**. The OpenCode npm lock audit reported zero known
vulnerabilities. Inference qualification uses controlled local responses, not live model
work. Hosted checks must pass on the final PR revision; later focused transport changes
also require their targeted qualification.

See [the roadmap](roadmap.md) for uncompleted gates. The trusted controller remains
native; independent acceptance, budget reservations/outbox and scoped GitHub product
delivery are separate work. Test/container counts do not qualify those boundaries.

The durable-telemetry checkpoint passed **393 cases with 12 skips** on macOS
(Python 3.12.8). Eleven skips are the existing opt-in container/stack checks; the twelfth
is the collector qualification below, which was run separately and passed. The opt-in
qualification used the pinned local collector container and asserted its read-only root
filesystem, dropped capabilities, no-new-privileges, memory/CPU/PID limits, loopback-only
ports, metadata privacy (a canary message and payload never reached the receiver), duplicate
identity on explicit replay, collector-outage backoff, and recovery across a controller and
collector restart. The events it submitted are marked `controlled-qualification`; no model
provider was called, and a receiving collector is not evidence of isolated inference. These
are local results: hosted CI must pass on the final PR revision, and the telemetry path is
not an acceptance oracle.
