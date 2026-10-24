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

This closes the persisted-run/usage crash window. It does not yet reserve exposure before
inference or recover unreported provider usage after a lost stream. Unknown exposure holds
and a durable event outbox are separate pending gates, not guarantees supplied by this
transaction. Reported-usage checks do not impose a provider billing cap.
