# ParcelDesk product: Quarkus, Deno and an autonomous Factory

Status: **working local product, exercised by a live paid mission.** The 3D web surface, the
Quarkus API, the ticket-to-PR loop and a host-side promotion of an accepted release have all
run. A remote GitHub PR, a hosted stack test of these services and independent model
providers remain outside what was measured.

## Runtime

| Layer | Technology | Port |
|---|---|---|
| API | Quarkus 3.40.1, Hibernate ORM 7.4.9, H2 2.4.240, **Temurin 25.0.4.1** | `127.0.0.1:8081` |
| Web | Deno 2.5.0, Fresh 1.6.8, Preact 10.19.6, Three.js 0.170 | `127.0.0.1:8125` |
| Factory control plane | cockpit, existing Python controller | `127.0.0.1:8124` |
| Host executor | trusted agent, disposable worker and roles | `127.0.0.1:8131` |
| Gateway | provider credential mediator | `127.0.0.1:8130` |

The API and web are separate Compose services. Both have a read-only root, no Docker socket,
no host home, no provider credential, dropped capabilities, `no-new-privileges`, bounded
memory/CPU/PIDs and loopback-only published ports. The API keeps synthetic orders in an H2
file volume; the web talks to the API and to the cockpit through server-side proxy routes, so
the browser has no cross-origin access.

```
browser ──▶ :8125 Fresh ──▶ :8081 Quarkus/Hibernate/H2
                    └─────▶ :8124 cockpit (tickets, missions, approval)
                                     │
                              :8131 host agent ──▶ disposable worker (Python/Maven/Deno)
                                     └──────────▶ :8130 gateway ──▶ paid models
```

## Source layout

```
sandbox/parceldesk/product/
  backend/     Quarkus app, H2, 8 baseline tests
  frontend/    Fresh routes, Three.js scene, Deno tests, Dockerfiles
  README.md    the product contract and API
```

The product lives inside the accepted sandbox template so a Factory mission can copy it,
patch `product/**`, run the suites and publish a local Git release. The legacy Python pricing
slice in `sandbox/parceldesk/app` and `tests` is a **separate release line** and is not run
for product missions.

## Product API

```
GET  /api/health
GET  /api/summary
GET  /api/orders
GET  /api/orders/{id}
POST /api/orders/{id}/refund      {"reason": "..."}
GET  /api/pulls
GET  /api/pulls/{id}
POST /api/pulls                   mission evidence, idempotent by missionId
POST /api/pulls/{id}/review       {"verdict": "OK"|"CHANGES_REQUESTED", "comment": "..."}
POST /api/pulls/{id}/merge        {"mergedBy": "..."}
POST /api/pulls/{id}/close
```

The refund rule is deterministic: delivered only, 30-day window, shipping excluded, and a
written reason at €200 or more. The review ledger refuses to merge a pull request unless all
trusted tests passed, the Gate approved the mission and the sentinel verdict is `OK`.

## The autonomous ticket flow

1. The web form sends a ticket with `product: "product"` to the cockpit. The cockpit stores
   the ticket and admits a bounded mission with `allowed_paths: ["product/**"]` and
   `test_profile: "product"`.
2. The paid author patches the product; the worker runs the Maven backend and Deno frontend
   suites; an independent reviewer reads the diff; the Gate applies policy.
3. Low risk with green checks is `CLEAN` and autonomous. High risk or a protected change
   becomes a human checkpoint in the cockpit/web.
4. The Gate re-runs the **trusted product tests** in a separate checkout, then the accepted
   patch is sealed and published as a local Git release. The product release head
   (`product_head:product`) advances independently of the legacy Python head.
5. The web's `/sync` posts the mission evidence to the product API as a pull request. A
   low-risk green PR is auto-merged; anything else waits for the operator. The ticket shows
   **Resolved** once its PR is merged.
6. `docker build` of the sealed release's `product/backend` and a `compose up` of the API
   promote the accepted code to the live surface. This remains a host-side action; the
   cockpit has no Docker socket.

## Measured live run — PD-010, 2026-10-25

Ticket `TCK-B028A56C`, product, risk low:

- author `opencode-go/glm-5.3-flash`: 5,122 / 3,814 tokens, `$0.002675`
- reviewer `opencode-go/deepseek-v4.1-flash`: 6,179 / 1,098 tokens, `$0.001586`, verdict `OK`
- worker: **13/13** tests (9 Maven + 4 Deno); Gate-owned acceptance: **12/12**
- Gate: `CLEAN`, `allow`, autonomous, no human checkpoint
- sealed packet `sha256:0c83baa40e14cd4f4e2b6049a60886399f6f2d973ef3932d4b81d4d3dbb84f44`
- release `3df45d50bcb6c1f23da77038288b09d2919ae48c`, `product_head:product` advanced
- **PR-001 merged automatically**; the diff is `OrderView.java` + a new Quarkus test
- promoted API returns `refundReason: "Damaged in transit"`, `refundAmountCents: 4000` for
  PD-1050, and the live order page shows it

Two earlier live tickets are kept as evidence:

- **PD-008**: blocked `PATCH_CONTEXT` before any model call (0 tokens, $0) because the context
  reader only allowed Python files.
- **PD-009**: the correctly patched product was denied `OUT_OF_SCOPE` by the Gate's hardcoded
  `app/tests` scope (`$0.00475`, 5,434 / 6,558 tokens). The scope fix and a regression test
  followed.

### Second and third autonomous releases

The next product tickets build on the accepted release rather than the template:

- **PD-020 / TCK-B82E1033** *(Add a tested refund note formatter)*, low risk.
  author `opencode-go/glm-5.3-flash` 7,835 / 3,765 tokens `$0.003058`; reviewer
  `opencode-go/deepseek-v4.1-flash` 9,150 / 3,362 tokens `$0.003390` `OK`; worker **16/16**;
  Gate acceptance **13/13**: the Gate restored the *accepted PD-010* product tests plus its
  `OrderRefundListTest`, proving the trusted-product-test path; Gate `CLEAN`; packet
  `sha256:1041f53d…61eb`; release `b592d1f` (parent `3df45d5`).
- **PD-021 / TCK-AB19B7EB** *(Use the refund note in the refund desk)*, low risk.
  author GLM 8,112 / 2,852 `$0.002643`; reviewer DeepSeek 9,014 / 1,112 `$0.002019` `OK`;
  worker **16/16**; Gate acceptance **16/16**; Gate `CLEAN`; packet `sha256:f0087ba4…2ed09`;
  release `b01d4f7` (parent `b592d1f`).
  The accepted frontend now renders `€40.00 refunded · Damaged in transit` in the live refund
  desk; the old separate amount/reason fields are gone.

PR-002 and PR-003 were created by the web sync and **auto-merged**; both show the worker and
Gate test counts, the reviewer verdict, the diff and the release commit. The live web image
was rebuilt from PD-021; the live API remains from PD-010 because PD-020/PD-021 changed no
backend code.

Cumulative cockpit model spend after these live runs is **$0.132792**, plus **$4.000**
of conservative operator settlements for two unknown upstream outcomes (PD-014 and
PD-022), no unsettled attempt. The local authorization ceiling is $10.

### Fourth and fifth autonomous releases

- **PD-024 / TCK-6F38ADDE** *(Expose the refund ledger endpoint)*, high risk. Author GLM
  4,486 / 15,196 `$0.008271`; reviewer DeepSeek 5,896 / 10,439 `$0.007148` `OK`; worker
  **17/17**; Gate acceptance **16/16**; Gate `HUMAN_CHECKPOINT` (`risk=high`); operator
  approval via the web proxy; packet `sha256:d725c08b…a8d05a9`; release `aeeeb7a1`;
  **PR-004 merged by `operator`** (the human merge path).
- **PD-028 / TCK-AAE35494** *(Read the refund desk from the refund ledger)*, low risk.
  Author GLM 8,118 / 15,012; reviewer DeepSeek 9,352 / 6,299 `OK`; worker **17/17**; Gate
  acceptance **17/17**; Gate `CLEAN`; packet `sha256:7518c6d6…`; release `b5fe5f7`;
  PR-005 auto-merged. The live web shows `€40.00 refunded · Damaged in transit · 2026-10-17`.

The chain is `3df45d5 → b592d1f → b01d4f7 → aeeeb7a1 → b5fe5f7`; the legacy head remains
`fc6ca0d`. The API image was promoted from PD-024 and the web image from PD-028.

### Reliability fixes found by the live tickets

Each failure is preserved in the ledger; the fixes are covered by tests:

- PD-011/PD-015: the models spent the whole **8,192-token** completion bound on reasoning
  before the patch. The admitted bound is now **16,384** tokens (gateway and role lease);
  the provider response and usage are still bounded and validated.
- PD-012/PD-013: a correct patch was rejected because git requires trailing context.
  `apply_patch` now uses `--unidiff-zero`; PD-018 then showed wrong `@@` counts, so it also
  uses `--recount`. A miscounted hunk and a minimal hunk have regression tests.
- PD-014: an upstream stream crossed the 2 MiB mediator bound, so no usage record existed
  and $2 was held. PD-022: the mediator's 90 s upstream deadline cut a role still inside its
  180 s admission at 91.7 s. `Store.reconcile_unknown_attempt` charges the **full reservation**
  (never zero), records a note and clears the global block; both $2 reservations were
  reconciled conservatively, and the upstream deadline is now 170 s.
- PD-016/PD-017: a length stop with a mediated completion was misclassified as provider
  unavailability, and a missing local gateway credential also cooled the model. The router
  now treats length stops and local gateway/configuration errors as non-cooldowns; the
  incorrect DeepSeek cooldown was cleared with a correction note.
- PD-019: the author and tests were green (16/16, 13/13) but the reviewer route was cooled by
  the credential error above. PD-020 reran the same ticket successfully after the routing fix.

## Commands

```sh
# build the product images and start the stack
AEION_OPERATOR_UID=$(id -u) AEION_OPERATOR_GID=$(id -g) \
  docker compose -f compose.yaml build parceldesk-api parceldesk-web cockpit
.venv/bin/python scripts/stack.py --provider-auth runtime/secrets/opencode-provider.json up

# verify the product itself
curl -s http://127.0.0.1:8081/api/summary
open http://127.0.0.1:8125/

# backend and frontend tests (host, no Docker)
cd sandbox/parceldesk/product/backend && mvn -B test
cd ../frontend && deno task test

# opt-in polyglot worker profile
AEION_DOCKER_TESTS=1 .venv/bin/python -m pytest -q tests/test_product_worker.py

# promote a sealed release (host-side; replace PD-010 with the accepted mission)
REL="$PWD/runtime/cockpit/sandbox/releases/PD-010/repo/product/backend"
docker build -t aeion-parceldesk-api:1 -f "$REL/Dockerfile" "$REL"
docker compose -f compose.yaml up -d --no-deps parceldesk-api
```

## Limits

- The review ledger is local H2. The cockpit still creates no remote GitHub PR, branch push
  or deployment; the local PR is the product's auditable record.
- A host-side operator command, `scripts/deliver_github.py`, turned PD-028 into real GitHub
  issue #33 and PR #34, waited for 8/8 checks including the new `product-tests` job, and
  squash-merged it as `59ae38a`; the remote product tree exactly matches the sealed release.
  This remains operator authority, not a cockpit action; see
  [`github-delivery.md`](github-delivery.md).
- Promotion of the running Compose services is still a host-side build of the sealed release,
  not an autonomous deploy.
- The hosted `stack-tests` job still starts only the cockpit, Paperclip and Postgres; the
  product services are measured locally, by the worker profile and by the new
  `product-tests` CI job.
- The first product release had no parent; later product tickets anchor to
  `product_head:product`. The legacy Python head stays at `fc6ca0d`.
- Both live model families share one provider account; the review is same-provider.
- The local web UI has no authentication and is bound to loopback. It is an operator surface,
  not a multi-user service.
- The accepted PD-010 packet was sealed before the context/scope fixes; it is preserved
  untouched. PD-008/PD-009 remain blocked in the history.
