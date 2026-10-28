"""Deterministic publication and deployment policy."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.publication import (
    PolicyError,
    deploy_allowed,
    evaluate_release,
    load_policy,
    policy_digest,
)

ROOT = Path(__file__).resolve().parents[1]


def packet(*, files: list[str], added: int = 10, removed: int = 0,
           risk: str = "low", decision: str = "allow", review: str = "OK",
           tests: tuple[int, int] = (20, 20), independent: bool = True) -> dict:
    return {
        "sealed": True,
        "mission": {"id": "PD-900", "status": "completed", "outcome": "autonomous", "risk": risk},
        "gate": {
            "decision": decision,
            "checks": [
                {"id": "review", "status": "pass" if independent else "warn",
                 "detail": "Review OK · independent family" if independent else "Same family"},
            ],
        },
        "review": review,
        "diff": {
            "files": [{"path": path, "status": "A"} for path in files],
            "lines_added": added, "lines_removed": removed, "digest": "abc",
        },
        "tests": {"available": True, "exit_code": 0, "total": tests[1], "passed": tests[0],
                  "failed": 0, "errors": 0, "skipped": 0},
        "digest": "sha256:packet",
    }


@pytest.fixture()
def policy():
    return load_policy(ROOT / "config/publication-policy.json")


def test_a_single_test_file_is_auto(policy):
    result = evaluate_release(packet(files=["product/frontend/tests/x_test.ts"], added=9), policy)
    assert result.decision == "auto"
    assert result.test_only is True


def test_github_repo_paths_are_normalized(policy):
    result = evaluate_release(packet(
        files=["sandbox/parceldesk/product/frontend/tests/x_test.ts"], added=9,
    ), policy)
    assert result.decision == "auto"
    assert result.test_only is True


def test_a_small_ui_change_is_auto(policy):
    result = evaluate_release(packet(
        files=["product/frontend/lib/format.ts", "product/frontend/tests/format_x_test.ts"],
        added=40,
    ), policy)
    assert result.decision == "auto"
    assert result.test_only is False


def test_backend_production_is_human(policy):
    result = evaluate_release(packet(files=["product/backend/src/main/java/com/aeion/parceldesk/service/RefundService.java"]), policy)
    assert result.decision == "human"
    assert any("critical path" in reason for reason in result.reasons)


def test_too_many_files_or_lines_is_human(policy):
    many = evaluate_release(packet(files=[f"product/frontend/tests/t{i}_test.ts" for i in range(30)]), policy)
    assert many.decision == "human"
    assert any("file bound" in reason for reason in many.reasons)
    long = evaluate_release(packet(files=["product/frontend/lib/format.ts"], added=120, removed=10), policy)
    assert long.decision == "human"
    assert any("line bound" in reason for reason in long.reasons)


def test_high_risk_is_human(policy):
    result = evaluate_release(packet(files=["product/frontend/tests/x_test.ts"], risk="high"), policy)
    assert result.decision == "human"
    assert any("risk" in reason for reason in result.reasons)


def test_unaccepted_gate_or_review_is_denied(policy):
    for payload in (
        packet(files=["product/frontend/tests/x_test.ts"], decision="checkpoint"),
        packet(files=["product/frontend/tests/x_test.ts"], review="CHANGES_REQUIRED"),
        packet(files=["product/frontend/tests/x_test.ts"], tests=(19, 20)),
        packet(files=["product/frontend/tests/x_test.ts"], independent=False),
    ):
        assert evaluate_release(payload, policy).decision == "deny"


def test_protected_path_is_denied(policy):
    result = evaluate_release(packet(files=["policy/policy.yaml"]), policy)
    assert result.decision == "deny"
    assert any("protected path" in reason for reason in result.reasons)


def test_an_approved_local_patch_is_human_not_denied(policy):
    payload = packet(
        files=["product/frontend/fresh.gen.ts"], risk="low",
        decision="approved", review=None, independent=False,
    )
    payload["mission"]["outcome"] = "assisted"
    payload["mission"]["human_approval"] = "operator"
    result = evaluate_release(payload, policy)
    assert result.decision == "human"
    assert any("critical path" in reason or "independent review" in reason for reason in result.reasons)


def test_a_valid_operator_approval_upgrades_human(policy):
    payload = packet(files=["product/backend/src/main/java/com/aeion/parceldesk/service/RefundService.java"])
    approval = {
        "schema": "aeion.publication-approval/v1",
        "mission": "PD-900",
        "packet_digest": "sha256:packet",
        "diff_digest": "abc",
        "approved_by": "operator",
        "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason": "Reviewed the service change.",
    }
    result = evaluate_release(payload, policy, approval=approval)
    assert result.decision == "human_approved"
    assert result.reasons == ()


def test_a_stale_or_mismatched_approval_stays_human(policy):
    payload = packet(files=["product/backend/src/main/java/com/aeion/parceldesk/service/RefundService.java"])
    stale = {
        "schema": "aeion.publication-approval/v1",
        "mission": "PD-900", "packet_digest": "sha256:packet", "diff_digest": "abc",
        "approved_by": "operator",
        "approved_at": (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(timespec="seconds"),
    }
    assert evaluate_release(payload, policy, approval=stale).decision == "human"
    stale["mission"] = "PD-901"
    assert evaluate_release(payload, policy, approval=stale).decision == "human"


def test_deploy_allowed_only_for_auto_or_approved(policy):
    auto = evaluate_release(packet(files=["product/frontend/tests/x_test.ts"]), policy)
    human = evaluate_release(packet(files=["product/backend/src/main/java/x.java"]), policy)
    assert deploy_allowed(auto, policy) is True
    assert deploy_allowed(human, policy) is False


def test_policy_digest_is_stable_and_bad_policy_is_rejected(tmp_path):
    policy = load_policy(ROOT / "config/publication-policy.json")
    assert policy_digest(policy) == policy_digest(policy)
    bad = tmp_path / "policy.json"
    bad.write_text(json.dumps({"schema": "wrong"}))
    with pytest.raises(PolicyError):
        load_policy(bad)
