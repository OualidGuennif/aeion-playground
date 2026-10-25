# Live provider inference: measured status

Status: **live provider inference is qualified through the containerised cockpit.** A full
PD01 mission and a bounded Factory ticket have completed through the host executor and the
gateway with paid models, the second publishing a local Git release. A hosted
provider-backed CI run, independent providers, a durable credential refresh and the free
tier remain unqualified. This file records the refusal and the successful runs; do not turn
either into a broader claim.

## Successful run — 2026-10-25

The host executor agent ran a real `forge` role in its disposable OpenCode container,
through the inference gateway, with the operator's OpenCode Console credential mounted
**only into the gateway**:

- model: `opencode-go/deepseek-v4.1-flash`
- result: `completed`, one model step, no tools
- usage: **259 prompt tokens / 1,699 completion tokens / 1,958 total**
- gateway lease: `completed`, usage reported and bounded
- output: the requested patch-protocol JSON, parsed by the agent
- no provider key, lease token or prompt was printed; the role container received only a
  single-use lease

The gateway health now reports `providers_configured: ["opencode", "opencode-go"]`. The
credential is a local, owner-only file at `runtime/secrets/opencode-provider.json`
(gitignored). The temporary `auth export` copy was deleted after the gateway file was
written. Never commit or copy that file into a worker, the cockpit, a prompt or the ledger.

The local `config/model-routing.json` is still deliberately expired. The `run_role` path
above does not consult the routing policy; a full mission will, so it needs a future
`authorization_expires_at` and a small `budget_usd` before the cockpit can admit one.

## Full live mission — PD-003, 2026-10-25

With a future local authorization and paid-only role pools, PD01 ran end to end from the
containerised cockpit, through the host executor and the gateway:

- **forge** `opencode-go/deepseek-v4.1-flash`: 979 prompt / 1,654 completion tokens,
  `$0.001139`; returned the correct started-kilogram patch for `app/pricing.py`.
- **tests**: 19/19 on the worker run and 19/19 on the Gate-owned trusted rerun, with the
  frozen `aeion.pytest-executor/v1` image identity matching admission.
- **sentinel** `opencode-go/glm-5.3-flash`: 1,160 prompt / 187 completion tokens,
  `$0.000267`, verdict `OK`, a different model family.
- **Gate**: `CHECKPOINT` / `HUMAN_CHECKPOINT` because risk is high and the diff touches
  `app/pricing.py`; the operator read the evidence and approved.
- **result**: mission `PD-003` completed as `assisted`, gate `HUMAN_APPROVED`, evidence
  packet sealed at
  `sha256:a314da8a78c84bfbeb4552585d1d9575057a4ef7903ca38a7477dc9e3388aadd`.
  Mission totals: 2,139 prompt / 1,841 completion tokens, `$0.001406`.

Two earlier live admissions failed and were fixed rather than papered over: the cockpit
could not inspect Docker locally (now asks the executor), and legacy scenarios were admitted
in editor mode (now text patches when the executor is remote; PD01/PD02 declare their task
paths). Both fixes are merged with tests.

The sealed packet records the reviewer as "different CLI-selected model families (deepseek
vs glm) · not independent providers": both roles use the same OpenCode gateway and operator
account. The approval was a real human checkpoint, not a model verdict. At that time
Paperclip issue creation returned HTTP 403 "Board access required", so the workboard link
was missing (fixed on 2026-10-25; see the Paperclip section below). The ledger and sealed
packet are authoritative. PD01 has no delivery target, so this mission
sealed evidence without publishing a local release; the Factory ticket below did publish
one.

## Factory ticket and local release — TCK-E68512F8 / PD-006, 2026-10-25

The final end-to-end validation was a bounded ticket filed from the warehouse surface, not
a canned fixture: *Charge the started kilogram for 1,001-1,999 g*, area pricing, risk low,
with the existing 19-case pricing suite as its acceptance.

Two earlier live tickets failed and are recorded rather than hidden:

- `TCK-368CF6A6` / `PD-004` asked for an exhaustive 1..30,000 regression test. The paid
  DeepSeek role used its whole output bound: 1,223 prompt / 8,192 completion tokens,
  `$0.005099`, `finish_reason=length`, blocked `AGENT_BLOCKED`. The router then treated the
  truncation as unknown provider state and withheld DeepSeek for an hour.
- `TCK-C7998C79` / `PD-005` therefore fell back to `opencode-go/glm-5.3-flash` (1,139 / 273
  tokens, `$0.000307`); its patch was corrupt (`error: corrupt patch at line 9`,
  `MODEL_PATCH_INVALID`).
- Fix `a01cc7f` (PR #23): a length stop is a bounded output failure, not provider
  uncertainty. It no longer opens a cooldown, while repeated truncation still counts as a
  deterministic quality failure. The stale cooldown was cleared by an audited
  `operator.routing_health_cleared` event after its attempt was verified settled.

The successful ticket:

- **forge** `opencode-go/deepseek-v4.1-flash`: 1,171 prompt / 2,020 completion tokens,
  `$0.001388`; a one-line ceiling-division patch, 1 file, +1/-1.
- **tests**: 19/19 on the worker run and 19/19 on the Gate-owned trusted rerun, with the
  same frozen `aeion.pytest-executor/v1` identity.
- **sentinel** `opencode-go/glm-5.3-flash`: 1,324 prompt / 352 completion tokens,
  `$0.000375`, verdict `OK`.
- **Gate**: `HUMAN_CHECKPOINT` because the diff touches `app/pricing.py`; the operator read
  the evidence and approved.
- **result**: `PD-006` completed `assisted`, gate `HUMAN_APPROVED`, packet sealed at
  `sha256:40dbb227147920738c592648ef48589795f55066bad2100e6b1e4eec5eae4aa9`.
- **delivery**: local Git release commit `fc6ca0df544e5132ae154ac9ba462789a3b1156e`,
  parent `86ff1351a6079d22cf1351e8fbe7a911b14c2b54`, tree `97d3856e5a6d...`, 19/19 tests;
  `product_head` points at it. Ticket totals: `$0.001763`, 2,495 prompt / 2,372 completion
  tokens.

The live run also exposed a demo-surface bug: linked tickets stayed `running` after their
missions ended, so `open_tickets` kept growing. Fix `1b5ed0c` (PR #24) closes a ticket when
its mission reaches a terminal state; an audited `operator.tickets_reconciled` event closed
the three rows written before the fix and the API now reports `open_tickets: 0`.

The release is a local Git artifact at `runtime/cockpit/sandbox/releases/PD-006/`; it is
**not** hot-deployed to the ParcelDesk quote surface. The sandbox slice contains only the
pricing module and its tests, so a Factory ticket cannot implement refunds or orders in it.
The released diff keeps the baseline "DEFECT" comment above the corrected expression
because the ticket constrained the edit to the rounding line; the arithmetic and tests are
correct. Cumulative model spend recorded in the cockpit ledger after all live runs is
`$0.018630`, with no unsettled attempt. The product ticket below accounts for the difference.

## Product ticket — TCK-B028A56C / PD-010, 2026-10-25

The ParcelDesk product (Quarkus/Java 25 + Deno Fresh/Three.js) became the Factory target.
The ticket *Expose the refund reason in the order list* was filed from the product web
surface with `product: "product"`, risk low, and `product/**` as the admitted scope.

- **author** `opencode-go/glm-5.3-flash`: 5,122 prompt / 3,814 completion tokens, `$0.002675`
- **worker**: 13/13 tests (9 Maven + 4 Deno), product profile, 180-second bound
- **Gate-owned acceptance**: 12/12 trusted product tests in a separate checkout
- **reviewer** `opencode-go/deepseek-v4.1-flash`: 6,179 / 1,098 tokens, `$0.001586`, `OK`
- **Gate**: `CLEAN`, `allow`, autonomous, no human checkpoint
- **packet**: `sha256:0c83baa40e14cd4f4e2b6049a60886399f6f2d973ef3932d4b81d4d3dbb84f44`
- **release**: `3df45d50bcb6c1f23da77038288b09d2919ae48c`, product head
  `product_head:product` advanced; legacy head stayed `fc6ca0d`
- **review ledger**: PR-001 created and auto-merged by the product web sync; the live API
  was rebuilt from the sealed release and returns `refundReason: "Damaged in transit"` and
  `refundAmountCents: 4000` for PD-1050

Two earlier product tickets remain as evidence instead of being hidden:

- `TCK-4467BF55` / **PD-008** blocked `PATCH_CONTEXT` before any model call (0 tokens, $0):
  the source-context reader only allowed Python files.
- `TCK-9A6C8616` / **PD-009** produced a correct patch but was denied `OUT_OF_SCOPE`
  (`$0.00475`, 5,434 / 6,558 tokens): the Gate's base scope was hardcoded to `app/tests`.
  The fix admits `product/**`, keeps the task scope check, restores trusted product tests in
  the acceptance checkout and advances a separate product release head.

Cumulative product-ticket spend: **$0.153366**; cumulative live model spend after all runs:
**$0.162985**, plus $8.000 conservatively reconciled (four unknown upstream outcomes:
PD-014, PD-022, PD-031 and PD-035), no unsettled attempt. The local model authorization is
**$10**; this is a ceiling, not spend. At $8.162985 exposure, a new $2 mission reservation
is refused until the operator raises or changes the authorization.

## Product releases PD-020 through PD-032, 2026-10-25

Two more paid product missions built on `product_head:product` (PD-010), with the Gate
acceptance restored from the accepted release's own product tests:

- **PD-020 / TCK-B82E1033** — *Add a tested refund note formatter*. Author
  `opencode-go/glm-5.3-flash` 7,835 / 3,765 tokens `$0.003058`; reviewer
  `opencode-go/deepseek-v4.1-flash` 9,150 / 3,362 `$0.003390`, verdict `OK`; worker 16/16;
  Gate acceptance **13/13** (the accepted PD-010 tests, including `OrderRefundListTest`);
  Gate `CLEAN`; packet `sha256:1041f53d056ede3bce89049f77e475689889380f049ef88f3a22be7a07ac61eb`;
  release `b592d1f999227803242e980baa7f2c178f1c4683`.
- **PD-021 / TCK-AB19B7EB** — *Use the refund note in the refund desk*. Author GLM
  8,112 / 2,852 `$0.002643`; reviewer DeepSeek 9,014 / 1,112 `$0.002019`, `OK`; worker
  16/16; Gate acceptance 16/16; Gate `CLEAN`; packet
  `sha256:f0087ba4aa13b4138759dbc9bba384ccb0dc72377608cf6308842a7d9c92ed09`; release
  `b01d4f77b9b6d3ad8f8831923b495b80b8ba773e`.

PR-002 and PR-003 were auto-merged by the product web sync. The live web image was rebuilt
from PD-021 and rendered `€40.00 refunded · Damaged in transit`; the old separate amount and
reason fields were gone.

Two further product releases followed:

- **PD-024 / TCK-6F38ADDE** — *Expose the refund ledger endpoint*, high risk. Author GLM
  4,486 / 15,196 tokens `$0.008271`; reviewer DeepSeek 5,896 / 10,439 `$0.007148` `OK`;
  worker 17/17; Gate acceptance 16/16; Gate `HUMAN_CHECKPOINT` (`risk=high`); the operator
  approved via the web proxy; packet `sha256:d725c08b…a8d05a9`; release `aeeeb7a1`;
  **PR-004 was merged by `operator`, not auto-merged**.
- **PD-028 / TCK-AAE35494** — *Read the refund desk from the refund ledger*, low risk.
  Author GLM 8,118 / 15,012; reviewer DeepSeek 9,352 / 6,299 `OK`; worker 17/17; Gate
  acceptance 17/17; Gate `CLEAN`; packet `sha256:7518c6d6…`; release `b5fe5f7`; PR-005
  auto-merged. The rebuilt web renders `€40.00 refunded · Damaged in transit · 2026-10-17`.

The API image was rebuilt from PD-024 and exposes `GET /api/refunds`; the web image was
rebuilt from PD-028. The live product still runs on loopback only.

### Sixth release and the paid-budget stop, 2026-10-25

- **PD-032 / TCK-218D9489** — *Add the refund CSV endpoint*, low risk. Author GLM
  4,654 / 4,950 `$0.003173`; reviewer DeepSeek 5,607 / 9,530 `$0.006559` `OK`; worker
  **17/17**; Gate acceptance **17/17**; Gate `CLEAN`; one file, +41 lines; packet
  `sha256:cb2dca372c50356760fe13d361c9a06ef66ce02e707a785f384783de17731ffb`; release
  `393f3fed624443efd3a852cc478f01952ad036b5`; PR-006 auto-merged locally. The API image was
  promoted from PD-032 and `GET /api/refunds.csv` is live: `text/csv`, attachment
  `refunds.csv`, header plus one row per refunded order.
- A dedicated Quarkus CSV test was attempted four times. PD-029 compiled-failed
  (`TEST_EXECUTION_ERROR`, `$0.005901`); PD-030 exhausted the 16,384-token output bound
  (`$0.008898`); PD-034 produced a passing test but refunded PD-1049, colliding with the
  existing `RefundLedgerTest` (`TEST_FAILURE`, `$0.005662`); PD-035 was cut by the 2 MiB
  upstream response bound and its $2 reservation was reconciled. The endpoint now has a
  dedicated `RefundCsvTest`, a manual operator/Codex contribution rather than a paid
  Factory release: it asserts the header, the attachment disposition, the seeded PD-1050
  row, the absence of a non-refunded order, and comma/quote escaping. The backend suite is
  12 tests; the combined product suite is 21 (12 Maven + 9 Deno).
- PD-033 was denied `STALE_BASE` because PD-032 was delivered after admission. PD-036 was
  denied `BUDGET_RESERVATION_DENIED`: $8.162985 exposure plus a $2 envelope exceeds the $10
  ceiling. No further paid mission can start without operator budget authority.

Cumulative reported spend after all live runs is **$0.182358**. Five upstream outcomes had
no usage record and were settled at their **full $2.000** reservations as `reconciled_usd`
rather than inventing a zero cost: PD-014 (the 2 MiB mediator bound), PD-022 (a 90 s
mediator deadline), PD-031 (a 170 s deadline that cut a still-running stream at 169 s),
PD-035 and PD-043 (the 2 MiB response bound). No attempt is unsettled. The role admission is
now **300 s** and the mediator deadline **290 s**. The operator authorized a **$30.00** local
cap on 2026-10-25; current exposure is `$10.203658`, leaving `$19.796342`.

### Autonomous paid improvement run, 2026-10-25

With the $30 authorization, nine product tickets were admitted through the cockpit. The
router mixed model families and learned from deterministic failures:

- **PD-039** — GLM author, `$0.004670`, `MODEL_PATCH_INVALID`: it tried to recreate the
  existing `fresh.gen.ts` and `_app.tsx` as new files.
- **PD-040** — GLM author, `$0.009405`, `AGENT_BLOCKED` with `finish_reason=length`: the
  16,384-token output bound was exhausted.
- **PD-041** — GLM author and DeepSeek reviewer, `$0.003299`, worker and acceptance
  **17/17**, Gate `CLEAN`, sealed release `937aa5f2…`, packet
  `sha256:9a9af48b…`. It removes the warehouse loading overlay after the first render.
- **PD-042** — GLM author, `$0.001999`, `MODEL_PATCH_INVALID`: the manifest patch did not
  apply.
- **PD-043** — DeepSeek was selected for the medium-risk manifest class, but the upstream
  SSE response crossed the 2 MiB mediator bound; no usage record was returned. Its $2
  reservation was reconciled conservatively and reported cost stayed `$0.00`.
- **PD-044** — GLM author and DeepSeek reviewer, `$0.002768`, worker and acceptance
  **17/17**, Gate `CLEAN`, sealed release `8733f9c7…`, packet
  `sha256:030227a8…`. It adds a refunds CSV download link to the refund desk.
- **PD-045** — GLM author, `$0.000944`, `MODEL_PATCH_INVALID`: the proxy patch did not
  apply. The proxy allowlist/header fix was applied manually and verified live.
- **PD-046** — DeepSeek was selected for the medium-risk proxy class, but the same proxy
  task exhausted the 16,384-token output bound (`AGENT_BLOCKED`, `$0.010630`).
- **PD-047** — after raising the bound to 32,768 tokens with a lease regression test,
  DeepSeek finished at 10,264 output tokens but produced a patch that did not apply
  (`MODEL_PATCH_INVALID`, `$0.006958`). The length stop moved to a patch-validity stop.

The nine tickets created Paperclip issues **AEI-4** through **AEI-12**. PD-041 and PD-044
are sealed releases; the manifest registration, the refunds-CSV proxy allowlist and the
`Content-Disposition` forwarding are manual source follow-ups. PD-045, PD-046 and PD-047
all failed to seal the proxy fix. The gateway/agent output bound is now **32,768 tokens**
(was 16,384), but that did not fix patch validity. The 2 MiB response bound remains a known
limit.

Live verification of PD-044 found that its link returned 404 because the Fresh proxy
allowlist omitted `refunds.csv`; after the manual proxy fix, `GET /api/refunds.csv` through
the live web returns **200**, `text/csv`, `attachment; filename="refunds.csv"`, the header
and the PD-1050 row.

The failed attempts remain in the ledger and are documented in
[`parceldesk-product.md`](parceldesk-product.md): completion-bound truncation (PD-011/PD-015,
now 16,384 tokens), a corrupt/miscounted unified diff (PD-012/PD-013/PD-018, now
`--unidiff-zero --recount`), the unknown stream (PD-014, conservative settlement), and
reviewer-route misclassification (PD-016/PD-017/PD-019, fixed in the router).

## Refusal before the credential — 2026-10-25

A direct gateway lease and a real isolated OpenCode role were first attempted with the
free model `opencode/deepseek-v4.1-flash-free`. Both reached the provider and were refused:

```
OpenCode provider (HTTP 403): free tier can only be used from within OpenCode;
automatic retry forbidden
```

The lease was recorded as `refused`; no tokens were reported. The gateway had no
`OPENCODE_AUTH_FILE` mounted at that point and used its `"public"` fallback. The free tier
still refuses that path. Use the paid `opencode-go` route with a real Console credential
for live work; do not weaken the gateway to force the free tier.

## How the successful path is configured

1. The operator's OpenCode Console credential is exported locally and transformed into the
   gateway's provider file. The file is created with mode `0600` under `runtime/secrets/`;
   the export is deleted.
2. The stack is started with the provider file mounted only into the gateway:

   ```sh
   .venv/bin/python scripts/stack.py --provider-auth runtime/secrets/opencode-provider.json up
   ```

   The gateway sees `/run/provider-auth/auth.json`; the cockpit, agent, role containers and
   workers do not.
3. The agent `run_role` call is the live probe. It starts a disposable role container, the
   gateway issues one lease, the upstream returns, and the lease is settled. No retry.

## What this does *not* qualify

- A hosted CI provider-backed run; CI stays on controlled fixtures.
- The free tier; it remains refused outside OpenCode's own client path.
- Independent providers. Both live roles share one OpenCode gateway and operator account.
- GitHub delivery or any remote publication. The Factory ticket published a local Git
  artifact only; nothing was pushed, deployed or hot-swapped into the demo surface.
- Refunds, orders or warehouse features through the legacy Python Factory. The accepted
  Python slice holds only the pricing module; the separate ParcelDesk product accepts
  `product/**` tickets with a Java/Deno worker profile.
- A remote GitHub PR or an autonomous deployment. The product review ledger is local H2 and
  promotion is a host-side rebuild of the sealed release.
- A hosted stack test of the product API/web services. The opt-in worker profile and the
  local live run are measured; the hosted `stack-tests` job still starts only the cockpit.
- A durable credential refresh path. The Console access token has an expiry; the gateway
  does not refresh OAuth. Re-export and rewrite the gateway file before it expires.

## Paperclip workboard link, 2026-10-25

The host-only board credential is bound to `http://127.0.0.1:3101`, so the containerised
cockpit cannot use it directly. The supported link is a derived plain bearer file:

- `scripts/connect_paperclip.py` saves the browser-approved board store and derives
  `runtime/secrets/paperclip_api_token` (mode `0600`, never printed);
- the cockpit mounts that one file read-only at `/run/secrets/paperclip_api_token` and
  reads it through `PAPERCLIP_API_TOKEN_FILE`;
- `PAPERCLIP_PUBLIC_URL=http://127.0.0.1:3101` is used only to build browser links; the
  API call still uses the Compose service name.

Measured on the live stack after rebuilding the cockpit:

- the Paperclip component reports `ready · 2026.1005.0+846.git.412914cfa · authenticated`;
- a free `PD00` baseline (`PD-037`, request `paperclip-live-20261025`) created **AEI-2**
  with no model call, worker 12/19, Gate `NO_PATCH`; its stored URL was corrected from the
  internal service name to the public origin;
- a second free `PD00` (`PD-038`, request `paperclip-live-20261025b`) created **AEI-3**
  directly with `http://127.0.0.1:3101/AEI/issues/AEI-3`;
- no secret appeared in the environment (`PAPERCLIP_API_TOKEN` is unset) or in the logs.

**Limits:** these are workboard issues, not Paperclip agent runs. Paperclip's own generic
`Operator` agent remains `paused` with `setupAdapterPending`; the yellow “agents paused”
banner is about that optional runner, not the Aeion Factory. The board grant is one user
and one local instance; there is no independent monitoring claim.

## Security properties preserved

- The role container has no provider credential, no Docker socket and no host mount; it
  receives a one-use lease token only.
- The gateway fixes the upstream host and path, bounds request/response sizes, refuses
  redirects and automatic retries, and records the lease state.
- The live credential is owner-only, gitignored, never printed and never in Git.
- A refused or unknown outcome holds the lease; only the Gate may choose another attempt.
