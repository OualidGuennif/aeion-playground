# aeion-playground

A local AI engineering playground: task admission, model patch, real tests, model review,
deterministic Gate, immutable evidence and a verified local Git artifact.

This public source starts from the working ParcelDesk proof of concept. It contains the
application and regression tests, without private frameworks, credentials, sessions or
historical runtime databases. The accepted cockpit and ParcelDesk design are preserved.

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
