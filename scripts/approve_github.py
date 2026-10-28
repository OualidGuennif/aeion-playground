#!/usr/bin/env python3
"""Record an explicit operator approval for a human-required publication.

This script never publishes and never approves a Factory mission. It only
writes a time-bounded approval file that scripts/deliver_github.py can consume.
Run it only after reviewing the sealed packet, the PR-shaped diff and the
publication policy reasons.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.github_delivery import DeliveryError, load_release
from app.publication import load_policy, policy_digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mission", required=True)
    parser.add_argument("--reason", required=True, help="Why this critical or oversized change is approved")
    parser.add_argument("--by", default="operator")
    parser.add_argument("--runtime-root", type=Path, default=Path("runtime/cockpit"))
    parser.add_argument("--policy", type=Path, default=Path("config/publication-policy.json"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if len(args.reason.strip()) < 10:
        print("The approval reason must be at least 10 characters.", file=sys.stderr)
        return 2
    try:
        policy = load_policy(args.policy)
        release = load_release(args.runtime_root, args.mission)
        diff = release.packet.get("diff") if isinstance(release.packet.get("diff"), dict) else {}
        output = args.output or (ROOT / "runtime/github-delivery/approvals" / f"{args.mission}.json")
        approval = {
            "schema": "aeion.publication-approval/v1",
            "mission": args.mission,
            "packet_digest": release.receipt.get("packet_digest"),
            "diff_digest": diff.get("digest"),
            "approved_by": args.by.strip() or "operator",
            "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "policy_digest": policy_digest(policy),
            "reason": args.reason.strip(),
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(approval, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({
            "ok": True,
            "approval": str(output),
            "publish_with": f".venv/bin/python scripts/deliver_github.py --mission {args.mission} "
                            f"--approval-file {output} --wait --merge",
        }, indent=2, ensure_ascii=False))
        return 0
    except (DeliveryError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
