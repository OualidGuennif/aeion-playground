# Contributing

This repository is a local engineering playground: a model proposes a patch, disposable
workers run real tests, a deterministic Gate decides, and evidence is sealed before any
delivery. Contributions are judged by those same rules — a good description is not a
substitute for a green, measured result.

## Before you start

- Read [`AGENTS.md`](AGENTS.md) (the engineering contract) and [`SECURITY.md`](SECURITY.md)
  (trust boundaries). They override convenience.
- Read [`docs/roadmap.md`](docs/roadmap.md): a gate marked *Pending* means the behaviour
  does not exist yet, whatever a file name suggests.
- Do not open an issue containing credentials, tokens, private paths, model transcripts or
  runtime databases. Redact first.

## Set up

Requires Python 3.12, Git, and Docker Engine/Desktop with Linux containers.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python scripts/build_worker.py
.venv/bin/python -m pytest -q
```

The default suite skips opt-in container, stack, inference and collector qualifications.
Run the ones your change touches; the CI workflow shows the exact commands.

## Making a change

1. Branch from `main`: `git checkout -b feat/<short-name>`.
2. Keep the change to one behaviour. If it needs a new service, say why in the pull
   request — "no services without a concrete purpose".
3. Add the test that would fail without your change. Negative cases matter more than the
   happy path: refused access, missing backend, timeout cleanup, duplicate delivery.
4. Run the suite you can run locally, and record the exact command and result.
5. Open a pull request. Hosted CI must pass on the exact revision before merge; direct
   pushes to `main` are refused.

## What a reviewer checks

| Check | Question |
|---|---|
| Boundary | Does any untrusted role gain a socket, host path, credential, network route or approval right? |
| Evidence | Is each claim backed by a command that ran, with its output, rather than by a file existing? |
| Honesty | Are unverified parts labelled unverified? Is anything described as "secure" without a scope? |
| Scope | Does the diff touch sealed proofs, the accepted cockpit design or ParcelDesk's design without cause? |
| Failure | What happens when the backend is missing, the container is killed, the disk is full, the collector refuses? |

## Improving the demo (ParcelDesk)

The demo is the end-to-end test of the Factory: improvements to it are delegated to the
agents through the workboard rather than written by hand. Open an
[improvement ticket](.github/ISSUE_TEMPLATE/parceldesk-improvement.yml) — the structured
form is what the Factory reads. A ticket needs an observable outcome ("support agents can
see a refund reason in the queue"), not an implementation instruction ("use a for loop").

The Factory will produce a patch, run the tests, review it, and either seal it or record a
blocked mission with a reason. A rejected ticket is a normal outcome; the reason is in the
ledger, not in a comment thread.

## Style

Match the surrounding code: standard library first, explicit transactions, bounded I/O,
no new dependency without a pinned version and a lock update. Documentation is part of the
change: if you alter a boundary, update the doc that states it. Keep Markdown lines short
and factual.

## Security

Report a vulnerability through GitHub private vulnerability reporting, not in a public
issue. Do not include exploit details, provider keys or customer data in any issue or pull
request. Nothing in this repository is a production service.
