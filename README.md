# aeion-playground

A local AI engineering playground: task admission, model patch, real tests, model review,
deterministic Gate, immutable evidence and a verified local Git artifact.

This public source starts from the working ParcelDesk proof of concept. It contains the
application and regression tests, without private frameworks, credentials, sessions or
historical runtime databases. The accepted cockpit and ParcelDesk design are preserved.

The [interactive architecture map](knowledge/aeion-playground-architecture.html) shows the
components, the trust boundaries and the executor path in one page.

## Current status

Canonical test execution defaults to disposable Docker workers with a frozen image identity,
non-root user, read-only root/source snapshot, no network, no host home or Docker socket,
and CPU/RAM/PID/scratch/file/deadline bounds. Worker and delivery use distinct containers.
The author and reviewer CLI now default to separate disposable Docker jobs, with no
source mount or provider credential. A trusted mediator limits each capability to one
admitted completion and keeps provider credentials outside the roles. The trusted
controller is native. Canonical calls reserve the remaining mission envelope before IO;
unknown usage stays held. Complete reconciliation and GitHub delivery are still work. See
[the phase gates](docs/roadmap.md); pending controls remain explicit.

## See the demo in ten minutes

The demo now has a second, larger product surface:

- **Mission Control** — the mission, the model runs, the Gate decision, the sealed evidence
  and the telemetry counters. One SQLite ledger.
- **ParcelDesk product** — a Quarkus 3.40 / Hibernate / **Java 25.0.4** API with H2
  persistence and a Deno 2.5 / Fresh / Three.js operations floor at
  <http://127.0.0.1:8125>. It has orders, refunds, a real review ledger and an autonomous
  Factory ticket → pull-request flow. See
  [the product guide](docs/parceldesk-product.md).
- **ParcelDesk legacy** — the original shipping quote, orders and support desk at
  <http://127.0.0.1:8124/parceldesk>.
- **Warehouse** — the original procedural Three.js fulfilment floor, backed by the legacy
  order API, plus a Factory tab that files a real mission.

Run it:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python scripts/build_worker.py      # frozen test worker image
./run.sh                                      # trusted controller on 127.0.0.1:8124
```

Then open <http://127.0.0.1:8124> and <http://127.0.0.1:8124/parceldesk>.

The new product is Compose-only (the native quick start keeps the original surfaces):
`docker compose build parceldesk-api parceldesk-web` then
`docker compose up -d --wait parceldesk-api parceldesk-web`, and open
<http://127.0.0.1:8125>. The first Factory ticket filed there targets the Quarkus/Deno
source, not the Python pricing slice.

### Or run the cockpit in Docker

The cockpit has its own image and **no Docker socket**. Test workers, author and reviewer
containers are launched by a trusted agent that stays on the host:

```sh
.venv/bin/python scripts/prepare_stack.py
.venv/bin/python -m app.executor_agent --bind 127.0.0.1 --port 8131 &
AEION_OPERATOR_UID=$(id -u) AEION_OPERATOR_GID=$(id -g) \
  docker compose up -d --wait postgres paperclip otel-collector inference-gateway cockpit
```

Open <http://127.0.0.1:8124>. The cockpit reaches the agent through a capability token in
`runtime/secrets/executor_agent`; nothing else from the host is mounted into it. On Linux,
bind the Docker bridge gateway instead (`--bind 172.17.0.1`) and start Compose with
`AEION_EXECUTOR_URL=http://172.17.0.1:8131`. Without the agent the UI still boots and
reports the executor unavailable; there is no automatic fallback to a socket.

The full mission loop also needs the worker image, the gateway image and an explicitly
authorized routing policy. See [running the demo in containers](docs/demo-deployment.md).

What to click, in order:

1. **Launch PD01** — a deliberately broken pricing rule. The model proposes a patch; a
   different model family reviews it; disposable containers run the real test suite; the
   Gate decides on the JUnit report, not on what the model said.
2. Open the mission and read the **evidence packet**, then verify it: its SHA-256 digest
   covers the tests, the diff and the environment identity. It is a digest seal, not a
   cryptographic signature.
3. Open **Observability** — the outbox counters and the collector state. `pending` is not an
   error you can ignore; it means delivery has not been acknowledged yet.
4. Launch **PD03** to see the Gate *deny* a patch that touches a protected path. A refused
   mission is a correct outcome, not a bug.
5. Open <http://127.0.0.1:8125> — orbit the Three.js floor, refund a parcel, file a product
   ticket and watch its sealed mission become a reviewed, auto-merged pull request. Then
   open <http://127.0.0.1:8124/warehouse> for the legacy floor; drag it, issue a refund from
   the support desk, then file a ticket from the **Factory** tab. The ticket becomes a bounded mission,
   not a comment thread.

Missions run on fixed fixtures (`PD00`–`PD04`) and on free-form tickets admitted from the
warehouse Factory tab. The pricing rules they touch are in `app/pricing.py` and
`tests/test_pricing.py`.

## Configure inference, budget and Paperclip

Copy the local template once; `.env` and `runtime/` are gitignored.

```sh
cp .env.example .env
```

### 1. OpenCode provider credential

There are two paths. **Do not mix them.**

**Docker gateway (the tested path).** The provider volume is mounted only into the
inference gateway; the cockpit, host agent, roles and workers never receive it.

```sh
./scripts/opencode_auth.sh
cp runtime/opencode/data/opencode/auth.json runtime/secrets/opencode-provider.json
chmod 600 runtime/secrets/opencode-provider.json
.venv/bin/python scripts/stack.py --provider-auth runtime/secrets/opencode-provider.json up
```

`OPENCODE_API_KEY` in `.env` is **not** read by this Docker path. It is for the native
runtime (`AEION_INFERENCE_BACKEND=native`), where `scripts/opencode_auth.sh` is also
accepted. Never commit the auth store, copy it into a role, or paste it into a prompt.

### 2. Global model-spend cap

Set the local daily guard in `.env`:

```dotenv
AEION_BUDGET_USD=15
```

The effective daily ceiling is
`min(AEION_BUDGET_USD, config/model-routing.json budget_usd)`: the environment can only
**lower** the authorized policy, never raise it. The ledger applies it to exposure booked
during the current **UTC day**; the window resets automatically at `00:00 UTC`, so a
completed day never becomes a permanent stop. The Docker stack passes this value into the
cockpit, so the same cap applies to the container and to the host scripts. Lifetime
exposure is still reported for audit, but it is not the admission gate. To change the
daily authorization, edit `config/model-routing.json` (`budget_usd` and
`authorization_expires_at`) as the operator, then restart the stack. A provider monthly
limit still has to be set separately in the OpenCode Console.

The live ledger keeps **unknown upstream settlements** at the full reservation. An
attempt that loses its usage record is charged its whole `$2.00` (or `$1.995907`)
reservation rather than silently zeroed. That conservative charge counts in the UTC day
in which the attempt started; it is never refunded automatically. To go past a daily
limit, wait for the next UTC day or raise the operator policy deliberately.

### 3. Paperclip workboard

Paperclip is the issue/monitoring view; **Aeion remains the execution engine**. Its own
generic agent can stay paused — the yellow “agents paused” banner does not stop Factory
missions.

```sh
.venv/bin/python scripts/prepare_stack.py
docker compose up -d --wait postgres paperclip
# open http://127.0.0.1:3101, create your account, choose "Claim this instance"
.venv/bin/python scripts/connect_paperclip.py
docker compose up -d --wait cockpit
```

`connect_paperclip.py` uses the browser-approved board grant and writes two private
files: `runtime/secrets/paperclip-board-auth.json` and the derived
`runtime/secrets/paperclip_api_token` (mode `0600`). The cockpit mounts only the latter
as `/run/secrets/paperclip_api_token`; the bearer is never placed in `.env`, `docker
inspect`, logs or model context. If you skip the connection, Paperclip health still says
`ok` but the cockpit reports `403 Board access required` and no issues are linked.

### 4. See the whole demo in one command

```sh
.venv/bin/python scripts/demo_status.py
```

It prints the cockpit/agent state, ledger counts, reported and exposure budget, Paperclip
board/issue count, and the product API/web checks. It reads loopback endpoints only; it
does not call a model. Then open <http://127.0.0.1:8124>, <http://127.0.0.1:8125> and
<http://127.0.0.1:3101>.

## Get an improvement into the demo

Tickets are the specification the Factory reads. Open a
[ParcelDesk improvement issue](.github/ISSUE_TEMPLATE/parceldesk-improvement.yml): the form
asks for an observable outcome, an area, a risk level and acceptance checks that can become
tests. Vague tickets become blocked missions with a reason, which is the intended behaviour.

Be precise about what is wired today: a GitHub issue is the **specification**, and the
mission that implements it is still started by the operator. Reading GitHub issues and
opening pull requests automatically is gate **GitHub delivery**, marked *Pending* in
[the roadmap](docs/roadmap.md). Do not assume a filed GitHub issue runs by itself yet.

The **Factory** tab in `/warehouse` is the wired path: fill in the title, observable
outcome, area, risk and acceptance checks, and the cockpit admits the ticket as an `agent`
mission. The author proposes a patch, disposable workers run the suite, the Gate re-runs the
trusted tests, a different model family reviews the diff, and the Gate decides. A blocked
mission is a normal outcome; its reason is in the ledger. A high-risk ticket waits for the
owner's approval instead of self-approving.

The accepted patch becomes a **local Git release** under `runtime/releases/`. It is not
hot-deployed to the quote reference; the release is the artifact to inspect and promote.

## Keep the Factory alive

The cockpit is not a replay: every mission below is a real model patch, a real
review, a trusted test run, a Gate decision and a sealed local release. To make
the board move without clicking, run the host-side pulse:

```sh
.venv/bin/python scripts/pulse.py --dry-run
.venv/bin/python scripts/pulse.py --interval-minutes 12 --max-runs 24 --max-exposure-usd 20
```

It admits a low-risk product ticket every 12 minutes (five per hour), waits for
each mission, and stops on the local exposure cap, an unsettled attempt, a
human checkpoint, three consecutive failures or `touch runtime/pulse/stop`. It
never approves a checkpoint, pushes or deploys. The checked-in backlog is
`config/pulse-tickets.json`; the full guide, safety rules and measured cost are
in [docs/autonomous-demo.md](docs/autonomous-demo.md).

The UI already polls `/api/snapshot` every 1.2 seconds, so new missions and
agent steps appear while the pulse runs. Paperclip gets one issue per mission.

## Publish and deploy through deterministic gates

A sealed mission is not automatically publishable. `config/publication-policy.json`
adds a file-and-size gate: small test/UI changes can publish and deploy
automatically; backend production code, the API proxy, the Fresh manifest,
Dockerfiles and oversized diffs require an operator approval file; protected
paths are denied. The same policy is applied to the actual GitHub PR diff.

```sh
# Dry-run one release against the policy
.venv/bin/python scripts/deliver_github.py --mission PD-065 --auto --dry-run

# Publish only if the policy says auto; waits for 8 checks and merges
.venv/bin/python scripts/deliver_github.py --mission PD-065 --auto --wait --merge

# Publish auto releases and redeploy the web image four times a day
.venv/bin/python scripts/redeploy.py --loop --interval-hours 6 --publish
```

The deploy step builds the sealed release, recreates `parceldesk-web`, checks
`/` and `/api/refunds.csv`, and rolls back to the previous image on failure.
The full rule set, approval flow and measured evidence are in
[docs/release-gate.md](docs/release-gate.md).

## Install and run

Requires Python 3.12, Git and a running Docker Engine/Desktop with Linux containers.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python scripts/build_worker.py
AEION_TEST_BACKEND=docker AEION_DOCKER_TESTS=1 .venv/bin/python -m pytest -q
./run.sh
```

Open [Mission Control](http://127.0.0.1:8124) or [ParcelDesk](http://127.0.0.1:8124/parceldesk).
The trusted controller binds loopback only. Do not expose it publicly.

For the persistent workboard, run:

```sh
.venv/bin/python scripts/prepare_stack.py
docker compose up -d --wait postgres paperclip
```

Open [Paperclip](http://127.0.0.1:3101), create your account and choose **Claim this
instance**. This is a fresh authenticated instance, with PostgreSQL and application data
in named volumes. It does not import another installation or impersonate its operator.
See [the stack runbook](docs/local-stack.md) for integration and recovery boundaries.
The native controller connects through browser-approved board access; no second provider
is needed for the Factory roles.

A pinned local OpenTelemetry collector receives metadata through a transactional SQLite
outbox, with separate private audit delivery and explicit pending/unavailable states.
See [telemetry operations and qualification](docs/observability.md).

Build and start the isolated inference runtime separately:

```sh
.venv/bin/python scripts/build_agents.py
.venv/bin/python scripts/stack.py up
AEION_INFERENCE_TESTS=1 .venv/bin/python -m pytest -q tests/test_docker_inference.py
```

This qualification uses a controlled local response, with the real OpenCode CLI and
container boundary. It does not call a provider. See [the inference contract](docs/inference.md)
before explicitly connecting an OpenCode account and authorizing tasks.

`scripts/build_worker.py` resolves the built image to an immutable local ID. That ID,
executor code and execution limits are frozen at task admission. A missing image or Docker
daemon fails closed. Docker workers receive only a bounded regular-file snapshot of
`app/`, `tests/` and `pytest.ini`; `.git`, policy, credentials and runtime state are excluded.
Scratch is a bounded tmpfs; stdout and the JUnit wire report are bounded and parsed by the
controller. A worker report remains untrusted and is not an independent acceptance oracle.

The older qualified macOS pytest boundary is available explicitly with
`AEION_TEST_BACKEND=macos`; it is never an automatic fallback from Docker failure.

Model calls require an explicitly configured local `config/model-routing.json` and a
provider credential. Without them, the expired example policy refuses new calls. The
short configuration is above; the full evidence is in
[`docs/live-inference.md`](docs/live-inference.md) and
[`docs/local-stack.md`](docs/local-stack.md). Provider login and Paperclip setup are
operator actions; merely opening the UI performs no inference. Runtime state is under
`runtime/`, ignored by Git. Credentials belong in local configuration only.

The Gate requires human approval for pricing changes. Tests-only tasks on an accepted
local product can complete autonomously after checks and a different-family model review.
Model identity is the CLI-selected label, not a provider attestation of independent weights.

See [RUNBOOK.md](RUNBOOK.md) for operations and [SECURITY.md](SECURITY.md) for trust boundaries.
