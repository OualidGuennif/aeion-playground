#!/usr/bin/env python3
"""Submit one idempotent task to the running local Factory backlog."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
import urllib.error
import urllib.request


def request(base: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    query = urllib.request.Request(base.rstrip("/") + path, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(query, timeout=30) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture")
    source.add_argument("--task-file", type=Path, help="JSON coverage task: title, task, test_file, minimum_new_tests.")
    parser.add_argument("--request-id", required=True, help="Reuse this id to retry delivery without repeating the task.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8123")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--timeout", type=float, default=600, help="Maximum observation time, not a worker cancellation deadline.")
    args = parser.parse_args()
    try:
        endpoint = "/api/missions/queue"
        payload = {"fixture": args.fixture, "request_id": args.request_id}
        if args.task_file:
            definition = json.loads(args.task_file.read_text())
            required = {"title", "task", "test_file", "minimum_new_tests"}
            if not isinstance(definition, dict) or set(definition) != required:
                raise ValueError("Task JSON must contain exactly title, task, test_file and minimum_new_tests.")
            endpoint = "/api/tasks/tests"
            payload = {**definition, "request_id": args.request_id}
        mission = request(args.base_url, endpoint, payload)["mission"]
        print(f"{mission['id']} · {mission['status']} · {args.base_url}/api/missions/{mission['id']}", flush=True)
        deadline = time.monotonic() + max(0, args.timeout)
        observed = {"mission": mission}
        def pending():
            local_delivery = json.loads(mission.get("contract_json") or "{}").get("scenario", {}).get("delivery") == "local"
            return mission["status"] in {"queued", "running"} or (
                mission["status"] == "completed" and local_delivery and not observed.get("delivery") and not observed.get("delivery_error")
            )
        while args.wait and pending() and time.monotonic() < deadline:
            time.sleep(1)
            observed = request(args.base_url, f"/api/missions/{mission['id']}")
            mission = observed["mission"]
        print(json.dumps({key: mission.get(key) for key in ("id", "request_id", "status", "gate_reason_code", "gate_reason", "paperclip_url")}, indent=2))
        if args.wait and pending():
            print("Observation ended; the persisted task continues independently.", file=sys.stderr)
            return 2
        if args.wait and observed.get("delivery_error"):
            print(f"Local delivery blocked: {observed['delivery_error']}", file=sys.stderr)
            return 1
        if observed.get("delivery"):
            print(json.dumps({"delivery": observed["delivery"]}, indent=2))
        if args.wait and mission["status"] == "awaiting_human":
            return 3
        if args.wait and mission["status"] == "blocked":
            return 1
        return 0
    except urllib.error.HTTPError as error:
        print(f"Factory rejected the request ({error.code}): {error.read().decode('utf-8', errors='replace')}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, OSError) as error:
        print(f"Factory submission/status unavailable: {error}. Reuse the same request id when retrying.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
