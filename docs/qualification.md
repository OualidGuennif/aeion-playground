# Qualification: fresh copy, first mission, restore

Measured **2026-10-25** on the operator's Mac. This report closes part of P7 and states
the remaining limits precisely. It is not a second-machine or off-host qualification.

## 1. Fresh installation from the public repository

A new directory outside the working copy was populated only from the public remote:

```sh
git clone https://github.com/OualidGuennif/aeion-playground.git <fresh>/aeion-playground
cd <fresh>/aeion-playground
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python scripts/check_publication.py
.venv/bin/python -m pytest -q
```

Measured:

- Clone HEAD `cd2206c`, tracked tree clean, no inherited `runtime/` or secrets.
- Python **3.12.8**; hash-enforced lock install succeeded.
- `scripts/check_publication.py`: passed
  (`Public index excludes private state, local authorization and symlinks.`).
- Full suite: **463 passed, 15 skipped**, 173.48 s.
- The opt-in Docker/stack tests were skipped unless explicitly enabled, exactly as in a
  normal install.

The operator then provisioned only the non-repository inputs a new host needs:
`scripts/prepare_stack.py` (fresh service secrets), `scripts/build_worker.py`,
`scripts/build_agents.py`, and the Compose images. No provider credential was copied
into the clone; the gateway mounted the operator's existing private store read-only.

## 2. First mission on the fresh installation

The stack ran as Compose project **`aeion-qual`** with a fresh `runtime/cockpit`, a
fresh host agent on port 8132, and no inherited ledger. All services were healthy and
the cockpit reported `agent ready`.

Submitted through the cockpit API:

```json
{"fixture": "PD00", "request_id": "fresh-install-pd00-20261025"}
```

Result: mission **`PD-001`**, status **blocked**, Gate **`NO_PATCH`**. The real worker ran
the baseline contract: **12 passed / 19 total, 7 failed, 0 errors** — the known baseline
drill result. Isolation was `docker-pytest/v1`, `network: denied`, container
`aeion-test-30903e5b4ccc4d82b6d31c0f3a0dd2cb`. No model was called and the cost was
**$0**. The packet verified (`sha256:6135968c1e3eba078bb91567a3ba949b5c6557cc980645f0f467c3967aeeb141`)
and is correctly `sealed: false` for a blocked mission.

This proves the fresh install's cockpit → host agent → disposable worker → Gate path.
It is a controlled baseline mission, **not** a paid inference qualification; paid
inference is proven separately on the main installation.

Both fresh product services also served normally: ``/`` on the cockpit, `/parceldesk`,
`/warehouse`, the product API on 8081 and the product web on 8125 all returned **200**.

## 3. Backup archive

With the live stack stopped, both SQLite databases were checkpointed and checked:

```text
runtime/cockpit/aeion.db:       0|0|0 ok
runtime/cockpit/parceldesk.db:  0|0|0 ok
```

Archive: `runtime/backups/cockpit-20261025T051850Z.tgz`, **3,434,606 bytes**, mode
**0600**, created with `tar -czf ... -C runtime cockpit`. The snapshot held:

- **28 missions**
- **376 events**
- **34 inference attempts**
- **24 tickets**, 0 open
- `product_head:product` = **PD-028**
- PD-024 packet `sha256:d725c08b…a8d05a9` valid
- PD-028 packet `sha256:7518c6d6…` valid

## 4. Restore into the independent installation

The archive was copied to the fresh clone, its separate `runtime/cockpit` was moved
aside, and the archive was extracted in place. Before any service started:

- `PRAGMA integrity_check` on both databases: `ok`
- counts identical to the snapshot: 28 missions, 376 events, 34 attempts, 24 tickets
- PD-024 and PD-028 packets valid
- `product_head:product` = PD-028

The independent installation was then started again (same `aeion-qual` project):

- all Compose services healthy
- cockpit health `ok`, agent `ready`
- `/`, `/parceldesk`, `/warehouse`, product web and product API: **200**
- ledger identical: `reported $0.132792`, `reconciled $4.000`, `exposure $4.132792`,
  no unsettled attempt
- 28 missions and 24 tickets visible from the restored store

No model was called during the restore.

After the drill the qualification stack was removed, the live stack was restarted from
the same archive's original directory, and the live agent returned to `ready`. Live
surfaces were rechecked: the product web still renders
`40.00 refunded · Damaged in transit · 2026-10-17` and the API still returns PD-1050.

## 5. Second drill: custom Compose project without an override

A second fresh clone of `main bccb28f` was installed the same way:

- full suite **465 passed, 15 skipped** (163.70 s), publication check pass;
- Compose project **`aeion-qual`**, no container-name override;
- the agent resolved **`aeion-qual-inference-gateway-1`** by the Compose service label,
  the inference probe returned `ready`, and the cockpit reported `agent ready`;
- `/`, `/parceldesk`, `/warehouse`, the product API and the product web all returned
  **200**; `GET /api/refunds.csv` served the header and the PD-1050 row;
- first mission `PD-001` (`PD00`, `fresh2-pd00-20261025`) ran the real worker
  **12/19** (seven known baseline failures) and Gate `NO_PATCH`; packet
  `sha256:1c370bf84fdbdf5a9aeb85121e48acb92041911bf508a502ab07acc5ba492c97`
  verified and is correctly `sealed: false`;
- the fresh ledger held 1 mission / 11 events; no model was called and the cost was $0.

The first attempt at this second drill reused the previous `aeion-qual` project volumes,
whose Postgres password no longer matched the newly generated secret. Removing only
that temporary project's volumes fixed it. A genuinely new project name does not have
this issue.

This closes the custom-project container-name limit from the first drill. The
independent-directory restore was not repeated in the second drill.

## Limits

1. **Same physical host and Docker engine.** This is a fresh *copy* install and an
   independent-directory restore, not a second machine. True off-host restore remains
   untested.
2. **No paid mission on the fresh install.** The first fresh mission was the controlled
   `PD00` baseline. Live paid inference is proven on the main installation.
3. **Custom Compose project names are now supported.** The first drill needed a one-line
   container-name override because the agent resolved the hardcoded default name. PR #42
   added label-based discovery, and the second drill ran `aeion-qual` with no override.
   Two simultaneous stacks on one engine are still refused by design.
4. **Global image tags couple installations on one Docker engine.** Building the worker
   and role images from the second clone overwrote the shared tags, so the first
   installation's `runtime/*-image.json` records no longer matched. Re-recording them
   with `build_worker.py`/`build_agents.py`, recreating the gateway and restarting the
   agent restored readiness. A real second host has its own tags and is unaffected.
5. **The archive contains cockpit state only.** Provider credentials, the executor
   token, the product H2 volume, PostgreSQL and Paperclip volumes are separate. A new
   host must provision them; the restored product services used fresh seed data, so the
   restore proves the cockpit ledger, tickets, releases and packets, not product data.
6. **No disk-corruption or partial-write simulation.** The archive was created after a
   clean stop and SQLite checkpoint.
