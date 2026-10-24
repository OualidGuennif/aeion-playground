# Security model and reporting

This is a local playground with explicit qualification gates, not a production service.
Do not publish credentials or exploit details in public issues. For a suspected vulnerability,
use GitHub private vulnerability reporting when enabled on this repository.

Trusted: the operator, Gate/controller, selected runtime images and host/container engine.
Untrusted: tasks, repository/test code, model output, worker output and external callbacks.
Models propose changes; they do not own approval, policy, budget or proof authority.

Canonical tests default to disposable Docker containers: no network, non-root 65532,
read-only root and minimal source snapshot, no capabilities, no-new-privileges, 256 MiB
RAM/no swap, one CPU, 64 PIDs, 64 MiB tmpfs, 2 MiB per file and 128 open descriptors.
The trusted native controller launches Docker; no container receives a Docker socket.
Timeout cleanup removes the container even when the Docker client was killed. Missing
backend/image fails closed. Escape attempts and cgroup limits have real-container tests.

The native model CLI is still outside that boundary. Isolated inference, constrained egress
and external acceptance checks remain explicit gates in [the roadmap](docs/roadmap.md).
No container or test report establishes JUnit honesty, kernel security or production safety.

Do not mount a host home, Docker socket, provider credential store or unrelated project
into an untrusted worker. Runtime/session files and local secrets are excluded from Git
and Docker build contexts. Dotenv loading is restricted to this project.

CI, secret scans and dependency audits are evidence for their specific checks. They do not
establish that every phase, deployment configuration or security boundary is qualified.
