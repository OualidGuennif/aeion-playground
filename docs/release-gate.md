# Deterministic release gate

A sealed mission is evidence that the Factory accepted a change. It is not
permission to publish that change to GitHub or to deploy it. The release gate
adds a second, deterministic decision based on the actual PR-shaped diff:

```text
sealed packet
  → mission Gate (tests, protected paths, review independence, budget)
  → publication policy (file count, line count, critical paths, risk)
  → GitHub CI (8 checks, product-tests required)
  → deployment health check
  → live web
```

The model never decides whether a path is critical or whether a diff is small
enough. Those rules are in `config/publication-policy.json`.

## Decision vocabulary

| Decision | Meaning | GitHub | Deploy |
|---|---|---|---|
| `auto` | small, allowed path, tests/review/Gate accepted | yes | yes |
| `human` | critical path, oversized, high risk or unknown path | only with an operator approval file | only after approval |
| `human_approved` | `human` plus a valid, recent approval file | yes | yes |
| `deny` | unsealed, incomplete, failing tests, non-OK review, protected path | no | no |

The policy is intentionally conservative:

- **Test-only** changes can use a larger bound: 25 files / 400 lines.
- **Small UI or frontend library** changes: 4 files / 120 lines.
- **Critical paths** always require a human: backend production
  (`product/backend/src/main/`), the API proxy (`product/frontend/routes/api/`),
  the Fresh manifest, Dockerfiles, `policy/`, `compose` and root Dockerfiles.
- **Unknown paths** are not auto-published; they require review.
- **High/critical risk** missions require a human.
- **Protected paths** are denied before any human override.
- **Tests** must be complete: no failures, errors or skips; the Gate review must
  be `OK` and independently established.

The policy is applied to the **actual GitHub PR diff**, not only the packet's
last-release diff. That matters when the public base lags several sealed local
releases: a 12-file test-only catch-up is still `auto`, while a 12-file change
that includes backend production code is `human`.

## Commands

Dry-run (clones and prepares locally, publishes nothing):

```sh
.venv/bin/python scripts/deliver_github.py --mission PD-065 --auto --dry-run
```

Automatic publication of an `auto` release:

```sh
.venv/bin/python scripts/deliver_github.py --mission PD-065 --auto --wait --merge
```

Human-required publication:

```sh
# After reviewing the packet, the PR-shaped diff and the policy reasons:
.venv/bin/python scripts/approve_github.py \
  --mission PD-XXX \
  --reason "Reviewed the critical backend change and its tests."

.venv/bin/python scripts/deliver_github.py \
  --mission PD-XXX \
  --approval-file runtime/github-delivery/approvals/PD-XXX.json \
  --wait --merge
```

The approval file is time-bounded (24 hours by default), bound to the mission,
packet digest and diff digest. `scripts/deliver_github.py` re-evaluates the
policy; an approval cannot override `deny`.

## Four deployments per day

```sh
.venv/bin/python scripts/redeploy.py --once --dry-run
.venv/bin/python scripts/redeploy.py --once
.venv/bin/python scripts/redeploy.py --loop --interval-hours 6 --publish
```

With `--publish`, each cycle:

1. reads the current `product_head:product` from the cockpit ledger;
2. runs the deterministic publication gate;
3. publishes an `auto` release to GitHub (or skips `human` until approval);
4. builds the sealed release's `product/frontend` image;
5. tags it as `aeion-parceldesk-web:1`, recreates `parceldesk-web`;
6. checks `/` and `/api/refunds.csv`;
7. rolls back to the previous image if either check fails.

Without `--publish`, it only deploys the current head. A `human` release is not
deployed until its approval file exists and the next cycle re-evaluates it.

State and logs:

```text
runtime/redeploy/state.json
runtime/logs/redeploy.log
runtime/github-delivery/approvals/PD-XXX.json
```

Stop the loop:

```sh
touch runtime/redeploy/stop
```

## Measured evidence

- **PD-065**: 12 test-only files, 117 lines → `auto`.
- GitHub publication: issue **#57**, PR **#58**, all **8/8** checks passed,
  squash-merged.
- Deployment: image `sha256:c86a6315…`, `parceldesk-web` recreated,
  `/` and `/api/refunds.csv` both healthy.
- **PD-056** dry-run: 4 test files, 34 lines → `auto`.
- **PD-066**: provider outcome unknown; reconciled at the full `$2.00`
  reservation, mission blocked `EXPOSURE_HELD`, no publication or deployment.

## Limits

- GitHub publication uses the host operator's `gh` login. No token is read by
  the cockpit, a model role or the pulse.
- The approval file is an operator record, not a cryptographic signature. Keep
  the repository and runtime directory private.
- The release train deploys the web surface. A backend production change that
  passes the human gate still needs the API image rebuilt; the current train
  does not silently deploy backend code.
- A model failure is not a policy failure. `blocked` missions are retained and
  never published.
- The `$30` ceiling is total local exposure, not a monthly provider counter.
