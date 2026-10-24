# Adaptive routing contract

Owner authorization is explicit in the local routing policy: reported-usage envelope,
existing per-mission Gate, authorized OpenCode routes and a timezone-qualified expiry.
The public example is expired and grants no new model calls. Credentials and billing
settings remain outside the routing policy. No automatic top-up or external vendor.

The local non-secret `config/model-routing.json` defines the authorized pool and defaults.
Each new task freezes that policy and its digest. Later policy drift blocks execution;
old admissions retain their original runtime contract. Each actual attempt records the
selected CLI model, reason, session, timing, reported usage and outcome. The pool starts
with the two already-tested Flash families; more expensive models need measured benefit.

Per role, rank candidates using actual recent results for the same task class and role.
Separate availability errors from deterministic task failures and protocol failures.
Prefer the role's default family when evidence is sparse. Repeated measured failures
penalize a family; sufficiently sampled cost and latency break quality ties. A failed
transport call is not evidence of poor code quality. Review opinions alone are not truth.

Try at most one eligible free candidate, then one authorized paid candidate on a recognized
availability refusal. Known free refusals have a persisted cooldown; expiry permits one
new attempt when useful work exists, not an assertion that quota reset. Settings/auth
refusals require an operator policy revision or a later genuine success. Timeouts and
unknown outcomes do not trigger another external call. No retry on semantic rejection.

The reviewer excludes the actual author's family, including an author selected by fallback.
The Gate, protected paths, task scope, tests, human checkpoints and immutable evidence
remain authoritative. Failed attempts retain their costs. Budget and authorization expiry
are checked before every call. Selecting a model never approves a patch.

The feedback loop is deterministic analysis of the existing ledger after runs and Gate
results. It updates availability facts and a bounded analysis projection, without another
LLM judging its own performance or generating unsolicited tasks. No provider weights or
independence are attested by a CLI model label. The budget is reported-use accounting,
not a provider-enforced billing cap or the subscription's fixed fee.
