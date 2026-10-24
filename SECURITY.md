# Security model and reporting

This is a local playground with explicit qualification gates, not a production service.
Do not publish credentials or exploit details in public issues. For a suspected vulnerability,
use GitHub private vulnerability reporting when enabled on this repository.

Trusted: the operator, Gate/controller, selected runtime images and host/container engine.
Untrusted: tasks, repository/test code, model output, worker output and external callbacks.
Models propose changes; they do not own approval, policy, budget or proof authority.

The inherited macOS boundary confines canonical pytest filesystem/network/process access.
It does not confine the parent model CLI, provide complete resource quotas or attest JUnit
honesty. Strong worker isolation, constrained egress and external acceptance checks remain
explicit gates in [the roadmap](docs/roadmap.md).

Do not mount a host home, Docker socket, provider credential store or unrelated project
into an untrusted worker. Runtime/session files and local secrets are excluded from Git
and Docker build contexts. Dotenv loading is restricted to this project.

CI, secret scans and dependency audits are evidence for their specific checks. They do not
establish that every phase, deployment configuration or security boundary is qualified.
