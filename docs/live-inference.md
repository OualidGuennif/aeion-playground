# Live provider inference: measured status

Status: **one live provider-backed role completed through the gateway.** A full live
mission, a hosted provider-backed CI run and the free tier remain unqualified. This file
records both the refusal and the successful run; do not turn either into a broader claim.

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
sealed evidence without publishing a local release.

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
- GitHub delivery or a Factory-ticket release: PD01 has no delivery target, and the
  Paperclip issue was refused with HTTP 403.
- A durable credential refresh path. The Console access token has an expiry; the gateway
  does not refresh OAuth. Re-export and rewrite the gateway file before it expires.

## Security properties preserved

- The role container has no provider credential, no Docker socket and no host mount; it
  receives a one-use lease token only.
- The gateway fixes the upstream host and path, bounds request/response sizes, refuses
  redirects and automatic retries, and records the lease state.
- The live credential is owner-only, gitignored, never printed and never in Git.
- A refused or unknown outcome holds the lease; only the Gate may choose another attempt.
