# ParcelDesk product

The product surface the Factory improves. It is deliberately separate from the accepted
Python pricing slice in `app/` and `tests/`; both suites run for every Factory mission.

## Runtime

- **API** — Quarkus 3.40.1, Hibernate ORM/Panache, Java 25.0.4, H2 file database.
  Source: `backend/src/main/java/com/aeion/parceldesk`. Tests:
  `backend/src/test/java/com/aeion/parceldesk`.
- **Web** — Deno 2.5, Fresh 1.6, Three.js 0.170. Source: `frontend/`. Tests:
  `frontend/tests/`.
- Local ports: API `127.0.0.1:8081`, web `127.0.0.1:8125`.

## Domain

Orders move through `PACKING → IN_TRANSIT → DELIVERED`, with `REFUNDED` as the terminal
refund state. A refund is allowed only for a delivered order inside 30 days; shipping is
never refunded; a refund of €200 or more needs a written reason.

The review ledger stores a pull request per Factory mission: the diff, trusted check
counts, the independent reviewer verdict, the release commit and the seal digest. A pull
request is mergeable only when every trusted test passed, the Gate approved the mission and
the reviewer verdict is `OK`.

## API

```
GET  /api/health
GET  /api/summary
GET  /api/orders
GET  /api/orders/{id}
POST /api/orders/{id}/refund          {"reason": "..."}
GET  /api/pulls
GET  /api/pulls/{id}
POST /api/pulls                       mission evidence (idempotent by missionId)
POST /api/pulls/{id}/review           {"verdict": "OK"|"CHANGES_REQUESTED", "comment": "..."}
POST /api/pulls/{id}/merge            {"mergedBy": "..."}
POST /api/pulls/{id}/close
```

## Tests

```sh
# backend
cd backend && mvn -B test

# frontend
cd frontend && deno task test
```

The Factory worker detects the product directories, runs the Python contract suite, the
Maven suite and the Deno suite, and merges their JUnit reports into the trusted result the
Gate re-runs.

## Boundaries

No provider credential, no Docker socket and no customer data. The seed is synthetic. A
Factory ticket may edit only `product/**`; policy, configuration, `AGENTS.md` and Git
history stay outside the patch.
