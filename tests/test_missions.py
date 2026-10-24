"""Regression checks for real sandbox execution and fail-closed acceptance."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent_runner import AgentResult
from app.evidence import EvidenceWriter


def result(agent, **fields):
    return AgentResult(status="completed", runtime="test-runner", run_id=f"test-{agent}", **fields)


def run_scenario(runtime, fixture, runner=None):
    if runner:
        runtime.monkeypatch.setattr(runtime.module, "agent_runner", SimpleNamespace(run=runner))
    mission = runtime.engine.launch_mission(fixture)
    runtime.engine._execute_mission(mission["id"], runtime.module.SCENARIOS[fixture])
    return runtime.store.mission(mission["id"])


def test_baseline_uses_real_failing_suite(runtime):
    mission = run_scenario(runtime, "PD00")
    tests = json.loads(mission["tests_json"])
    assert (tests["total"], tests["passed"], tests["failed"]) == (19, 12, 7)
    assert mission["gate_reason_code"] == "NO_PATCH"
    assert not runtime.store.runs(mission["id"])


def test_protected_path_denied_even_when_actual_tests_pass(runtime):
    mission = run_scenario(runtime, "PD03")
    tests = json.loads(mission["tests_json"])
    assert tests["exit_code"] == 0
    assert tests["passed"] == 19
    assert mission["gate_reason_code"] == "PROTECTED_PATH"
    assert mission["status"] == "blocked"
    assert json.loads(mission["gate_json"])["protected_touched"] == ["policy/policy.yaml"]


def test_completed_agent_without_patch_blocks_before_review_and_tests(runtime):
    calls = []
    def runner(**kwargs):
        calls.append(kwargs["agent_id"])
        return result("forge", num_turns=4, result_text="Edits were denied.")
    mission = run_scenario(runtime, "PD01", runner)
    assert calls == ["forge"]
    assert mission["gate_reason_code"] == "NO_PATCH"
    assert mission["tests_json"] is None
    packet = runtime.store.evidence_for(mission["id"])
    assert packet["payload"]["payload"]["gate"]["reason_code"] == "NO_PATCH"
    assert not packet["payload"]["payload"]["sealed"]


def patch_runner(**kwargs):
    if kwargs.get("review"):
        return result("sentinel", verdict="OK", reason="Bounded rounding repair.")
    path = kwargs["cwd"] / "app/pricing.py"
    path.write_text(path.read_text().replace("kilograms = grams // 1000", "kilograms = (grams + 999) // 1000"))
    return result("forge", num_turns=4)


def test_patch_test_review_checkpoint_approval_and_seal(runtime):
    mission = run_scenario(runtime, "PD01", patch_runner)
    assert mission["status"] == "awaiting_human"
    assert mission["gate_reason_code"] == "HUMAN_CHECKPOINT"
    assert json.loads(mission["tests_json"])["passed"] == 19
    assert [f["path"] for f in json.loads(mission["diff_json"])["files"]] == ["app/pricing.py"]
    steps = runtime.store.steps(mission["id"])
    assert len(steps) == len({step["idx"] for step in steps})
    assert runtime.engine.approve(mission["id"])
    assert not runtime.engine.approve(mission["id"])
    sealed = runtime.store.evidence_for(mission["id"])
    body = sealed["payload"]["payload"]
    assert body["sealed"] and body["mission"]["human_approval"] == "operator"
    assert body["gate"]["decision"] == "approved"
    assert sealed["status"] == "sealed"
    from pathlib import Path
    assert EvidenceWriter.verify(Path(sealed["path"]))["valid"]


def test_changed_patch_cannot_be_approved(runtime):
    mission = run_scenario(runtime, "PD01", patch_runner)
    from pathlib import Path
    path = Path(mission["sandbox_path"]) / "app/pricing.py"
    path.write_text(path.read_text() + "\n# changed after review\n")
    assert not runtime.engine.approve(mission["id"])
    assert runtime.store.mission(mission["id"])["gate_reason_code"] == "CHECKPOINT_CHANGED"
    assert runtime.store.evidence_for(mission["id"])["status"] == "draft"


def test_agent_failure_is_explicit_and_terminal(runtime):
    def runner(**kwargs):
        return AgentResult(status="failed", runtime="test", run_id="quota", error="HTTP 429 quota exhausted")
    mission = run_scenario(runtime, "PD01", runner)
    assert mission["status"] == "blocked"
    assert "429" in mission["blocked_reason"]
    assert mission["gate_reason_code"] == "PROVIDER_LIMIT"
    assert float(runtime.store.get_kv("provider_retry_at")) > runtime.module.time.time()
    assert "provider cooldown until" in runtime.engine.health()["components"]["agent"]["detail"]


def test_successful_review_cannot_accept_a_patch_changed_after_testing(runtime):
    def reviewer_mutates(**kwargs):
        reply = patch_runner(**kwargs)
        if kwargs.get("review"):
            path = kwargs["cwd"] / "app/pricing.py"
            path.write_text(path.read_text().replace("(grams + 999) // 1000", "grams // 1000"))
        return reply
    mission = run_scenario(runtime, "PD01", reviewer_mutates)
    assert json.loads(mission["tests_json"])["passed"] == 19
    assert mission["gate_reason_code"] == "REVIEW_CHANGED_PATCH"
    assert mission["status"] == "blocked"
    assert runtime.store.evidence_for(mission["id"])["status"] == "draft"


def test_test_code_cannot_rewrite_source_or_trigger_review_after_failure(runtime):
    def self_modifying_tests(**kwargs):
        assert not kwargs.get("review"), "Review must not run on an unbound test result"
        reply = patch_runner(**kwargs)
        (kwargs["cwd"] / "tests/test_rewrite.py").write_text(
            'from pathlib import Path\n'
            'def test_rewrite():\n'
            '    path = Path("app/pricing.py")\n'
            '    path.write_text(path.read_text().replace("(grams + 999) // 1000", "grams // 1000"))\n'
        )
        return reply
    mission = run_scenario(runtime, "PD01", self_modifying_tests)
    tests = json.loads(mission["tests_json"])
    assert tests["passed"] == 19 and tests["failed"] == 1
    assert "PermissionError" in tests["stdout"]
    assert tests["isolation"]["backend"] == "macos-seatbelt/v1"
    assert "(grams + 999) // 1000" in (Path(mission["sandbox_path"]) / "app/pricing.py").read_text()
    assert mission["gate_reason_code"] == "TEST_FAILURE"
    assert mission["status"] == "blocked"


def test_background_exception_does_not_leave_a_running_mission(runtime, monkeypatch):
    def crash(*args):
        raise OSError("checkout unavailable")
    monkeypatch.setattr(runtime.sandbox, "collect_changes", crash)
    mission = run_scenario(runtime, "PD01", patch_runner)
    assert mission["status"] == "blocked"
    assert mission["gate_reason_code"] == "EXECUTION_FAILED"


def test_launch_rejects_unknown_scenario_and_active_capacity(runtime):
    with pytest.raises(ValueError):
        runtime.engine.launch_mission("PD99")
    runtime.engine.launch_mission("PD00")
    with pytest.raises(RuntimeError, match="capacity"):
        runtime.engine.launch_mission("PD01")


def test_local_contribution_is_verified_without_model_calls(runtime, monkeypatch):
    import difflib
    from pathlib import Path
    original = (runtime.sandbox.template_dir / "app/pricing.py").read_text()
    repaired = original.replace("kilograms = grams // 1000", "kilograms = (grams + 999) // 1000")
    patch = "".join(difflib.unified_diff(original.splitlines(True), repaired.splitlines(True), fromfile="a/app/pricing.py", tofile="b/app/pricing.py"))
    monkeypatch.setattr(runtime.engine, "agent_probe", lambda **kw: pytest.fail("Local contribution must not probe a provider"))
    mission = runtime.engine.launch_mission("PD01", local_patch=patch)
    scenario = {**runtime.module.SCENARIOS["PD01"], "kind": "local-patch", "patch": patch}
    runtime.engine._execute_mission(mission["id"], scenario)
    current = runtime.store.mission(mission["id"])
    assert current["status"] == "awaiting_human"
    assert json.loads(current["tests_json"])["passed"] == 19
    assert not runtime.store.runs(mission["id"])
    body = runtime.store.evidence_for(mission["id"])["payload"]["payload"]
    assert body["review"] == "NOT RUN"
    assert "local patch" in body["mission"]["execution_source"]
    assert not body["sealed"]


def test_invalid_local_patch_blocks_without_running_models(runtime):
    mission = runtime.engine.launch_mission("PD01", local_patch="invalid patch")
    runtime.engine._execute_mission(mission["id"], {**runtime.module.SCENARIOS["PD01"], "kind": "local-patch", "patch": "invalid patch"})
    assert runtime.store.mission(mission["id"])["gate_reason_code"] == "LOCAL_PATCH_INVALID"


def test_renaming_a_protected_file_does_not_hide_its_source(runtime):
    from pathlib import Path
    from app.policy_engine import PolicyEngine
    from app.sandbox import TestResult as SuiteResult
    run = runtime.sandbox.create_run("rename-check")
    path = Path(run["path"])
    (path / "policy/policy.yaml").rename(path / "app/moved_policy.yaml")
    diff = runtime.sandbox.collect_changes(path)
    assert "policy/policy.yaml" in diff.changed_files
    gate = PolicyEngine(runtime.sandbox.read_policy()).evaluate(
        diff=diff, tests=SuiteResult(available=True, exit_code=0, total=19, passed=19),
        risk="low", cost_usd=0, review_verdict="OK", review_independent=True,
    )
    assert gate.reason_code == "PROTECTED_PATH"


def test_agent_commit_cannot_hide_a_critical_path_from_the_admitted_base(runtime):
    def commits_repair(**kwargs):
        reply = patch_runner(**kwargs)
        if not kwargs.get("review"):
            runtime.sandbox._git(kwargs["cwd"], "add", "-A")
            committed = runtime.sandbox._git(kwargs["cwd"], "commit", "-q", "-m", "agent commit")
            assert committed.returncode == 0
            (kwargs["cwd"] / "tests/test_extra.py").write_text(
                'from app.pricing import quote\n'
                'def test_extra():\n'
                '    assert quote(1001) == 800\n'
            )
        return reply
    mission = run_scenario(runtime, "PD02", commits_repair)
    assert mission["status"] == "awaiting_human"
    assert mission["gate_reason_code"] == "HUMAN_CHECKPOINT"
    assert "app/pricing.py" in json.loads(mission["diff_json"])["changed_files"]
    assert "business-critical path" in mission["gate_reason"]
