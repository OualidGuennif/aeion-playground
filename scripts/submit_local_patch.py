#!/usr/bin/env python3
"""Verify a real local contribution through the same sandbox, tests and gate.

No model is called. Independent review is unavailable, so successful patches
stop at the human checkpoint. This script never approves a mission.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.real_engine import engine
from app.store import store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--fixture", choices=("PD01", "PD02"), default="PD01")
    args = parser.parse_args()
    mission = engine.launch_mission(args.fixture, local_patch=args.patch.read_text())
    print(f"Mission {mission['id']} — local patch, no model API call", flush=True)
    while True:
        current = store.mission(mission["id"])
        if current["status"] in {"blocked", "completed", "awaiting_human"}:
            tests = json.loads(current.get("tests_json") or "{}")
            diff = json.loads(current.get("diff_json") or "{}")
            print(json.dumps({
                "id": current["id"], "status": current["status"],
                "gate": current["gate_reason_code"], "reason": current["gate_reason"],
                "passed": tests.get("passed"), "total": tests.get("total"),
                "files": diff.get("files"), "paperclip": current.get("paperclip_url"),
            }, indent=2))
            return
        time.sleep(0.25)


if __name__ == "__main__":
    main()
