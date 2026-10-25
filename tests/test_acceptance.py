"""Gate-owned acceptance: a worker report is never the only test evidence."""
from __future__ import annotations

import json
from pathlib import Path

from app.agent_runner import AgentResult
from app.policy_engine import PolicyEngine
from app.sandbox import DiffResult
from app.sandbox import TestResult as SuiteResult
from test_missions import patch_runner, result, run_scenario


def _passing():
    return SuiteResult(available=True, exit_code=0, total=19, passed=19, executor={"executor": "test"})


def test_acceptance_checkout_uses_the_patched_app_and_trusted_tests(runtime):
    run = runtime.sandbox.create_run("acceptance-check")
    path = Path(run["path"])
    pricing = path / "app/pricing.py"
    pricing.write_text(pricing.read_text().replace("kilograms = grams // 1000", "kilograms = (grams + 999) // 1000"))
    (path / "tests/test_pricing.py").write_text("def test_trivial():\n    assert True\n")
    target = runtime.sandbox.acceptance_checkout(path)
    try:
        assert "Boundary contract" in (target / "tests/test_pricing.py").read_text()
        assert "test_trivial" not in (target / "tests/test_pricing.py").read_text()
        assert "(grams + 999) // 1000" in (target / "app/pricing.py").read_text()
        tests = runtime.sandbox.run_tests(target)
        assert tests.available and tests.total == 19 and tests.passed == 19
    finally:
        import shutil
        shutil.rmtree(target, ignore_errors=True)


def test_acceptance_checkout_refuses_a_symlink_in_the_patched_app(runtime):
    run = runtime.sandbox.create_run("acceptance-symlink")
    path = Path(run["path"])
    (path / "app/escape").symlink_to("/etc/hosts")
    try:
        runtime.sandbox.acceptance_checkout(path)
    except RuntimeError as error:
        assert "Symlink" in str(error)
    else:
        raise AssertionError("A symlink in the patched app must be refused")


def test_gate_requires_a_trusted_acceptance_run():
    diff = DiffResult(available=True, files=[{"path": "app/pricing.py"}], patch="fix", digest="digest")
    missing = PolicyEngine({}).evaluate(diff=diff, tests=_passing(), risk="low", cost_usd=0,
                                       review_verdict="OK", review_independent=True)
    assert missing.reason_code == "EXTERNAL_ACCEPTANCE" and missing.decision == "deny"
    failed = SuiteResult(available=True, exit_code=2, total=19, passed=12, failed=7, executor={"executor": "test"})
    denied = PolicyEngine({}).evaluate(diff=diff, tests=_passing(), risk="low", cost_usd=0,
                                       review_verdict="OK", review_independent=True, acceptance=failed)
    assert denied.reason_code == "EXTERNAL_ACCEPTANCE" and denied.decision == "deny"
    other = SuiteResult(available=True, exit_code=0, total=19, passed=19, executor={"executor": "other"})
    changed = PolicyEngine({}).evaluate(diff=diff, tests=_passing(), risk="low", cost_usd=0,
                                        review_verdict="OK", review_independent=True, acceptance=other)
    assert changed.reason_code == "ACCEPTANCE_EXECUTOR_CHANGED"
    allowed = PolicyEngine({}).evaluate(diff=diff, tests=_passing(), risk="low", cost_usd=0,
                                        review_verdict="OK", review_independent=True, acceptance=_passing())
    assert allowed.decision == "allow"


def test_weakened_worker_tests_are_denied_by_the_trusted_run(runtime):
    calls = []

    def weakened(**kwargs):
        calls.append(kwargs["agent_id"])
        if kwargs.get("review"):
            return result("sentinel", verdict="OK", reason="Would have accepted the weakened suite.")
        (kwargs["cwd"] / "tests/test_pricing.py").write_text("def test_trivial():\n    assert True\n")
        return result("forge", num_turns=2)

    mission = run_scenario(runtime, "PD01", weakened)
    stored = json.loads(mission["tests_json"])
    assert stored["total"] == 1 and stored["passed"] == 1
    assert stored["acceptance"]["failed"] > 0
    assert mission["status"] == "blocked" and mission["gate_reason_code"] == "EXTERNAL_ACCEPTANCE"
    assert calls == ["forge"]


def test_a_real_repair_passes_both_the_worker_and_the_trusted_suite(runtime):
    mission = run_scenario(runtime, "PD01", patch_runner)
    stored = json.loads(mission["tests_json"])
    assert stored["passed"] == 19 and stored["acceptance"]["passed"] == 19
    assert mission["status"] == "awaiting_human" and mission["gate_reason_code"] == "HUMAN_CHECKPOINT"
    packet = runtime.store.evidence_for(mission["id"])
    assert packet["payload"]["payload"]["tests"]["acceptance"]["passed"] == 19


def test_acceptance_checkout_keeps_product_implementation_and_trusted_product_tests(runtime):
    run = runtime.sandbox.create_run("acceptance-product")
    path = Path(run["path"])
    view = path / "product/backend/src/main/java/com/aeion/parceldesk/dto/OrderView.java"
    view.write_text(view.read_text().replace("Instant deliveredAt)", "Instant deliveredAt, String refundReason)"))
    added = path / "product/backend/src/test/java/com/aeion/parceldesk/RefundedOrderListTest.java"
    added.write_text("class RefundedOrderListTest {}\n")
    target = runtime.sandbox.acceptance_checkout(path)
    try:
        accepted = target / "product/backend/src/main/java/com/aeion/parceldesk/dto/OrderView.java"
        assert "String refundReason" in accepted.read_text()
        assert not (target / "product/backend/src/test/java/com/aeion/parceldesk/RefundedOrderListTest.java").exists()
        assert (target / "product/backend/src/test/java/com/aeion/parceldesk/OrderApiTest.java").is_file()
    finally:
        import shutil
        shutil.rmtree(target, ignore_errors=True)
