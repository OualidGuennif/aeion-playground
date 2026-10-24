# Operations

Start from the README. Native services and optional Docker services must use separate
ports/data directories from any existing installation. Never reset an old runtime to
make a demonstration succeed.

The ledger preserves queued tasks, failed attempts and checkpoints. Interrupted inference
remains unknown and is never automatically replayed. Existing sealed evidence is immutable.

Local contribution without a model:

```sh
.venv/bin/python scripts/submit_local_patch.py --help
.venv/bin/python scripts/factory_queue.py --help
.venv/bin/python scripts/deliver_local.py --help
```

To authorize model work, copy `config/model-routing.example.json` to the ignored local
`config/model-routing.json`, explicitly set a future authorization expiry and a budget
within the supported envelope, then configure the provider. The example is deliberately
expired. A changed admitted policy blocks old work; repeated request IDs do not rerun calls.

The reported usage budget is not a provider billing cap and excludes subscriptions.
Human approval cannot be supplied by model output. A passed test count alone cannot
approve protected-path changes or replace a valid independent review.

Paperclip is an external workboard integration. If unavailable, the UI reports it unavailable;
no external issue, running agent or result is invented. Framework integrations are optional
local references and are never fetched from sibling/private folders automatically.
