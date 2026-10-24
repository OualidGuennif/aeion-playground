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

## Session context budget (hard limit)

A session gets 250,000 tokens of context, maximum. That is a ceiling, not a target, and it
overrides any instruction to finish the remaining work in one go.

When usage approaches 250k:

1. Start nothing new: no new files, edits, investigations, test suites or refactors.
2. Finish and report the command already in flight so nothing is left half-applied.
3. Replace `HANDOVER.md` — delete the previous content, never append to it — with a complete
   record written for a reader who has none of this conversation: the goal, each decision
   and why it was made, every file touched and its state, what is finished with the command
   and output that measured it, what is uncommitted, what is unverified, open blockers, the
   exact next commands in order, and what must not be touched. No "as discussed", no
   "the earlier issue". Write it before compacting, while the details are still readable,
   and assume anything not written down is lost.
4. Compact this session and keep working in it. Do not start a new session and do not ask
   the operator to; compaction replaces the transcript with the summary, and the handover —
   not the transcript — carries the state forward. In OpenCode this is
   `POST /api/session/{id}/compact` with an empty JSON body; a bare `POST` returns
   `InvalidRequestError: Expected object`.

Do not cross the cap to "just finish" a task. Do not write a handover that claims more than
was measured: unverified work is listed as unverified. `HANDOVER.md` is local working state
and stays out of Git.
