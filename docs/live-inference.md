# Live provider inference: measured status

Status: **blocked on a provider credential, not on code.** A real probe reached the
inference gateway and the upstream provider on 2026-10-25 and was refused. This is not
live inference proof; it is the exact failure to fix before claiming that gate.

## What was attempted

1. **A direct gateway lease.** The gateway was asked for a one-use lease for
   `opencode/deepseek-v4.1-flash-free`, then called the fixed
   `/inference/opencode/v1/chat/completions` route with a controlled prompt.
2. **A real OpenCode role through the host executor agent.** The same model was run in the
   isolated role container by the real CLI, through the same gateway.

Both returned HTTP 403 from the upstream provider:

```
OpenCode provider (HTTP 403): free tier can only be used from within OpenCode;
automatic retry forbidden
```

The lease is recorded as `refused`; no tokens were reported. The gateway did not retry.

## Why

- `GET http://127.0.0.1:8130/health` reports `providers_configured: ["opencode"]`.
  That is the **public** fallback: `OpenCodeTransport.credentials()` returns `"public"`
  when no `OPENCODE_AUTH_FILE` is mounted.
- The running gateway container has no `/run/provider-auth/auth.json` mount and no
  `OPENCODE_AUTH_FILE` environment variable. `compose.provider.yaml` exists, but the stack
  was started without `scripts/stack.py --provider-auth`.
- The paid `opencode-go` provider is not configured, so the gateway cannot fall back to it.
- The operator's OpenCode desktop session is authenticated inside the desktop app, but that
  credential is not a file under `runtime/` and must not be extracted from the app's secure
  store. The trusted gateway is the only component allowed to hold it.

## What enables a real run

1. Create an OpenCode auth store with the operator's key:

   ```sh
   scripts/opencode_auth.sh          # interactive login; writes runtime/opencode/data/opencode/auth.json
   ```

   The repo's vendored CLI must exist first (`vendor/opencode`, OpenCode 1.19.4). Do not
   copy the file into Git, a task, a worker or a model prompt.

2. Start the stack with that file mounted **only** into the trusted gateway:

   ```sh
   .venv/bin/python scripts/stack.py --provider-auth runtime/opencode/data/opencode/auth.json up
   ```

   The gateway reads it as `/run/provider-auth/auth.json`; the cockpit, the agent's role
   containers and the test workers do not see it.

3. Authorize a bounded local policy. Copy `config/model-routing.example.json` to the ignored
   `config/model-routing.json`, set a **future** `authorization_expires_at`, a small
   `budget_usd`, and models that match the configured provider. The current local file is
   deliberately expired (`2000-01-01T00:00:00Z`).

4. Re-run the live probe, then a real mission (for example PD01) from
   `http://127.0.0.1:8124`. Only the after-state is live proof; the controlled gateway test
   (`AEION_INFERENCE_TESTS=1`) remains a boundary test, not a provider call.

## What this does not change

The gateway still enforces one-use leases, a fixed upstream host and path, bounded request
and response sizes, no redirects and no automatic retry. A refused or unknown outcome holds
the lease; only the Gate may choose another attempt. Provider credentials never enter a
worker, the cockpit, a prompt, the ledger or Git.

## Measured on this machine

- Gateway health: `providers_configured: ["opencode"]`, no auth mount.
- Direct lease + completion: HTTP 403, `state: refused`, `usage: null`.
- Real role container through the agent: `status: failed`, same refusal, 0 tokens.
- No orphan role container was observed after the failed run.
