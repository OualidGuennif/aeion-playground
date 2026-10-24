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

- A full mission (worker tests, Gate-owned acceptance, review, Gate decision) with live
  inference; the live run exercised only the role transport.
- A hosted CI provider-backed run; CI stays on controlled fixtures.
- The free tier; it remains refused outside OpenCode's own client path.
- A durable credential refresh path. The Console access token has an expiry; the gateway
  does not refresh OAuth. Re-export and rewrite the gateway file before it expires.

## Security properties preserved

- The role container has no provider credential, no Docker socket and no host mount; it
  receives a one-use lease token only.
- The gateway fixes the upstream host and path, bounds request/response sizes, refuses
  redirects and automatic retries, and records the lease state.
- The live credential is owner-only, gitignored, never printed and never in Git.
- A refused or unknown outcome holds the lease; only the Gate may choose another attempt.
