#!/usr/bin/env python3
"""Keep the local Factory alive with a bounded cadence of product tickets.

This is operator tooling, not a model authority. It admits low-risk product
tickets from an operator-editable backlog, waits for each mission, and stops
on the local exposure limit, an unsettled attempt, a human checkpoint, a
failure streak or the stop file. It never approves, seals or merges anything.

    .venv/bin/python scripts/pulse.py --dry-run
    .venv/bin/python scripts/pulse.py --interval-minutes 12 --max-runs 24
    touch runtime/pulse/stop

The cockpit UI already polls /api/snapshot, so new missions, agent steps,
tests and Gate decisions appear live while this runs.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.pulse import (  # noqa: E402
    PulseConfigError,
    budget_stop,
    classify,
    delivery_summary,
    is_daily_budget_stop,
    load_tickets,
    next_ticket,
    next_utc_midnight,
    ticket_payload,
)

STOP = False


def _stop(signum: int, _frame: Any) -> None:
    global STOP
    STOP = True
    log(f"signal {signum} received; stopping after the current observation")


def log(message: str, path: Path | None = None) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"{stamp} {message}"
    print(line, flush=True)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")


def request_json(base: str, path: str, payload: dict[str, Any] | None = None,
                 timeout: float = 30) -> tuple[int | None, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(4_000_000)
            try:
                return response.status, json.loads(body)
            except ValueError:
                return response.status, body.decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        body = error.read(2_000_000)
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = body.decode("utf-8", "replace")
        return error.code, parsed
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return None, str(error)


def load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {"schema": "aeion.pulse/v1", "state": "idle", "runs": 0,
            "used": [], "cursor": 0, "consecutive_failures": 0}


def save_state(path: Path, state: dict[str, Any]) -> None:
    state["schema"] = "aeion.pulse/v1"
    state["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sleep_until(deadline: float, stop_file: Path) -> bool:
    """Sleep until the deadline; return False when the operator asks to stop."""
    while not STOP and not stop_file.exists():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(1.0, remaining))
    return False


def wait_for_cockpit(base: str, logfile: Path, stop_file: Path, timeout: float = 120) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while not STOP and not stop_file.exists() and time.monotonic() < deadline:
        status, body = request_json(base, "/api/snapshot", timeout=10)
        if status == 200 and isinstance(body, dict) and body.get("kpis"):
            return body
        log(f"cockpit not ready (HTTP {status}); retrying", logfile)
        time.sleep(10)
    return None


def wait_for_mission(base: str, mission_id: str, logfile: Path, stop_file: Path,
                     timeout_minutes: float) -> dict[str, Any] | None:
    deadline = time.monotonic() + max(1.0, timeout_minutes) * 60
    last_status = ""
    while not STOP and not stop_file.exists() and time.monotonic() < deadline:
        status, body = request_json(base, f"/api/missions/{mission_id}", timeout=15)
        if status == 200 and isinstance(body, dict):
            mission = body.get("mission") or {}
            current = str(mission.get("status") or "?")
            if current != last_status:
                log(f"{mission_id} · {current} · {mission.get('current_step') or '—'}", logfile)
                last_status = current
            if current in {"completed", "blocked", "failed", "denied", "awaiting_human"}:
                # A completed product mission runs its local delivery after the seal;
                # give the receipt a short window to appear so the log is useful.
                if current == "completed" and not body.get("delivery") and not body.get("delivery_error"):
                    for _ in range(10):
                        time.sleep(2)
                        status, body = request_json(base, f"/api/missions/{mission_id}", timeout=15)
                        if status != 200 or not isinstance(body, dict):
                            break
                        if body.get("delivery") or body.get("delivery_error"):
                            break
                return body
        else:
            log(f"{mission_id} · status read HTTP {status}; retrying", logfile)
        time.sleep(5)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=ROOT / "config/pulse-tickets.json")
    parser.add_argument("--base-url", default="http://127.0.0.1:8124")
    parser.add_argument("--interval-minutes", type=float, default=12.0,
                        help="Cadence between admissions; 12 = 5 per hour")
    parser.add_argument("--max-runs", type=int, default=0,
                        help="Hard cap for this process; 0 = no run cap (the daily budget governs)")
    parser.add_argument("--max-exposure-usd", type=float, default=15.0,
                        help="Daily UTC exposure cap; resets at 00:00 UTC")
    parser.add_argument("--max-consecutive-failures", type=int, default=3)
    parser.add_argument("--mission-timeout-minutes", type=float, default=15.0)
    parser.add_argument("--stop-file", type=Path, default=ROOT / "runtime/pulse/stop")
    parser.add_argument("--state-file", type=Path, default=ROOT / "runtime/pulse/status.json")
    parser.add_argument("--log", type=Path, default=ROOT / "runtime/logs/pulse.log")
    parser.add_argument("--once", action="store_true", help="Admit and observe one ticket, then exit")
    parser.add_argument("--dry-run", action="store_true", help="Show the next ticket and budget; admit nothing")
    parser.add_argument("--reset-state", action="store_true", help="Forget used tickets and restart the backlog")
    args = parser.parse_args()

    global STOP
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    logfile = args.log
    stop_file = args.stop_file
    state_file = args.state_file
    base = args.base_url.rstrip("/")

    try:
        tickets = load_tickets(args.config)
    except PulseConfigError as error:
        log(f"pulse config rejected: {error}", logfile)
        return 2

    if args.reset_state:
        try:
            state_file.unlink()
        except FileNotFoundError:
            pass
    state = load_state(state_file)
    used = set(state.get("used") or [])
    cursor = int(state.get("cursor") or 0)
    runs = int(state.get("runs") or 0)
    failures = int(state.get("consecutive_failures") or 0)
    state.update(state="starting", message="")
    save_state(state_file, state)
    run_cap = "unlimited" if args.max_runs <= 0 else f"max {args.max_runs} runs"
    log(f"pulse starting · {len(tickets)} tickets · {args.interval_minutes:g} min cadence · "
        f"daily cap ${args.max_exposure_usd:.2f} · {run_cap}", logfile)

    while not STOP and not stop_file.exists() and (args.max_runs <= 0 or runs < args.max_runs):
        snapshot = wait_for_cockpit(base, logfile, stop_file)
        if snapshot is None:
            state.update(state="cockpit_unavailable", message="cockpit snapshot unavailable")
            save_state(state_file, state)
            log("pulse stopped: cockpit unavailable", logfile)
            return 2
        kpis = snapshot.get("kpis") or {}
        reason = budget_stop(kpis, args.max_exposure_usd)
        if reason:
            if is_daily_budget_stop(reason):
                reset = next_utc_midnight()
                wait = max(1.0, (reset - datetime.now(timezone.utc)).total_seconds())
                state.update(state="daily_pause", message=reason,
                             exposure_usd=float(kpis.get("budget_used") or 0),
                             budget_remaining=kpis.get("budget_remaining"),
                             next_at=reset.isoformat(timespec="seconds"))
                save_state(state_file, state)
                log(f"daily budget pause: {reason}; resuming at {state['next_at']}", logfile)
                if not sleep_until(time.monotonic() + wait, stop_file):
                    break
                continue
            state.update(state="budget_stop", message=reason,
                         exposure_usd=float(kpis.get("budget_used") or 0),
                         budget_remaining=kpis.get("budget_remaining"))
            save_state(state_file, state)
            log(f"pulse stopped: {reason}", logfile)
            return 1
        if int(kpis.get("running_missions") or 0) > 0:
            log("active mission still running; waiting for capacity", logfile)
            time.sleep(5)
            continue
        selected = next_ticket(tickets, used, cursor)
        if selected is None:
            state.update(state="exhausted", message="all backlog tickets have been used")
            save_state(state_file, state)
            log("pulse finished: backlog exhausted", logfile)
            return 0
        index, ticket = selected
        payload = ticket_payload(ticket)
        if args.dry_run:
            state.update(state="dry_run", last_ticket=ticket["id"],
                         exposure_usd=float(kpis.get("budget_used") or 0),
                         budget_remaining=kpis.get("budget_remaining"))
            save_state(state_file, state)
            print(json.dumps({"dry_run": True, "ticket": ticket, "budget_stop": reason}, indent=2, ensure_ascii=False))
            return 0

        started = time.monotonic()
        status, body = request_json(base, "/api/parceldesk/tickets", payload)
        if status == 409:
            log("capacity rejected the ticket; retrying after the active mission", logfile)
            time.sleep(15)
            continue
        if status != 200 or not isinstance(body, dict) or not body.get("ok"):
            state.update(state="admission_error", last_ticket=ticket["id"],
                         message=f"HTTP {status}: {str(body)[:300]}")
            save_state(state_file, state)
            log(f"pulse stopped: ticket admission failed (HTTP {status})", logfile)
            return 2
        mission = body.get("mission") or {}
        mission_id = str(mission.get("id") or "")
        ticket_id = (body.get("ticket") or {}).get("id")
        if not mission_id:
            state.update(state="admission_error", last_ticket=ticket["id"],
                         message="admission returned no mission id")
            save_state(state_file, state)
            log("pulse stopped: admission returned no mission id", logfile)
            return 2
        used.add(ticket["id"])
        runs += 1
        cursor = index + 1
        state.update(state="running", runs=runs, used=sorted(used), cursor=cursor,
                     last_ticket=ticket["id"], ticket_id=ticket_id, last_mission=mission_id,
                     consecutive_failures=failures, message="")
        save_state(state_file, state)
        log(f"admitted {ticket['id']} as {mission_id} (ticket {ticket_id})", logfile)

        final = wait_for_mission(base, mission_id, logfile, stop_file, args.mission_timeout_minutes)
        if final is None:
            state.update(state="mission_timeout", message=f"{mission_id} did not reach a terminal state")
            save_state(state_file, state)
            log(f"pulse stopped: {mission_id} did not reach a terminal state", logfile)
            return 2
        final_mission = final.get("mission") or {}
        outcome = classify(final_mission, final.get("delivery"), final.get("delivery_error"))
        delivery = delivery_summary(final.get("delivery"), final.get("delivery_error"))
        fresh_status, fresh = request_json(base, "/api/snapshot", timeout=10)
        if fresh_status == 200 and isinstance(fresh, dict) and fresh.get("kpis"):
            kpis = fresh["kpis"]
        state.update(last_status=final_mission.get("status"), last_gate=final_mission.get("gate_reason_code"),
                     last_outcome=final_mission.get("outcome"), last_delivery=delivery,
                     exposure_usd=float(kpis.get("budget_used") or 0),
                     budget_remaining=kpis.get("budget_remaining"), message="")
        if outcome == "completed":
            failures = 0
            state.update(state="completed", consecutive_failures=failures)
            save_state(state_file, state)
            log(f"{mission_id} completed · {final_mission.get('gate_reason_code')} · "
                f"delivery {delivery.get('commit') or 'none'}", logfile)
        elif outcome == "waiting_human":
            state.update(state="waiting_human", consecutive_failures=failures)
            save_state(state_file, state)
            log(f"{mission_id} needs operator approval; pulse paused, nothing was approved", logfile)
            return 3
        elif outcome == "blocked":
            failures += 1
            state.update(state="blocked", consecutive_failures=failures)
            save_state(state_file, state)
            log(f"{mission_id} blocked · {final_mission.get('gate_reason_code')} · "
                f"failure streak {failures}/{args.max_consecutive_failures}", logfile)
            if failures >= max(1, args.max_consecutive_failures):
                state.update(state="failure_streak", message="too many consecutive blocked missions")
                save_state(state_file, state)
                log("pulse stopped: consecutive failure cap reached", logfile)
                return 1
        else:
            state.update(state="unknown", message=f"unclassified mission status {final_mission.get('status')}")
            save_state(state_file, state)
            log(f"pulse stopped: unclassified mission status {final_mission.get('status')}", logfile)
            return 2
        if args.once:
            state.update(state="finished", message="one-shot run complete")
            save_state(state_file, state)
            return 0
        next_monotonic = started + max(0.0, args.interval_minutes) * 60
        next_wall = time.time() + max(0.0, next_monotonic - time.monotonic())
        state["next_at"] = datetime.fromtimestamp(next_wall, timezone.utc).isoformat(timespec="seconds")
        save_state(state_file, state)
        log(f"next admission at {state['next_at']}", logfile)
        if not sleep_until(next_monotonic, stop_file):
            break

    reason = "stop file present" if stop_file.exists() else "operator signal" if STOP else "run cap reached"
    state.update(state="stopped", message=reason)
    save_state(state_file, state)
    log(f"pulse stopped: {reason}", logfile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
