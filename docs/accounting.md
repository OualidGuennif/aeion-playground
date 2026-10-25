# Reported-usage accounting

Each new model result and its mission's cost/input/output token increments commit in one
SQLite transaction. The run ID is immutable: an identical replay is a no-op; a conflicting
record is refused. A failure between inserting the run and incrementing usage rolls back
both. Two connections cannot settle the same run twice. Known cost is stored at its reported
precision rather than rounded to the display DTO. New results require a running mission;
terminal history can only receive an exact idempotent replay, without changing its totals.

Reported costs must be finite/nonnegative and token counts nonnegative integers. These
checks prevent invalid counters from silently changing the budget. Run identity is bounded
and nonempty. Historical sealed evidence is never backfilled or rewritten by this operation.

Canonical Docker admissions also require a reservation **before** the adapter performs
inference. The Gate owns the attempt UUID; the mediator's lease is linked to it before the
role container starts. A result cannot move to another model, role, mission or reservation.
Recording the immutable result, mission usage and reservation outcome is one SQLite
transaction. A failure rolls back all three; an identical result replay still charges once.

The conservative reservation covers the entire remaining **2 USD mission envelope**.
Dispatch is serialized while an attempt is open. Reported spend, immutable carried spend
from a previous installation and held exposure must fit the admitted total budget (at
most 100 USD). If the full remaining envelope will not fit, no call starts. This may stop
earlier than a prediction of the next token bill; it does not speculate about provider
pricing or reset the budget when a routing policy changes.

The hold is released only for a mediator-bound complete usage record matching the CLI,
a recorded zero-usage provider refusal, or proof the request was never dispatched. A
completed CLI with missing/mismatched accounting cannot proceed to Gate acceptance.
Malformed model output can still have known billable usage; settling its cost does not
approve its patch or retry it. Reported overruns are retained and stop further inference.

A timeout, lost receipt, interruption or unknown usage remains held across restart and
authorization expiry. It blocks new inference globally, including a new request ID or
paid fallback. There is no automatic age-based refund. Editing a terminal mission or
inventing a zero cost is not a recovery procedure. Persisted gateway receipts and
immutable run logs must be preserved for that investigation.

`Store.reconcile_unknown_attempt(run_id, note)` is the one operator settlement: it
charges the attempt's **full held reservation** as `reconciled_usd`, clears the block and
keeps the original reservation in the row. It cannot understate the provider bill, and it
is not an invoice match; it is the conservative choice when the upstream stream exceeded
the mediator's response bound and no usage record exists. The note is required and stored
with the attempt. Reconciled charges count in `exposure_usd` and are never refunded
automatically. Explicit native inference remains legacy diagnostics outside this
qualified reservation protocol; historical results/seals are not backfilled.

Reported costs and catalog-based CLI estimates remain distinct from an actual provider
invoice. These local reservations do not impose a provider billing cap. A durable event
outbox and complete operator reconciliation tooling are separate qualification work.
