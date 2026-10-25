# Host-side GitHub delivery

P6 turns a **sealed local product release** into a real GitHub branch, issue, pull
request and CI-gated merge. It is an operator command on the host, not a cockpit
feature: the cockpit and the model roles never receive a GitHub credential, and the
Factory still stops at the sealed local release.

## Why host-side

The cockpit runs read-only without a Docker socket or provider credentials. Giving it
a GitHub token would add a second external authority to the same process that runs
model output. The operator already has an authenticated `gh` session; delivery uses
that session explicitly, after the Factory's Gate has sealed the release. Approval
authority stays separate from model output.

## The command

```sh
# verify the release, clone the remote, replace the product tree and commit locally
.venv/bin/python scripts/deliver_github.py --mission PD-028 --dry-run

# create/reuse the issue, push the branch, open the PR and wait for checks
.venv/bin/python scripts/deliver_github.py --mission PD-028 --wait

# only after every check passes, squash-merge and delete the branch
.venv/bin/python scripts/deliver_github.py --mission PD-028 --merge
```

`--no-issue` skips the issue. `--force` is only for an inspected divergence in
`sandbox/parceldesk/product`; the default is to refuse.

## What is verified before publishing

- The receipt is `aeion.local-delivery/v1`, names the same mission and says `product`.
- The sealed packet's canonical SHA-256 matches the receipt and the packet is sealed.
- The Gate decision is `allow` with an autonomous outcome, or `approved` with recorded
  operator approval and an assisted outcome.
- The release checkout is clean and `HEAD` equals the receipt commit.
- The release product tree can be resolved from its root and parent commits.
- The remote base tree is the release template or its parent, not arbitrary operator
  code. A divergence is refused unless the operator explicitly forces it.
- The prepared diff changes only `sandbox/parceldesk/product/**`. A path outside that
  prefix aborts before any push.
- The ticket is read from the local ledger if present; the issue and PR link it.
- The `product-tests` GitHub check exists and every non-skipped check passes before a
  merge. The tool refuses to merge on a failing or missing product job.

The branch, issue and PR URLs, the release commit and the packet digest are recorded
in `runtime/github-delivery/<mission>.json` (gitignored). Re-running a mission reuses
the recorded issue and PR; a later sealed commit for the same mission is pushed with
`--force-with-lease`.

## CI

`.github/workflows/ci.yml` now has a `product-tests` job that runs the product's real
Maven and Deno suites on Ubuntu 24.04 with Temurin 25 and Deno 2.5.0. It is independent
of the cockpit's local worker image; it is the remote check the delivery command waits
for.

## Measured

On 2026-10-25 the dry run for `PD-028` (release `b5fe5f7`, packet
`sha256:7518c6d6…`) verified the release, cloned
`OualidGuennif/aeion-playground`, prepared branch `factory/pd-028` and listed only
these product paths:

```
sandbox/parceldesk/product/backend/src/main/java/com/aeion/parceldesk/api/OrderResource.java
sandbox/parceldesk/product/backend/src/main/java/com/aeion/parceldesk/dto/OrderView.java
sandbox/parceldesk/product/backend/src/test/java/com/aeion/parceldesk/OrderRefundListTest.java
sandbox/parceldesk/product/backend/src/test/java/com/aeion/parceldesk/RefundLedgerTest.java
sandbox/parceldesk/product/frontend/lib/format.ts
sandbox/parceldesk/product/frontend/routes/index.tsx
sandbox/parceldesk/product/frontend/tests/format_test.ts
```

### Live delivery, 2026-10-25

The first live run created issue **#33**
<https://github.com/OualidGuennif/aeion-playground/issues/33> and PR **#34**
<https://github.com/OualidGuennif/aeion-playground/pull/34> with exactly the seven product
paths above. It then stopped because `gh pr checks` returns a non-zero "no checks reported"
response before the workflow registers its jobs; PR #35 fixed that. The PR's first CI run
also exposed a stale test expectation: the product worker test pinned 12 tests while PD-028
runs 17. PR #36 relaxed the floor to "at least 12, all pass".

After both fixes, a re-run on merged `main` prepared branch `factory/pd-028` at
**`2db7bc1`**, force-pushed PR #34 with `--force-with-lease`, waited for **8/8 checks**
(`native-tests`, `stack-tests`, `product-tests`, `docker-tests`, `secrets`,
`inference-tests`, `telemetry-tests`, `dependencies`), and squash-merged it as
**`59ae38a`**. Issue #33 closed at the same time. The remote
`origin/main:sandbox/parceldesk/product` tree is **`640bdbcf2655cb34202c9841117e9a89ffc4630d`**,
exactly the sealed release's `HEAD:product` tree. The remote route fetches `/api/refunds`
and renders `shortDate(order.refundedAt)`.

A dry run is not a remote delivery; this paragraph records the measured live run.

## Limits

- The cockpit still cannot push, open a PR or deploy. `--merge` is an operator action.
- The local release line and GitHub `main` are separate histories. Delivery replaces
  the product subtree from the sealed release; it does not merge the release's Git
  history into `main`.
- The tool uses the operator's `gh` authentication. It never prints or stores the
  token, and it is not available inside the cockpit, a worker or a model role.
- A live merge requires the exact repository's rules and checks. Branch protection,
  required reviewers and organisation policy are outside this tool; the tool only
  refuses on observed failing or missing checks.
- The `product-tests` job runs the suites, not the live Quarkus/Deno containers. The
  Compose smoke test remains local.
