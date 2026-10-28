# Keep the Factory alive

The cockpit shows one mission at a time, so an idle board proves nothing. The
**pulse** is host-side operator tooling that admits a bounded sequence of
low-risk product tickets on a cadence. Each ticket is a real Factory mission:
a model author proposes a patch, a different model family reviews it, the
trusted Maven/Deno suites run in disposable containers, the Gate decides, and a
`CLEAN` result is sealed and delivered as a local Git release.

The pulse never approves, seals, merges, pushes or changes a policy. It is not
part of the cockpit and it cannot make the Gate pass.

## Ten-minute tour

```sh
cd /Users/wallseven/Desktop/AEion/aeion-playground
.venv/bin/python scripts/demo_status.py
```

Open:

| Surface | URL | What to watch |
|---|---|---|
| Mission Control | <http://127.0.0.1:8124> | new missions, agent steps, tests, Gate decisions, sealed evidence |
| Warehouse / Factory | <http://127.0.0.1:8124/warehouse> | the ticket backlog and its linked missions |
| ParcelDesk product | <http://127.0.0.1:8125> | orders, refunds, the CSV download and the review desk |
| Paperclip | <http://127.0.0.1:3101> | one issue per mission, with the Gate outcome |
| Observability | <http://127.0.0.1:8124/observability> | cost, latency, routing and event delivery |

A mission is not a chat reply: it is a persisted state machine with steps,
model runs, a diff, a JUnit report, a review verdict and a sealed packet. A
`blocked` mission is a normal, honest outcome. A `HUMAN_CHECKPOINT` is not a
failure; it is the Gate refusing to self-approve.

## Start the pulse

```sh
.venv/bin/python scripts/pulse.py --dry-run
.venv/bin/python scripts/pulse.py \
  --interval-minutes 12 \
  --max-runs 24 \
  --max-exposure-usd 20
```

`--interval-minutes 12` gives **five admissions per hour**. The default
`--max-runs 24` covers about five hours; `--max-exposure-usd 20` stops the
pulse before the authorized `$30` ceiling, leaving a buffer for other work.

Run it in the background so it survives the terminal:

```sh
mkdir -p runtime/logs runtime/pulse
nohup .venv/bin/python scripts/pulse.py \
  --interval-minutes 12 --max-runs 24 --max-exposure-usd 20 \
  > runtime/logs/pulse.out 2>&1 &
echo $! > runtime/pulse/pid
```

Watch it:

```sh
tail -f runtime/logs/pulse.out
cat runtime/pulse/status.json
```

Stop it cleanly:

```sh
touch runtime/pulse/stop
# or: kill "$(cat runtime/pulse/pid)"
```

The stop file is checked before every admission and during every sleep. The
pulse also stops on `SIGINT`/`SIGTERM`.

## What it admits

`config/pulse-tickets.json` is an operator-editable backlog. The checked-in
backlog contains **seventeen** tickets: twelve deterministic frontend test-only
tickets, four small visible frontend features (refund-window days, high-value
label, refund deadline, CSV link label) and one backend summary change. They use the same
low-risk product path as PD-053, which completed autonomously with `CLEAN`:
23/23 tests, two model runs, `$0.002819` and a local release.

The three frontend features can pass the deterministic publication policy
automatically when they stay within the file/line/critical-path bounds. The
backend summary change touches backend production code, so it is sealed and
delivered locally but **not published or deployed** until an operator approval
file exists. See [the release gate](release-gate.md).

Each ticket names exactly one new test file, imports an existing pure function
from `product/frontend/lib/format.ts`, and adds deterministic assertions. The
ticket text forbids production changes. A successful mission adds one test
file, advances the local product head and creates a Paperclip issue.

To add your own work, append an object with these fields:

```json
{
  "id": "frontend-my-check",
  "title": "Add my regression test",
  "outcome": "The frontend tests cover the behavior I care about.",
  "area": "warehouse",
  "risk": "low",
  "acceptance": "Create exactly product/frontend/tests/my_check_test.ts ...",
  "constraints": "Add one frontend test file only.",
  "product": "product",
  "target_file": "product/frontend/tests/my_check_test.ts"
}
```

`id` and `target_file` are pulse bookkeeping; they are not sent to the
cockpit. The API fields are `title`, `outcome`, `area`, `risk`, `acceptance`,
`constraints` and `product`.

## Safety model

- **Budget:** the pulse reads `budget_remaining`, `budget_total` and
  `budget_unsettled_attempts` from `/api/snapshot`. It stops at the exposure
  cap, below the `$2.00` per-mission reservation floor, or on any unsettled
  attempt. It never reconciles or refunds automatically.
- **One mission at a time:** it waits for the active mission to become
  terminal before admitting the next one.
- **Human checkpoints:** if a mission returns `awaiting_human`, the pulse
  writes `state=waiting_human`, logs the mission and exits. It never calls
  `/approve`. The operator must inspect and approve or resolve it, then
  restart the pulse; the used-ticket set prevents repeating earlier work.
- **Failure streak:** after three consecutive blocked/failed missions the
  pulse stops instead of spending more.
- **No protection bypass:** the ticket still goes through the same policy,
  protected-path check, trusted tests, independent review and Gate. A weak
  ticket is blocked by the Gate like any other mission.
- **Local only:** the pulse admits missions and seals local releases. It does
  not push or deploy. The separate release train applies the deterministic
  publication policy before GitHub and deployment; critical changes wait for
  the operator approval file.
- **State:** `runtime/pulse/status.json` records used tickets, cursor, last
  mission, last Gate decision, last delivery and budget. `--reset-state`
  clears it; use that only when you intend to replay the backlog.

## Reading the result

A healthy admission looks like this in `runtime/logs/pulse.out`:

```text
admitted frontend-money-edge as PD-054 (ticket TCK-...)
PD-054 · running · Patch
PD-054 · running · Tests
PD-054 · running · Review
PD-054 completed · CLEAN · delivery 1a2b3c...
next admission at 2026-10-28T00:12:00+00:00
```

The corresponding mission in Mission Control shows:

- the author and reviewer model runs with tokens and cost;
- the exact diff (`+N/-0`) and the test report;
- the review verdict and the Gate checks;
- the sealed packet digest and the local delivery commit.

A blocked ticket is also useful: the Gate reason tells you whether the model
produced an invalid diff, the tests failed, the patch touched a protected path
or the worker report was unusable. That is the Factory being honest, not the
demo being broken.

## Publication and four daily deployments

```sh
.venv/bin/python scripts/redeploy.py --loop --interval-hours 6 --publish
```

Every six hours the train reads the current product head, applies
`config/publication-policy.json`, publishes an `auto` release to GitHub,
rebuilds the sealed web image, recreates `parceldesk-web`, checks `/` and
`/api/refunds.csv`, and rolls back on failure. A `human` release is skipped
until `scripts/approve_github.py` writes an approval file. Full rules and
commands: [the release gate](release-gate.md).

## Measured baseline

| Run | Ticket | Result | Cost |
|---|---|---|---|
| PD-053 | frontend status-label test | `CLEAN`, 23/23, local release `e00f182b` | `$0.002819` |

At the measured cost, five admissions per hour cost roughly **`$0.014/hour`**.
The actual cost varies with model output and failures; the pulse stops on the
caps above regardless.

## Troubleshooting

- `pulse stopped: cockpit unavailable` — start the stack and rerun
  `scripts/demo_status.py`.
- `pulse stopped: ... unsettled inference attempt(s)` — reconcile the attempt
  in the cockpit before new calls. The pulse deliberately does not guess.
- `awaiting_human` — inspect the mission, then approve it with the operator
  endpoint or leave it blocked. The pulse is already stopped.
- `admission failed (HTTP 422)` — the backlog ticket is invalid. Fix
  `config/pulse-tickets.json`; the pulse validates it before starting.
- `backlog exhausted` — all tickets were used. Add more tickets or run
  `--reset-state` only if you want to repeat the same files.
