# Active implementation status

Requested by owner: publish `OualidGuennif/aeion-playground` publicly and complete the
functional/security phase gates. Scheduler remains deleted; this is direct ongoing work.
Do not stop at repository publication or describe pending security gates as completed.

## Completed

- Clean public source at https://github.com/OualidGuennif/aeion-playground.
- Main commit `efe480f392d37fcbdfa24a826476e6627fb86981`.
- Local native baseline 248 tests passed; 11 additional public-index tests passed.
- Exact staged snapshot and complete initial Git history passed Gitleaks 8.30.1.
- CI workflow pinned actions by SHA, read-only permissions, no persisted checkout credential.
- First real CI run: `40096715116`; both secrets and native tests passed.
- Private POC directory, accepted UI, credentials, histories and sealed packets untouched.
- Public code loads project dotenv only and indexes only its own default context sources.
- Local routing policy is ignored; tracked example expires in 2000, preventing inference
  until the operator supplies an explicit current authorization. Tests use isolated policies.

## In progress

Branch `feat/isolated-workers`. Priority: actual disposable Docker test/model execution,
then atomic usage/reservations/outbox, external acceptance and scoped GitHub delivery.
Track completion against docs/roadmap.md; preserve the accepted frontend.

Docker Desktop is running. Official Python 3.12-slim image pulled and resolved to
`python@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704`.
An actual disposable probe confirmed `docker cp` into a read-only container rootfs is
refused. Use a controller-built, bounded regular-file snapshot mounted read-only for
tests; do not mount the original checkout with Git metadata, home, secrets or Docker socket.
Temporary scratch must be bounded tmpfs; worker non-root, read-only root, no network,
cap-drop ALL, no-new-privileges, CPU/RAM/PID/fsize/nofile/deadline bounds, forced cleanup.
Worker reports remain untrusted: a separate Gate-owned acceptance check is still required.

Implemented `app/container_executor.py`, worker Dockerfile/runner and build script. Actual
qualification: 27 passed, one native-only case skipped; 7 assertions ran inside the real
container and verified UID/capabilities/privilege refusal, root/source write refusal, no
host/Git/socket/secrets/env, ENETUNREACH, cgroup CPU/RAM/PIDs, tmpfs and fd/fsize limits.
Worker and delivery used distinct containers with the same frozen image/contract. Timeout
cleanup and missing-image/symlink refusal are exercised. Docker is now the public default;
macOS remains explicit and never a fallback. Daemon container logging is disabled to avoid
an unbounded log file; the controller retains bounded test output/evidence.

Fresh hash-enforced `.venv` installation succeeded. Local full native run: 259 passed,
5 Docker-only cases skipped (before 7 new wire-protocol tests, which separately passed).
First full Docker run: 255 passed, 7 native-only skipped, two failures. Confirmed causes:
unusable wire reports were labeled unavailable instead of executed-with-error; read-only
Linux mounts report EROFS rather than macOS PermissionError. Corrected the classification,
retained the Gate TEST_EXECUTION_ERROR assertion, adapted the platform-specific exception
assertion and improved the forged-report/output-overflow fixture to write the actual
allowed JUnit path. All 40 affected Docker regressions then passed. Do not claim the
first full Docker run was green. A final complete CI run is still required after commit.

Native and Docker full logs are in ignored `runtime/logs/`. Current Docker manifest is
`runtime/executor-image.json`; the local image is `aeion-playground-worker:1`, ID
`sha256:0bb48e4bee59e4e67c9f2f3e56c2ea5cd9d8c4ae017723234b519ab62714e203`.
No actual provider inference has been made in the public project.

Local `uv==0.13.5` is installed only in ignored `runtime/build-tools`; requirements.lock
was generated with hashes. Review it and use hash-enforced installs in CI/images.
Gitleaks image is pinned at
`ghcr.io/gitleaks/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f`.

## Operational boundaries

No copying or reading private `.env`, auth.json or private frameworks. Existing OpenCode
authentication can be used through the CLI/application without displaying secrets. Original
provider authorization permits OpenCode free-first and Go fallback within 100 USD through
2026-10-25T01:00Z, not other vendors/top-ups. New public source authorization does not extend
that paid-inference window. No new provider calls have been needed for publication so far.

GitHub create/push was explicitly requested; public source publication is authorized.
Do not deploy a public application endpoint. Every new PR must be attached to this chat.
No subagents are active. Use the existing CLI GitHub connection; no GitHub connector is
available. Work directory is this repository, not the old private POC.
