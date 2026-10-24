# Durable local telemetry

`Store.add_event` commits the canonical event and its frozen metadata outbox row in one
SQLite transaction. Every new event has a stable `instance:event` identity. Existing
history and sealed proofs are neither backfilled nor rewritten. The database, WAL/SHM
and private audit projection are owner-only files.

A native exporter leases one pending record for 30 seconds. Separate audit and collector
acknowledgements survive restart; an expired owner cannot acknowledge a newer lease.
The audit append is fsynced, with a nonblocking file lock and 5 MiB rotation/two backups.
SQLite remains the full canonical history. Disk/SQLite failures remain explicit.

The collector receives only a versioned allowlist: event type/actor, validated task,
fixture/run/session/model identity, bounded metrics and SHA256 digests. Messages, source,
prompts, arbitrary payloads, headers and credentials are excluded. OTLP logs and
instantaneous ledger-event spans share trace/span/event IDs. These spans describe ledger
events; they do not invent model execution durations or provider provenance.

Delivery is **at least once**. If a response or acknowledgement is lost, the same identity
may appear again. A logs success followed by a traces failure also repeats the log on
retry. Consumers must deduplicate by `aeion.event.id`; no cross-HTTP exactly-once claim.
HTTP 429/502/503/504 and network failures back off with jitter and honor `Retry-After`.
Other HTTP refusals, malformed/oversized responses and partial rejection remain pending
without automatic retries. After correcting a permanent rejection, an operator may use
`--requeue` to resend that record explicitly. Collector acknowledgement is not a remote
archive or an invoice. The local collector uses bounded rotating files without an
external exporter or an in-memory batch stage.

```sh
.venv/bin/python scripts/prepare_stack.py
AEION_OPERATOR_UID=$(id -u) AEION_OPERATOR_GID=$(id -g) \
  docker compose up -d otel-collector
AEION_OTEL_TESTS=1 .venv/bin/python -m pytest -q tests/test_otel_collector.py
.venv/bin/python scripts/export_events.py --limit 100
# Only after diagnosing/correcting a permanent rejection:
.venv/bin/python scripts/export_events.py --requeue 123
```

The receiver is published only on `127.0.0.1:4318`, health on `127.0.0.1:13133`.
`runtime/otel/collector` is a private local projection, excluded from Git. The cockpit
reports actual receiver health, exporter state and pending counts; a file's existence
alone cannot establish a running collector. Health reachability does not prove a specific
record was persisted. The opt-in qualification checks actual receiver files, metadata
privacy, duplicate IDs, an outage and controller/collector restart recovery. Its events
are explicitly marked `controlled-qualification`; no model provider is called.

No local HTTP authentication is imposed on the telemetry receiver. Other processes run
by the local operator can submit telemetry. Consequently collector data is diagnostic;
Gate decisions and sealed evidence use the canonical ledger, not received telemetry.
