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
account. The approval was a real human checkpoint, not a model verdict. Paperclip issue
creation returned HTTP 403 "Board access required", so the workboard link is missing; the
ledger and sealed packet are authoritative. PD01 has no delivery target, so this mission
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

Cumulative product-ticket spend: **$0.009011**; cumulative live model spend after all runs:
**$0.018630**, no unsettled attempt.

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
  artifact only; nothing was pushed, deployed or hot-swapped into the demo surface. The
  Paperclip workboard still refuses issue creation with HTTP 403 "Board access required".
- Refunds, orders or warehouse features through the legacy Python Factory. The accepted
  Python slice holds only the pricing module; the separate ParcelDesk product accepts
  `product/**` tickets with a Java/Deno worker profile.
- A remote GitHub PR or an autonomous deployment. The product review ledger is local H2 and
  promotion is a host-side rebuild of the sealed release.
- A hosted stack test of the product API/web services. The opt-in worker profile and the
  local live run are measured; the hosted `stack-tests` job still starts only the cockpit.
- A durable credential refresh path. The Console access token has an expiry; the gateway
  does not refresh OAuth. Re-export and rewrite the gateway file before it expires.

## Security properties preserved

- The role container has no provider credential, no Docker socket and no host mount; it
  receives a one-use lease token only.
- The gateway fixes the upstream host and path, bounds request/response sizes, refuses
  redirects and automatic retries, and records the lease state.
- The live credential is owner-only, gitignored, never printed and never in Git.
- A refused or unknown outcome holds the lease; only the Gate may choose another attempt.
