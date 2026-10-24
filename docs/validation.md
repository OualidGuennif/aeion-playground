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

See [the roadmap](roadmap.md) for uncompleted gates. The trusted native model CLI/controller,
independent acceptance oracle, usage reservations/outbox and scoped GitHub delivery remain
separate work; test isolation alone does not qualify those boundaries.
