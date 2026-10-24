# Engineering contract

Use the smallest verified solution. Inspect code and tests before modifying behavior.
Preserve the accepted ParcelDesk and cockpit design. Never alter historical sealed proof.
Never read or print credentials. Local state, provider credentials, model transcripts and
private framework sources must stay outside Git. Do not load parent-folder dotenv files.

Tests: create a Python 3.12 venv, install requirements-dev.txt, run `python -m pytest -q`.
The inherited native executor requires macOS; the Docker executor must fail closed when
its qualified image/backend is absent. Controlled model fixtures are not live provider proof.

Do not add services without a concrete purpose. No Docker socket, host home or credentials
inside untrusted workers. Keep Gate/approval authority separate from model output.
Document supported behavior, measured evidence and unverified limits. Do not claim that a
configuration file, test count or secret scan establishes complete security.
