"""Deterministic gate policy.

The policy is read from the trusted template, never from the mission copy, so an
agent cannot edit the rule that judges it. Every check below is computed from a
real git diff, a real pytest run and a real agent result.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any

from .sandbox import DiffResult, TestResult


@dataclass
class GateCheck:
    id: str
    label: str
    status: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label, "status": self.status, "detail": self.detail}


@dataclass
class GateDecision:
    decision: str
    reason_code: str
    reason: str
    checks: list[GateCheck] = field(default_factory=list)
    protected_touched: list[str] = field(default_factory=list)
    requires_human: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "checks": [check.to_dict() for check in self.checks],
            "protected_touched": self.protected_touched,
            "requires_human": self.requires_human,
        }


def matches_path(path: str, patterns: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    return any(
        fnmatch.fnmatch(normalized, pattern)
        or normalized == pattern.rstrip("/**")
        or normalized.startswith(pattern.rstrip("*").rstrip("/") + "/")
        for pattern in patterns
    )


class PolicyEngine:
    def __init__(self, policy: dict[str, Any]) -> None:
        self.policy = policy
        self.protected = list(policy.get("protected_paths") or [])
        limits = policy.get("limits") or {}
        self.max_changed_files = int(limits.get("max_changed_files", 6))
        self.max_diff_lines = int(limits.get("max_diff_lines", 200))
        self.budget_usd = float(limits.get("budget_usd", 2.0))
        approval = policy.get("approval") or {}
        self.checkpoint_paths = list(approval.get("checkpoint_paths") or [])
        self.required_for_risk = set(approval.get("required_for_risk") or [])

    def evaluate(
        self,
        *,
        diff: DiffResult,
        tests: TestResult,
        risk: str,
        cost_usd: float,
        review_verdict: str | None,
        review_independent: bool,
        allowed_paths: list[str] | None = None,
        minimum_tests: int = 1,
        acceptance: TestResult | None = None,
    ) -> GateDecision:
        checks: list[GateCheck] = []

        if not diff.available:
            checks.append(GateCheck("diff", "Diff captured", "fail", diff.error or "no diff"))
            return GateDecision("deny", "NO_DIFF", diff.error or "The diff could not be captured.", checks)
        checks.append(
            GateCheck(
                "diff",
                "Diff captured",
                "pass",
                f"{len(diff.files)} file(s) · +{diff.lines_added}/-{diff.lines_removed} · {diff.digest[:12]}",
            )
        )

        if not diff.files:
            checks.append(GateCheck("patch", "Patch present", "fail", "The agent produced no change."))
            return GateDecision("deny", "NO_PATCH", "The agent produced no change.", checks)
        checks.append(GateCheck("patch", "Patch present", "pass", ", ".join(diff.changed_files)))

        protected_touched = [path for path in diff.changed_files if matches_path(path, self.protected)]
        if protected_touched:
            checks.append(
                GateCheck(
                    "protected",
                    "Protected paths untouched",
                    "fail",
                    "Touched: " + ", ".join(protected_touched),
                )
            )
            return GateDecision(
                "deny",
                "PROTECTED_PATH",
                "The patch touches a protected path: " + ", ".join(protected_touched),
                checks,
                protected_touched=protected_touched,
            )
        checks.append(GateCheck("protected", "Protected paths untouched", "pass", "No protected path in the diff."))

        outside_scope = [path for path in diff.changed_files if not matches_path(path, ["app/**", "tests/**"])]
        if outside_scope:
            checks.append(GateCheck("paths", "Task edit scope", "fail", ", ".join(outside_scope)))
            return GateDecision("deny", "OUT_OF_SCOPE", "The task permits edits only in app/** and tests/**: " + ", ".join(outside_scope), checks)
        checks.append(GateCheck("paths", "Task edit scope", "pass", "Every changed path is inside app/** or tests/**."))

        if allowed_paths is not None:
            outside_task = [path for path in diff.changed_files if not matches_path(path, allowed_paths)]
            if outside_task:
                checks.append(GateCheck("task_paths", "Admitted task paths", "fail", ", ".join(outside_task)))
                return GateDecision("deny", "TASK_SCOPE", "The patch exceeds the admitted task paths: " + ", ".join(outside_task), checks)
            checks.append(GateCheck("task_paths", "Admitted task paths", "pass", ", ".join(allowed_paths)))

        if len(diff.files) > self.max_changed_files:
            checks.append(
                GateCheck(
                    "scope",
                    "Changed-file bound",
                    "fail",
                    f"{len(diff.files)} files > {self.max_changed_files}",
                )
            )
            return GateDecision("deny", "DIFF_SCOPE", f"The diff changes {len(diff.files)} files (limit {self.max_changed_files}).", checks)
        checks.append(
            GateCheck("scope", "Changed-file bound", "pass", f"{len(diff.files)} ≤ {self.max_changed_files} files")
        )

        diff_lines = diff.lines_added + diff.lines_removed
        if diff_lines > self.max_diff_lines:
            checks.append(
                GateCheck("size", "Diff-size bound", "fail", f"{diff_lines} lines > {self.max_diff_lines}")
            )
            return GateDecision("deny", "DIFF_SIZE", f"The diff has {diff_lines} changed lines (limit {self.max_diff_lines}).", checks)
        checks.append(GateCheck("size", "Diff-size bound", "pass", f"{diff_lines} ≤ {self.max_diff_lines} lines"))

        if not tests.available:
            checks.append(GateCheck("tests", "Tests executed", "fail", tests.error or "pytest unavailable"))
            return GateDecision("deny", "TESTS_UNAVAILABLE", tests.error or "pytest is unavailable.", checks)
        if tests.error:
            checks.append(GateCheck("tests", "Test execution completed", "fail", tests.error))
            return GateDecision("deny", "TEST_EXECUTION_ERROR", tests.error, checks)
        if tests.total == 0:
            checks.append(GateCheck("tests", "Tests executed", "fail", "No test was collected."))
            return GateDecision("deny", "NO_TESTS", "No test was collected.", checks)
        if tests.total < minimum_tests:
            checks.append(GateCheck("test_count", "Admitted test count", "fail", f"{tests.total} < {minimum_tests}"))
            return GateDecision("deny", "TEST_COVERAGE", "The admitted task's minimum test count was not reached.", checks)
        checks.append(GateCheck("tests", "Tests executed", "pass", tests.summary()))

        if tests.failed or tests.errors:
            checks.append(
                GateCheck(
                    "tests_pass",
                    "Tests pass",
                    "fail",
                    f"{tests.failed} failed · {tests.errors} errors",
                )
            )
            return GateDecision(
                "deny",
                "TEST_FAILURE",
                f"{tests.failed} test(s) failed and {tests.errors} errored.",
                checks,
            )
        if tests.exit_code != 0 or tests.skipped or tests.passed != tests.total:
            checks.append(GateCheck("tests_pass", "Tests pass", "fail", "The suite did not pass completely with exit code 0."))
            return GateDecision("deny", "TESTS_INCOMPLETE", "Every collected test must pass with exit code 0; skipped or incomplete tests are not accepted.", checks)
        checks.append(GateCheck("tests_pass", "Tests pass", "pass", f"{tests.passed}/{tests.total} passed"))

        if acceptance is None:
            checks.append(GateCheck("acceptance", "Gate-owned trusted tests", "fail",
                                    "No trusted acceptance run was recorded."))
            return GateDecision(
                "deny", "EXTERNAL_ACCEPTANCE",
                "The worker report was not checked against a Gate-owned trusted test run.",
                checks,
            )
        if acceptance.executor != tests.executor:
            checks.append(GateCheck("acceptance", "Gate-owned trusted tests", "fail",
                                    "The acceptance run used a different executor identity."))
            return GateDecision(
                "deny", "ACCEPTANCE_EXECUTOR_CHANGED",
                "The trusted acceptance run did not use the admitted executor.",
                checks,
            )
        if (
            not acceptance.available or acceptance.error or acceptance.exit_code != 0
            or not acceptance.total or acceptance.passed != acceptance.total
            or acceptance.failed or acceptance.errors or acceptance.skipped
        ):
            checks.append(GateCheck("acceptance", "Gate-owned trusted tests", "fail",
                                    acceptance.summary()))
            return GateDecision(
                "deny", "EXTERNAL_ACCEPTANCE",
                "The trusted tests failed against the submitted application: " + acceptance.summary(),
                checks,
            )
        checks.append(GateCheck("acceptance", "Gate-owned trusted tests", "pass",
                                f"{acceptance.passed}/{acceptance.total} trusted tests passed"))

        if cost_usd > self.budget_usd:
            checks.append(
                GateCheck("budget", "Budget bound", "fail", f"${cost_usd:.2f} > ${self.budget_usd:.2f}")
            )
            return GateDecision("deny", "BUDGET_EXCEEDED", f"Agent cost ${cost_usd:.2f} exceeds the ${self.budget_usd:.2f} envelope.", checks)
        checks.append(GateCheck("budget", "Budget bound", "pass", f"${cost_usd:.2f} ≤ ${self.budget_usd:.2f}"))

        if review_verdict == "CHANGES_REQUIRED":
            checks.append(GateCheck("review", "Independent review", "fail", "Reviewer requested changes."))
            return GateDecision("deny", "REVIEW_REJECTED", "The independent reviewer requested changes.", checks)
        if review_verdict != "OK":
            checks.append(
                GateCheck(
                    "review",
                    "Independent review",
                    "warn",
                    "No successful review run." if review_verdict is None else f"Review verdict: {review_verdict}",
                )
            )
        elif not review_independent:
            checks.append(
                GateCheck(
                    "review",
                    "Independent review",
                    "warn",
                    "Review ran on the same model family; independence is not established.",
                )
            )
        else:
            checks.append(GateCheck("review", "Independent review", "pass", "Review OK · independent family"))

        critical_path = [path for path in diff.changed_files if matches_path(path, self.checkpoint_paths)]
        if risk in self.required_for_risk or critical_path or review_verdict != "OK" or not review_independent:
            reasons = []
            if risk in self.required_for_risk:
                reasons.append(f"risk={risk}")
            if critical_path:
                reasons.append("business-critical path " + ", ".join(critical_path))
            if review_verdict != "OK":
                reasons.append("review not OK")
            if not review_independent:
                reasons.append("review family not independent")
            checks.append(GateCheck("human", "Human checkpoint", "warn", "; ".join(reasons)))
            return GateDecision(
                "checkpoint",
                "HUMAN_CHECKPOINT",
                "Operator approval required: " + "; ".join(reasons) + ".",
                checks,
                requires_human=True,
            )

        checks.append(GateCheck("human", "Human checkpoint", "pass", "Not required for this bounded low-risk change."))
        return GateDecision("allow", "CLEAN", "All deterministic checks passed; no human checkpoint required.", checks)
