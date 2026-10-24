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

The demo is three surfaces over one SQLite ledger:

- **Mission Control** — the mission, the model runs, the Gate decision, the sealed evidence
  and the telemetry counters.
- **ParcelDesk** — the product: a shipping quote, orders and a support desk for refunds.
- **Warehouse** — a procedural Three.js fulfilment floor with moving parcels and workers,
  backed by the same order and refund API, plus a Factory tab that files a real mission.

Run it:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python scripts/build_worker.py      # frozen test worker image
./run.sh                                      # trusted controller on 127.0.0.1:8124
```

Then open <http://127.0.0.1:8124> and <http://127.0.0.1:8124/parceldesk>.

### Or run the cockpit in Docker

The cockpit has its own image and **no Docker socket**. Test workers, author and reviewer
containers are launched by a trusted agent that stays on the host:

```sh
.venv/bin/python scripts/prepare_stack.py
.venv/bin/python -m app.executor_agent --bind 127.0.0.1 --port 8131 &
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
5. Open <http://127.0.0.1:8124/warehouse> — drag the floor, issue a refund from the support
   desk, then file a ticket from the **Factory** tab. The ticket becomes a bounded mission,
   not a comment thread.

Missions run on fixed fixtures (`PD00`–`PD04`) and on free-form tickets admitted from the
warehouse Factory tab. The pricing rules they touch are in `app/pricing.py` and
`tests/test_pricing.py`.

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

Model calls require an explicitly configured local `config/model-routing.json`. Without
it, the expired example policy refuses new calls. Provider login and optional Paperclip
setup are operator actions; merely opening the UI performs no inference. Runtime state
is under `runtime/`, ignored by Git. Credentials belong in local configuration only.

The Gate requires human approval for pricing changes. Tests-only tasks on an accepted
local product can complete autonomously after checks and a different-family model review.
Model identity is the CLI-selected label, not a provider attestation of independent weights.

See [RUNBOOK.md](RUNBOOK.md) for operations and [SECURITY.md](SECURITY.md) for trust boundaries.
