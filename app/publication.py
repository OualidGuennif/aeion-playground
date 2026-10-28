"""Deterministic publication and deployment policy.

A sealed mission is not automatically publishable. This module adds a second,
file-and-size based gate between the Factory and GitHub/deployment. It never
uses a model verdict to decide whether a release is small enough or whether a
path is critical.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "aeion.publication-policy/v1"
DECISIONS = {"auto", "human", "human_approved", "deny"}


class PolicyError(ValueError):
    """The publication policy is malformed; nothing may be published."""


@dataclass(frozen=True)
class PublicationDecision:
    decision: str
    reasons: tuple[str, ...]
    changed_files: tuple[str, ...]
    added: int
    removed: int
    test_only: bool
    policy_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reasons": list(self.reasons),
            "changed_files": list(self.changed_files),
            "added": self.added,
            "removed": self.removed,
            "test_only": self.test_only,
            "policy_digest": self.policy_digest,
        }


def policy_digest(policy: dict[str, Any]) -> str:
    canonical = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def load_policy(path: Path) -> dict[str, Any]:
    try:
        policy = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise PolicyError(f"Cannot read publication policy: {error}") from error
    if not isinstance(policy, dict) or policy.get("schema") != SCHEMA:
        raise PolicyError("A publication policy with schema aeion.publication-policy/v1 is required.")
    auto = policy.get("auto")
    if not isinstance(auto, dict):
        raise PolicyError("Publication policy has no auto section.")
    for key in ("max_files", "max_diff_lines", "max_test_files", "max_test_lines"):
        value = auto.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PolicyError(f"Publication policy auto.{key} must be a non-negative integer.")
    for key in ("auto_paths", "critical_paths", "protected_paths"):
        value = auto.get(key)
        if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
            raise PolicyError(f"Publication policy auto.{key} must be a non-empty string list.")
    if not isinstance(auto.get("human_risks"), list):
        raise PolicyError("Publication policy auto.human_risks must be a list.")
    approval = policy.get("approval")
    if not isinstance(approval, dict) or not isinstance(approval.get("max_age_hours"), (int, float)):
        raise PolicyError("Publication policy approval.max_age_hours must be numeric.")
    deploy = policy.get("deploy")
    if not isinstance(deploy, dict) or not isinstance(deploy.get("allowed_decisions"), list):
        raise PolicyError("Publication policy deploy.allowed_decisions must be a list.")
    return policy


def _normalize(path: str) -> str:
    clean = path.replace("\\", "/")
    for prefix in ("sandbox/parceldesk/", "./"):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
    return clean


def _matches(path: str, patterns: list[str]) -> bool:
    clean = _normalize(path).lstrip("./")
    for pattern in patterns:
        value = pattern.rstrip("/")
        if pattern.endswith("/"):
            if clean == value or clean.startswith(value + "/"):
                return True
        elif clean == value:
            return True
    return False


def _files_from_packet(packet: dict[str, Any]) -> list[str]:
    diff = packet.get("diff") if isinstance(packet.get("diff"), dict) else {}
    files = diff.get("files")
    if isinstance(files, list):
        output = []
        for item in files:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                output.append(item["path"])
            elif isinstance(item, str):
                output.append(item)
        if output:
            return output
    changed = diff.get("changed_files")
    if isinstance(changed, list) and all(isinstance(item, str) for item in changed):
        return list(changed)
    return []


def _gate_is_accepted(packet: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    mission = packet.get("mission") if isinstance(packet.get("mission"), dict) else {}
    gate = packet.get("gate") if isinstance(packet.get("gate"), dict) else {}
    tests = packet.get("tests") if isinstance(packet.get("tests"), dict) else {}
    if packet.get("sealed") is not True:
        reasons.append("packet is not sealed")
    if mission.get("status") != "completed":
        reasons.append("mission is not completed")
    decision = gate.get("decision")
    outcome = mission.get("outcome")
    if decision == "allow":
        if outcome != "autonomous":
            reasons.append("allow gate requires an autonomous mission")
    elif decision == "approved":
        if outcome != "assisted" or mission.get("human_approval") != "operator":
            reasons.append("approved gate requires recorded operator approval")
    else:
        reasons.append(f"gate decision is {decision!r}, not allow/approved")
    if tests.get("available") is not True or int(tests.get("total") or 0) <= 0:
        reasons.append("trusted test report is missing")
    if int(tests.get("passed") or 0) != int(tests.get("total") or 0):
        reasons.append("not every trusted test passed")
    if int(tests.get("failed") or 0) or int(tests.get("errors") or 0) or int(tests.get("skipped") or 0):
        reasons.append("trusted tests contain failures, errors or skips")
    if tests.get("exit_code") not in (0, None):
        reasons.append("trusted test exit code is not zero")
    checks = gate.get("checks") if isinstance(gate.get("checks"), list) else []
    independent = any(
        isinstance(check, dict) and check.get("id") == "review" and check.get("status") == "pass"
        and "independent" in str(check.get("detail") or "").lower()
        for check in checks
    )
    review_ok = packet.get("review") == "OK" and independent
    if decision == "allow" and not review_ok:
        reasons.append("independent review is not established for an autonomous release")
    return not reasons, reasons


def _approval_is_valid(packet: dict[str, Any], policy: dict[str, Any], approval: dict[str, Any] | None) -> tuple[bool, list[str]]:
    if not isinstance(approval, dict):
        return False, ["no publication approval file"]
    reasons: list[str] = []
    mission = packet.get("mission") if isinstance(packet.get("mission"), dict) else {}
    diff = packet.get("diff") if isinstance(packet.get("diff"), dict) else {}
    if approval.get("schema") != "aeion.publication-approval/v1":
        reasons.append("approval schema is invalid")
    if approval.get("mission") != mission.get("id"):
        reasons.append("approval mission does not match")
    if approval.get("packet_digest") != packet.get("digest") and approval.get("packet_digest") != packet.get("packet_digest"):
        # Local delivery receipts carry the packet digest separately; the packet
        # itself carries its own digest under "digest".
        if approval.get("packet_digest") not in {packet.get("digest"), packet.get("packet_digest")}:
            reasons.append("approval packet digest does not match")
    if approval.get("diff_digest") != diff.get("digest"):
        reasons.append("approval diff digest does not match")
    if not isinstance(approval.get("approved_by"), str) or not approval["approved_by"].strip():
        reasons.append("approval has no operator identity")
    try:
        approved_at = datetime.fromisoformat(str(approval.get("approved_at", "")).replace("Z", "+00:00"))
        if approved_at.tzinfo is None:
            approved_at = approved_at.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - approved_at).total_seconds() / 3600
        if age_hours < 0 or age_hours > float(policy["approval"]["max_age_hours"]):
            reasons.append("approval is outside its validity window")
    except (TypeError, ValueError):
        reasons.append("approval timestamp is invalid")
    return not reasons, reasons


def evaluate_release(
    packet: dict[str, Any],
    policy: dict[str, Any],
    *,
    changed_files: list[str] | None = None,
    added: int | None = None,
    removed: int | None = None,
    approval: dict[str, Any] | None = None,
) -> PublicationDecision:
    """Evaluate a sealed packet (and optional PR-shaped diff) deterministically."""
    if not isinstance(packet, dict):
        raise PolicyError("A sealed packet is required.")
    auto = policy["auto"]
    files = list(changed_files) if changed_files is not None else _files_from_packet(packet)
    diff = packet.get("diff") if isinstance(packet.get("diff"), dict) else {}
    added = int(added if added is not None else diff.get("lines_added") or 0)
    removed = int(removed if removed is not None else diff.get("lines_removed") or 0)
    digest = policy_digest(policy)

    accepted, deny_reasons = _gate_is_accepted(packet)
    if not accepted:
        return PublicationDecision("deny", tuple(deny_reasons), tuple(files), added, removed, False, digest)

    protected = [path for path in files if _matches(path, auto["protected_paths"])]
    if protected:
        return PublicationDecision(
            "deny", tuple(f"protected path: {path}" for path in protected),
            tuple(files), added, removed, False, digest,
        )

    mission = packet.get("mission") if isinstance(packet.get("mission"), dict) else {}
    risk = str(mission.get("risk") or "")
    test_prefixes = ("product/frontend/tests/", "product/backend/src/test/")
    test_only = bool(files) and all(
        any(_normalize(path).startswith(prefix) for prefix in test_prefixes) for path in files
    )
    critical = [path for path in files if _matches(path, auto["critical_paths"])]
    unknown = [path for path in files if not _matches(path, auto["auto_paths"]) and path not in critical]

    reasons: list[str] = []
    gate = packet.get("gate") if isinstance(packet.get("gate"), dict) else {}
    if gate.get("decision") == "approved" and (packet.get("review") != "OK"
                                               or not any(
                                                   isinstance(check, dict) and check.get("id") == "review"
                                                   and check.get("status") == "pass"
                                                   and "independent" in str(check.get("detail") or "").lower()
                                                   for check in (gate.get("checks") or [])
                                               )):
        reasons.append("independent review is not established; operator publication approval is required")
    if risk in auto["human_risks"]:
        reasons.append(f"risk is {risk}")
    if critical:
        reasons.extend(f"critical path: {path}" for path in critical)
    if unknown:
        reasons.extend(f"path is not in the auto allowlist: {path}" for path in unknown)
    max_files = auto["max_test_files"] if test_only else auto["max_files"]
    max_lines = auto["max_test_lines"] if test_only else auto["max_diff_lines"]
    if len(files) > max_files:
        reasons.append(f"{len(files)} files exceeds the {max_files}-file bound")
    if added + removed > max_lines:
        reasons.append(f"{added + removed} changed lines exceeds the {max_lines}-line bound")

    if not reasons:
        return PublicationDecision("auto", (), tuple(files), added, removed, test_only, digest)
    approved, approval_reasons = _approval_is_valid(packet, policy, approval)
    if approved:
        return PublicationDecision("human_approved", (), tuple(files), added, removed, test_only, digest)
    return PublicationDecision("human", tuple(reasons + approval_reasons), tuple(files), added, removed, test_only, digest)


def deploy_allowed(decision: PublicationDecision, policy: dict[str, Any]) -> bool:
    return decision.decision in set(policy["deploy"]["allowed_decisions"])
