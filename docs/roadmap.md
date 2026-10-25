# Completion gates

Each gate requires execution evidence. A Dockerfile or an empty service is not completion.
The native source baseline has 248 regression tests and real local Git/pytest checks with
controlled model replies. Historical provider-backed demonstrations remain private local
evidence; they are not copied into this public repository or replayed for activity.

| Gate | Required behavior | Initial status |
| --- | --- | --- |
| Reproducible source | Public clean repository, pinned dependencies/actions, secret scan, CI | Public baseline CI passed; hash-locked fresh install, Docker and dependency CI added |
| Isolated execution | Non-root disposable workers, read-only root/source, no host/socket/secrets, resource/deadline bounds, negative boundary tests | Local full suites passed: 264 Docker / 266 macOS; exact-head hosted CI required |
| Isolated inference | Separate OpenCode role execution, minimal context, mediated credentials/egress, no approval authority | Four controlled-response qualifications passed; full live PD01 mission through the containerised cockpit on 2026-10-25 (paid DeepSeek author + paid GLM reviewer, 19/19 trusted tests, Gate checkpoint, operator approval, sealed packet, $0.001406); a live Factory ticket then completed the same path and published local release `fc6ca0d` ($0.001763, 2,495/2,372 tokens); hosted provider-backed run and independent providers still required |
| Accounting | Atomic run/usage records, idempotency, held unknown exposure, reservations and bounded fallback | Canonical reservations/atomic settlement implemented with controlled concurrency/crash tests; complete reconciliation tooling pending |
| External acceptance | Gate-owned read-only checks, worker results insufficient by themselves | Implemented for new missions: the Gate re-runs the trusted tests against the patched app and denies `EXTERNAL_ACCEPTANCE` on a worker-only or weakened pass; controlled local tests, exact-head CI required |
| Durable events | Transactional event outbox, idempotent recovery, trace-linked evidence | Implemented for new events; fenced replay and actual collector restart qualified |
| Workboard | Real Paperclip task linkage, persistent service and health; one decision authority | Persistent authenticated Compose stack and restart/DB restore qualification; browser-approved controller connection and a real Codex development task verified locally |
| Observability | Real collector/export with task/run/digest linkage and unavailable states | Local pinned collector, metadata-only logs/spans, real outage/restart and privacy qualification; at-least-once IDs |
| Context | Scoped provenance/freshness, no automatic private sibling sources | Partial |
| GitHub delivery | Dedicated demo scope, branch/PR/checks tied to exact head, separate human merge authority | Pending |
| Qualification | Fresh install, escape/failure/recovery tests, backup/restore and documented limits | Pending |

Preserve existing source designs and sealed local history throughout. No public application
hosting or production access is implied by publishing this source repository.
