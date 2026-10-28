#!/usr/bin/env python3
"""Deploy the current sealed product head to the local web service.

This is a host-side operator service, not a model role. It reads the accepted
product head from the cockpit ledger, applies the deterministic publication
policy, builds the sealed release tree, recreates the web container, checks the
live routes and rolls back to the previous image on failure.

    .venv/bin/python scripts/redeploy.py --once --dry-run
    .venv/bin/python scripts/redeploy.py --loop --interval-hours 6
    touch runtime/redeploy/stop

A release that the policy marks ``human`` is not deployed; it waits for the
operator approval consumed by scripts/deliver_github.py.
"""
from __future__ import annotations

import argparse
import json
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.github_delivery import DeliveryError, load_release  # noqa: E402
from app.publication import PolicyError, deploy_allowed, evaluate_release, load_policy  # noqa: E402

STOP = False


def _stop(signum: int, _frame: Any) -> None:
    global STOP
    STOP = True
    log(f"signal {signum} received; stopping after the current step")


def log(message: str, path: Path | None = None) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"{stamp} {message}"
    print(line, flush=True)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")


def run(argv: list[str], *, logfile: Path, timeout: float = 900) -> subprocess.CompletedProcess[str]:
    log("+ " + " ".join(argv), logfile)
    return subprocess.run(argv, cwd=ROOT, text=True, capture_output=True, timeout=timeout)


def product_head(runtime_root: Path) -> dict[str, Any] | None:
    database = runtime_root / "aeion.db"
    if not database.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT value FROM kv WHERE key = 'product_head:product'"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise RuntimeError(f"Cannot read the product head: {error}") from error
    if not row:
        return None
    try:
        head = json.loads(row[0])
    except ValueError as error:
        raise RuntimeError(f"Product head is not JSON: {error}") from error
    return head if isinstance(head, dict) else None


def load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {"schema": "aeion.redeploy/v1", "history": []}


def save_state(path: Path, state: dict[str, Any]) -> None:
    state["schema"] = "aeion.redeploy/v1"
    state["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def health(base: str, paths: list[str], logfile: Path) -> tuple[bool, str]:
    for path in paths:
        url = base.rstrip("/") + path
        try:
            with urllib.request.urlopen(url, timeout=6) as response:
                if response.status != 200:
                    return False, f"{path} returned HTTP {response.status}"
                if path.endswith(".csv"):
                    content_type = response.headers.get("content-type", "")
                    if "text/csv" not in content_type:
                        return False, f"{path} content-type is {content_type!r}"
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            return False, f"{path}: {error}"
    return True, "ok"


def deploy(args: argparse.Namespace, head: dict[str, Any], policy: dict[str, Any],
           state: dict[str, Any], release, decision, logfile: Path) -> int:
    mission = str(head["mission"])
    commit = str(head["commit"])
    context = Path(release.product) / "frontend"
    if not (context / "Dockerfile").is_file():
        log(f"release {mission} has no frontend Dockerfile", logfile)
        return 1
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    new_tag = f"aeion-parceldesk-web:{mission.lower()}-{timestamp}"
    image = args.image
    previous = run(["docker", "image", "inspect", "--format", "{{.Id}}", image], logfile=logfile)
    previous_id = previous.stdout.strip() if previous.returncode == 0 else ""
    if args.dry_run:
        log(f"dry-run: would build {context} as {new_tag}, tag {image}, "
            f"recreate {args.service}, health {args.health_url}", logfile)
        return 0
    built = run(["docker", "build", "--tag", new_tag, str(context)], logfile=logfile, timeout=1800)
    if built.returncode != 0:
        log(f"docker build failed: {(built.stderr or built.stdout)[-800:]}", logfile)
        return 1
    tagged = run(["docker", "tag", new_tag, image], logfile=logfile)
    if tagged.returncode != 0:
        log(f"docker tag failed: {(tagged.stderr or tagged.stdout)[-800:]}", logfile)
        return 1
    recreated = run([
        "docker", "compose", "-f", str(args.compose), "up", "-d", "--no-deps",
        "--force-recreate", "--wait", args.service,
    ], logfile=logfile, timeout=600)
    if recreated.returncode != 0:
        log(f"compose recreate failed: {(recreated.stderr or recreated.stdout)[-800:]}", logfile)
        if previous_id:
            log(f"rolling back to {previous_id}", logfile)
            run(["docker", "tag", previous_id, image], logfile=logfile)
            run(["docker", "compose", "-f", str(args.compose), "up", "-d", "--no-deps",
                 "--force-recreate", "--wait", args.service], logfile=logfile, timeout=600)
        return 1
    ok, detail = health(args.health_url, list(policy["deploy"]["health_paths"]), logfile)
    if not ok:
        log(f"health check failed: {detail}", logfile)
        if previous_id:
            log(f"rolling back to {previous_id}", logfile)
            run(["docker", "tag", previous_id, image], logfile=logfile)
            run(["docker", "compose", "-f", str(args.compose), "up", "-d", "--no-deps",
                 "--force-recreate", "--wait", args.service], logfile=logfile, timeout=600)
            rollback_ok, rollback_detail = health(args.health_url, list(policy["deploy"]["health_paths"]), logfile)
            log(f"rollback health: {rollback_ok} · {rollback_detail}", logfile)
        return 1
    image_id = run(["docker", "image", "inspect", "--format", "{{.Id}}", image], logfile=logfile).stdout.strip()
    receipt = {
        "mission": mission, "commit": commit, "image": image, "image_id": image_id,
        "built_tag": new_tag, "decision": decision.to_dict(), "health": detail,
        "deployed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "previous_image_id": previous_id,
    }
    state.update(last_mission=mission, last_commit=commit, last_deploy=receipt)
    history = state.setdefault("history", [])
    history.append(receipt)
    state["history"] = history[-20:]
    save_state(args.state, state)
    log(f"deployed {mission} · {image_id[:19]} · health {detail}", logfile)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runtime-root", type=Path, default=ROOT / "runtime/cockpit")
    parser.add_argument("--policy", type=Path, default=ROOT / "config/publication-policy.json")
    parser.add_argument("--compose", type=Path, default=ROOT / "compose.yaml")
    parser.add_argument("--service", default="parceldesk-web")
    parser.add_argument("--image", default="aeion-parceldesk-web:1")
    parser.add_argument("--health-url", default="http://127.0.0.1:8125")
    parser.add_argument("--state", type=Path, default=ROOT / "runtime/redeploy/state.json")
    parser.add_argument("--log", type=Path, default=ROOT / "runtime/logs/redeploy.log")
    parser.add_argument("--stop-file", type=Path, default=ROOT / "runtime/redeploy/stop")
    parser.add_argument("--interval-hours", type=float, default=6.0)
    parser.add_argument("--publish", action="store_true",
                        help="Publish an auto (or operator-approved) release to GitHub before deploying")
    parser.add_argument("--repo", default="OualidGuennif/aeion-playground")
    parser.add_argument("--base", default="main")
    parser.add_argument("--approval-dir", type=Path,
                        default=ROOT / "runtime/github-delivery/approvals")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--force", action="store_true", help="Deploy even when the head is unchanged")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    global STOP
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    logfile = args.log
    try:
        policy = load_policy(args.policy)
    except PolicyError as error:
        log(f"policy rejected: {error}", logfile)
        return 2
    log("redeploy starting · interval "
        f"{args.interval_hours:g}h · dry_run={args.dry_run}", logfile)
    while not STOP and not args.stop_file.exists():
        try:
            head = product_head(args.runtime_root)
            if not head or not head.get("mission"):
                log("no product head yet; waiting", logfile)
            else:
                state = load_state(args.state)
                if (not args.force and state.get("last_mission") == head.get("mission")
                        and state.get("last_commit") == head.get("commit")):
                    log(f"head {head.get('mission')} already deployed; no-op", logfile)
                else:
                    release = load_release(args.runtime_root, str(head["mission"]))
                    approval_path = args.approval_dir / f"{head['mission']}.json"
                    approval = None
                    if approval_path.is_file():
                        try:
                            approval = json.loads(approval_path.read_text())
                        except ValueError as error:
                            log(f"approval file is invalid: {error}", logfile)
                    decision = evaluate_release(release.packet, policy, approval=approval)
                    log(f"head {head['mission']} · publication {decision.decision} · "
                        f"{decision.reasons or 'auto'}", logfile)
                    if not deploy_allowed(decision, policy):
                        state.update(pending_human=decision.to_dict())
                        save_state(args.state, state)
                        log("deployment skipped: operator publication approval required", logfile)
                    else:
                        publish_ok = True
                        if args.publish:
                            command = [
                                sys.executable, str(ROOT / "scripts/deliver_github.py"),
                                "--mission", str(head["mission"]), "--repo", args.repo,
                                "--base", args.base, "--wait", "--merge",
                            ]
                            if approval_path.is_file():
                                command += ["--approval-file", str(approval_path)]
                                log(f"using operator approval {approval_path}", logfile)
                            else:
                                command += ["--auto"]
                            published = run(command, logfile=logfile, timeout=1800)
                            tail = (published.stdout or published.stderr or "").strip().splitlines()[-6:]
                            for line in tail:
                                log("publish: " + line, logfile)
                            if published.returncode == 0:
                                log("github publication complete", logfile)
                            elif "already merged" in (published.stdout or ""):
                                log("github publication already merged; continuing to deploy", logfile)
                            elif published.returncode == 3 or "human approval required" in (published.stdout or ""):
                                publish_ok = False
                                state.update(pending_human=decision.to_dict())
                                save_state(args.state, state)
                                log("github publication pending operator approval; deployment skipped", logfile)
                            else:
                                publish_ok = False
                                log("github publication failed; deployment skipped", logfile)
                        if publish_ok:
                            result = deploy(args, head, policy, state, release, decision, logfile)
                            if result != 0:
                                log("deployment failed; see the log above", logfile)
        except (DeliveryError, OSError, RuntimeError, ValueError) as error:
            log(f"redeploy error: {type(error).__name__}: {error}", logfile)
        if not args.loop or args.once:
            break
        deadline = time.monotonic() + max(0.1, args.interval_hours) * 3600
        while not STOP and not args.stop_file.exists() and time.monotonic() < deadline:
            time.sleep(min(5.0, deadline - time.monotonic()))
    log("redeploy stopped", logfile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
