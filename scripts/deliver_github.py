#!/usr/bin/env python3
"""Deliver one sealed local product release to GitHub under operator authority.

The operator's own ``gh`` login is used on the host. No credential is read,
printed or passed to the cockpit or a model role.

Every delivery is checked by the deterministic publication policy before any
branch, issue or pull request is created. Small test/UI changes can publish
automatically; critical or oversized changes require an approval file created
by scripts/approve_github.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.github_delivery import DeliveryError, GitHubDelivery, load_release
from app.publication import PolicyError, evaluate_release, load_policy


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mission", required=True, help="Sealed product mission, for example PD-056")
    parser.add_argument("--repo", default="OualidGuennif/aeion-playground")
    parser.add_argument("--base", default="main")
    parser.add_argument("--runtime-root", type=Path, default=Path("runtime/cockpit"))
    parser.add_argument("--policy", type=Path, default=Path("config/publication-policy.json"))
    parser.add_argument("--approval-file", type=Path, help="Operator approval for a human-required publication")
    parser.add_argument("--auto", action="store_true", help="Publish only when the policy says auto")
    parser.add_argument("--dry-run", action="store_true", help="Prepare and evaluate locally; publish nothing")
    parser.add_argument("--no-issue", action="store_true", help="Do not create a GitHub issue")
    parser.add_argument("--wait", action="store_true", help="Wait for every GitHub check")
    parser.add_argument("--merge", action="store_true", help="Squash-merge only after every check passes")
    parser.add_argument("--merge-only", action="store_true",
                        help="Wait for and merge the pull request already recorded for this mission")
    parser.add_argument("--force", action="store_true", help="Allow replacing a diverged product tree")
    parser.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args()
    try:
        policy = load_policy(args.policy)
        release = load_release(args.runtime_root, args.mission)
        approval = None
        if args.approval_file:
            approval = json.loads(args.approval_file.read_text())
        delivery = GitHubDelivery(release, args.repo, base=args.base)
        if args.merge_only:
            print(json.dumps(delivery.merge_existing(timeout=args.timeout), indent=2, ensure_ascii=False))
            return 0

        # A deny can be decided from the sealed packet before any network clone.
        preliminary = evaluate_release(release.packet, policy, approval=approval)
        if preliminary.decision == "deny":
            print(json.dumps({"mission": args.mission, "publication": preliminary.to_dict()},
                             indent=2, ensure_ascii=False))
            return 1

        plan = delivery.prepare(force=args.force, dry_run=args.dry_run)
        decision = evaluate_release(
            release.packet, policy,
            changed_files=plan.changed_files,
            added=plan.lines_added,
            removed=plan.lines_removed,
            approval=approval,
        )
        summary = {
            "mission": plan.mission,
            "branch": plan.branch,
            "commit": plan.commit,
            "changed_files": plan.changed_files,
            "lines_added": plan.lines_added,
            "lines_removed": plan.lines_removed,
            "issue_title": plan.issue_title,
            "pr_title": plan.pr_title,
            "publication": decision.to_dict(),
        }
        if args.dry_run:
            print(json.dumps({**summary, "dry_run": True}, indent=2, ensure_ascii=False))
            return 0
        if decision.decision == "deny":
            print(json.dumps({**summary, "error": "publication denied"}, indent=2, ensure_ascii=False))
            return 1
        if decision.decision == "human":
            print(json.dumps({
                **summary,
                "error": "human approval required",
                "approve_with": f".venv/bin/python scripts/approve_github.py --mission {args.mission} "
                                f"--reason 'reviewed the critical or oversized change'",
            }, indent=2, ensure_ascii=False))
            return 3
        if args.auto and decision.decision != "auto":
            print(json.dumps({**summary, "error": "--auto refuses a human-required publication"},
                             indent=2, ensure_ascii=False))
            return 3

        result = delivery.publish(plan, create_issue=not args.no_issue,
                                  wait=args.wait, merge=args.merge, timeout=args.timeout)
        print(json.dumps({**summary, **result}, indent=2, ensure_ascii=False))
        return 0
    except (DeliveryError, PolicyError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
