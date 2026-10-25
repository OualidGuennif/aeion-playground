#!/usr/bin/env python3
"""Deliver one sealed local product release to GitHub under operator authority.

The operator's own ``gh`` login is used on the host. No credential is read,
printed or passed to the cockpit or a model role.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.github_delivery import DeliveryError, GitHubDelivery, load_release


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", required=True, help="Sealed product mission, for example PD-028")
    parser.add_argument("--repo", default="OualidGuennif/aeion-playground")
    parser.add_argument("--base", default="main")
    parser.add_argument("--runtime-root", type=Path, default=Path("runtime/cockpit"))
    parser.add_argument("--dry-run", action="store_true", help="Prepare and diff locally; publish nothing")
    parser.add_argument("--no-issue", action="store_true", help="Do not create a GitHub issue")
    parser.add_argument("--wait", action="store_true", help="Wait for every GitHub check")
    parser.add_argument("--merge", action="store_true", help="Squash-merge only after every check passes")
    parser.add_argument("--force", action="store_true", help="Allow replacing a diverged product tree")
    parser.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args()
    try:
        release = load_release(args.runtime_root, args.mission)
        delivery = GitHubDelivery(release, args.repo, base=args.base)
        plan = delivery.prepare(force=args.force, dry_run=args.dry_run)
        summary = {
            "mission": plan.mission, "branch": plan.branch, "commit": plan.commit,
            "changed_files": plan.changed_files, "issue_title": plan.issue_title,
            "pr_title": plan.pr_title,
        }
        if args.dry_run:
            print(json.dumps({**summary, "dry_run": True}, indent=2, ensure_ascii=False))
            return 0
        result = delivery.publish(plan, create_issue=not args.no_issue,
                                  wait=args.wait, merge=args.merge, timeout=args.timeout)
        print(json.dumps({**summary, **result}, indent=2, ensure_ascii=False))
        return 0
    except (DeliveryError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
