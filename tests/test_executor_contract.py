"""Frozen executor identity with real local tests; all model replies are controlled."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import sandbox as sandbox_module, test_isolation
from app.delivery import LocalDelivery
from app.evidence import EvidenceWriter
from app.sandbox import SandboxManager
from test_delivery import accepted
from test_missions import patch_runner
from test_native_isolation import checkout
from test_product_loop import bootstrap, coverage_runner, start_product_task


def drift(monkeypatch):
    monkeypatch.setattr(test_isolation, "PROFILE", test_isolation.PROFILE + "\n; controlled identity drift\n")


def test_admission_worker_seal_and_delivery_bind_same_executor_despite_distinct_scratch(runtime, monkeypatch):
    bootstrap(runtime)
    mission = start_product_task(runtime)
    assert mission["status"] == "completed" and mission["human_approval"] is None
    expected = json.loads(mission["contract_json"])["test_executor"]
    tests = json.loads(mission["tests_json"])
    packet = runtime.store.evidence_for(mission["id"])
    receipt = json.loads(runtime.store.get_kv(f"delivery:{mission['id']}"))
    assert expected == tests["executor"] == packet["payload"]["payload"]["contract"]["test_executor"] == receipt["test_executor"]
    assert expected["limits"] == tests["limits"]
    assert expected["isolation"]["pytest_version"]
    assert tests["isolation"]["parameters_digest"] != receipt["test_isolation"]["parameters_digest"]
    packet_bytes = Path(packet["path"]).read_bytes()
    drift(monkeypatch)
    monkeypatch.setattr(runtime.sandbox, "run_tests", lambda *a, **kw: pytest.fail("Existing receipt must not replay tests"))
    assert LocalDelivery(runtime.store, runtime.sandbox).deliver(mission["id"]) == receipt
    assert Path(packet["path"]).read_bytes() == packet_bytes


@pytest.mark.parametrize("changed", ["profile", "bounds"])
def test_queued_executor_drift_blocks_before_checkout_or_inference(runtime, monkeypatch, changed):
    bootstrap(runtime)
    mission = runtime.engine.launch_mission("PD04", request_id="executor-drift", defer=True)
    if changed == "profile":
        drift(monkeypatch)
    else:
        monkeypatch.setattr(sandbox_module, "MAX_JUNIT_CASES", 999)
    monkeypatch.setattr(runtime.module, "agent_runner", SimpleNamespace(run=lambda **kw: pytest.fail("No inference")))
    assert runtime.engine.dispatch_once()
    assert runtime.store.mission(mission["id"])["gate_reason_code"] == "TEST_EXECUTOR_CHANGED"
    assert not runtime.store.runs(mission["id"])
    assert not (runtime.sandbox.runs_dir / mission["id"]).exists()


def test_executor_drift_during_author_refuses_tests_and_review(runtime, monkeypatch):
    bootstrap(runtime)
    calls = []
    def author(**kwargs):
        calls.append(kwargs["agent_id"])
        reply = coverage_runner(**kwargs)
        drift(monkeypatch)
        return reply
    mission = start_product_task(runtime, author)
    assert calls == ["forge"] and mission["gate_reason_code"] == "TEST_EXECUTOR_CHANGED"
    assert not json.loads(mission["tests_json"])["available"]
    assert not (Path(mission["sandbox_path"]) / "junit.xml").exists()


def test_executor_drift_during_review_cannot_reach_acceptance(runtime, monkeypatch):
    bootstrap(runtime)
    def reviewer(**kwargs):
        reply = coverage_runner(**kwargs)
        if kwargs.get("review"):
            drift(monkeypatch)
        return reply
    mission = start_product_task(runtime, reviewer)
    assert json.loads(mission["tests_json"])["passed"] == 49
    assert mission["status"] == "blocked" and mission["gate_reason_code"] == "TEST_EXECUTOR_CHANGED"
    assert runtime.store.evidence_for(mission["id"])["status"] == "draft"
    assert runtime.store.get_kv(f"delivery:{mission['id']}") is None


def test_mismatched_executor_preserves_prior_report_and_starts_no_process(tmp_path, monkeypatch):
    workspace = SandboxManager(tmp_path / "runs")
    root = checkout(tmp_path, "def test_ok(): assert True\n")
    report = root / "junit.xml"
    report.write_text("public previous report")
    expected = copy.deepcopy(workspace.executor_contract())
    expected["isolation"]["pytest_version"] = "different-admitted-version"
    monkeypatch.setattr(sandbox_module, "_run", lambda *a, **kw: pytest.fail("No process"))
    result = workspace.run_tests(root, expected_executor=expected)
    assert not result.available and result.total == 0 and "differs from the admitted" in result.error
    assert report.read_text() == "public previous report"


def test_executor_drift_while_real_pytest_runs_invalidates_passing_output(tmp_path, monkeypatch):
    workspace = SandboxManager(tmp_path / "runs")
    root = checkout(tmp_path, "def test_ok(): assert True\n")
    execute = sandbox_module._run
    def changing_run(*args, **kwargs):
        result = execute(*args, **kwargs)
        assert result.returncode == 0 and "1 passed" in result.stdout
        monkeypatch.setattr(sandbox_module, "MAX_JUNIT_CASES", 999)
        return result
    monkeypatch.setattr(sandbox_module, "_run", changing_run)
    result = workspace.run_tests(root, expected_executor=workspace.executor_contract())
    assert not result.available and result.total == 0 and "changed during execution" in result.error


def test_fresh_delivery_refuses_changed_executor(runtime, monkeypatch):
    mission = accepted(runtime)
    packet = runtime.store.evidence_for(mission["id"])
    packet_bytes = Path(packet["path"]).read_bytes()
    drift(monkeypatch)
    monkeypatch.setattr(runtime.sandbox, "run_tests", lambda *a, **kw: pytest.fail("No revalidation on changed executor"))
    delivery = LocalDelivery(runtime.store, runtime.sandbox)
    with pytest.raises(ValueError, match="executor changed"):
        delivery.deliver(mission["id"])
    assert not (delivery.directory / mission["id"]).exists()
    assert Path(packet["path"]).read_bytes() == packet_bytes


def test_waiting_human_checkpoint_cannot_approve_executor_drift(runtime, monkeypatch):
    from test_missions import run_scenario
    mission = run_scenario(runtime, "PD01", patch_runner)
    drift(monkeypatch)
    assert not runtime.engine.approve(mission["id"])
    current = runtime.store.mission(mission["id"])
    assert current["gate_reason_code"] == "CHECKPOINT_CHANGED" and current["human_approval"] is None


def test_legacy_contract_has_no_retroactive_executor_binding_and_can_deliver(runtime, monkeypatch):
    monkeypatch.setattr(runtime.module, "agent_runner", SimpleNamespace(run=patch_runner))
    mission = runtime.engine.launch_mission("PD01", request_id="legacy-executor", defer=True)
    contract = json.loads(mission["contract_json"])
    contract.pop("test_executor")  # Controlled reproduction of a historical admission.
    runtime.store.update_mission(mission["id"], contract_json=json.dumps(contract))
    drift(monkeypatch)
    assert runtime.engine.dispatch_once() and runtime.engine.approve(mission["id"])
    packet = runtime.store.evidence_for(mission["id"])
    assert "test_executor" not in packet["payload"]["payload"]["contract"]
    before = Path(packet["path"]).read_bytes()
    receipt = LocalDelivery(runtime.store, runtime.sandbox).deliver(mission["id"])
    assert receipt["tests"]["passed"] == 19 and "test_executor" not in receipt
    assert Path(packet["path"]).read_bytes() == before
    assert EvidenceWriter.verify(Path(packet["path"]), packet["digest"])["valid"]


def test_missing_pytest_distribution_is_explicit_and_starts_no_process(tmp_path, monkeypatch):
    def missing(name):
        raise test_isolation.PackageNotFoundError(name)
    monkeypatch.setattr(test_isolation, "version", missing)
    monkeypatch.setattr(sandbox_module, "_run", lambda *a, **kw: pytest.fail("No unverified process"))
    root = checkout(tmp_path, "def test_never(): assert True\n")
    result = SandboxManager(tmp_path / "runs").run_tests(root)
    assert not result.available and "requires an installed pytest distribution" in result.error
