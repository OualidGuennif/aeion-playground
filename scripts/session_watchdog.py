#!/usr/bin/env python3
"""Re-prompt a stalled OpenCode session after a provider quota or rate limit.

Local operator tooling, not part of the Factory. It watches one session's
`time.updated`; when the session has produced nothing for `--idle-minutes` it
posts one short resume prompt, then waits `--cooldown-minutes` before the next
attempt, up to `--max-resumes`. It never reads or prints message content, so a
transcript cannot leak through it.

Honest limits: a stall is not proof of a rate limit. An agent that thinks for
longer than the idle threshold would also be resumed. That is why the caps are
low, the thresholds generous, and why `--stop-file` exists: after finishing, the
operator (or the agent) writes that file and the watchdog exits for good.

    .venv/bin/python scripts/session_watchdog.py --session ses_xxx --dry-run
    nohup .venv/bin/python scripts/session_watchdog.py --session ses_xxx \
        > runtime/logs/watchdog.out 2>&1 &
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = os.environ.get("OPENCODE_CLI", "/Applications/OpenCode.app/Contents/Resources/opencode-cli")
RESUME_TEXT = (
    "Resume the work in this repository. Read CURRENT.md, then HANDOVER.md and docs/roadmap.md. "
    "Continue the next unfinished step, keep evidence measured, and do not start a new session."
)


def log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"{stamp} {message}\n")


def api(*args: str) -> dict:
    result = subprocess.run([CLI, "api", *args], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise OSError(f"opencode api failed: {result.returncode}")
    return json.loads(result.stdout)


def session_updated(session: str) -> float:
    for item in api("get", "/api/session").get("data", []):
        if item.get("id") == session:
            return float((item.get("time") or {}).get("updated") or 0) / 1000.0
    raise OSError("session not found")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session", required=True, help="Session ID to watch, e.g. ses_...")
    parser.add_argument("--idle-minutes", type=float, default=25.0, help="Silence before a resume attempt")
    parser.add_argument("--cooldown-minutes", type=float, default=20.0, help="Minimum gap between attempts")
    parser.add_argument("--max-resumes", type=int, default=8, help="Hard cap; the watchdog then exits")
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--stop-file", default=str(ROOT / "runtime/watchdog/stop"))
    parser.add_argument("--log", default=str(ROOT / "runtime/logs/watchdog.log"))
    parser.add_argument("--dry-run", action="store_true", help="Report decisions without prompting")
    args = parser.parse_args()

    stop, logfile = Path(args.stop_file), Path(args.log)
    log(logfile, f"watching {args.session} idle={args.idle_minutes}m cooldown={args.cooldown_minutes}m cap={args.max_resumes} dry_run={args.dry_run}")
    resumes, last_resume, last_change = 0, 0.0, time.time()
    try:
        updated = session_updated(args.session)
    except (OSError, ValueError):
        log(logfile, "cannot read session list; exiting")
        return 2
    while True:
        if stop.exists():
            log(logfile, "stop file present; exiting")
            return 0
        try:
            current = session_updated(args.session)
        except (OSError, ValueError):
            log(logfile, "session read failed; retry after poll interval")
            time.sleep(args.poll_seconds)
            continue
        now = time.time()
        if current > updated:
            updated, last_change = current, now
        idle = now - max(updated, last_change)
        if idle >= args.idle_minutes * 60 and now - last_resume >= args.cooldown_minutes * 60:
            if resumes >= args.max_resumes:
                log(logfile, f"cap reached ({resumes}); exiting")
                return 0
            if args.dry_run:
                log(logfile, f"would resume (idle {idle / 60:.1f}m, attempt {resumes + 1})")
            else:
                try:
                    api("post", f"/api/session/{args.session}/prompt",
                        "--data", json.dumps({"text": RESUME_TEXT, "delivery": "steer"}))
                    log(logfile, f"resumed (idle {idle / 60:.1f}m, attempt {resumes + 1})")
                except (OSError, ValueError):
                    log(logfile, "resume prompt failed; will retry after cooldown")
            resumes += 1
            last_resume = now
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    sys.exit(main())
