# aeion-playground

A local AI engineering playground: task admission, model patch, real tests, model review,
deterministic Gate, immutable evidence and a verified local Git artifact.

This public source starts from the working ParcelDesk proof of concept. It contains the
application and regression tests, without private frameworks, credentials, sessions or
historical runtime databases. The accepted cockpit and ParcelDesk design are preserved.

## Current status

The inherited workflow is operational on its qualified macOS setup. Worker pytest uses a
native OS boundary; the model CLI and controller are still native processes. Containerized
agents, portable execution, transactional budget accounting and the full GitHub delivery
path are being completed. See [the phase gates](docs/roadmap.md). Do not treat pending gates
as completed security controls.

## Run the native baseline

Requires Python 3.12, Git and the qualified macOS framework Python installation.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
./run.sh
```

Open [Mission Control](http://127.0.0.1:8123) or [ParcelDesk](http://127.0.0.1:8123/parceldesk).
The native baseline binds loopback only. Do not expose it publicly.

Model calls require an explicitly configured local `config/model-routing.json`. Without
it, the expired example policy refuses new calls. Provider login and optional Paperclip
setup are operator actions; merely opening the UI performs no inference. Runtime state
is under `runtime/`, ignored by Git. Credentials belong in local configuration only.

The Gate requires human approval for pricing changes. Tests-only tasks on an accepted
local product can complete autonomously after checks and a different-family model review.
Model identity is the CLI-selected label, not a provider attestation of independent weights.

See [RUNBOOK.md](RUNBOOK.md) for operations and [SECURITY.md](SECURITY.md) for trust boundaries.
