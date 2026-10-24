"""The executor boundary is narrow, injectable and used at admission.

These tests do not run a suite: they prove that the controller asks the injected
client for the executor identity instead of reaching for the sandbox global, which is
what makes a remote executor possible without changing the mission flow.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import real_engine
from app.delivery import LocalDelivery
from app.executor import ExecutorClient, InProcessExecutor


class RecordingExecutor:
    def __init__(self, identity: dict[str, Any] | None = None):
        self.identity = identity or {"executor": "recording", "isolation": "none", "limits": {}}
        self.probes: list[float] = []
        self.runs: list[dict[str, Any]] = []

    def probe(self, *, timeout: float = 180) -> dict[str, Any]:
        self.probes.append(timeout)
        return self.identity

    def run_tests(self, path: Path, *, timeout: float = 180,
                  expected_executor: dict[str, Any] | None = None) -> Any:
        self.runs.append({"path": path, "timeout": timeout, "expected": expected_executor})
        raise AssertionError("No test run expected in this test")


def test_in_process_executor_delegates_probe_and_run_tests():
    class Workspace:
        def __init__(self):
            self.calls = []

        def executor_contract(self, *, timeout: float = 180):
            self.calls.append(("probe", timeout))
            return {"executor": "in-process"}

        def run_tests(self, path, *, timeout: float = 180, expected_executor=None):
            self.calls.append(("run", path, timeout, expected_executor))
            return "report"

    workspace = Workspace()
    executor = InProcessExecutor(workspace)
    assert isinstance(executor, ExecutorClient)
    assert executor.probe(timeout=7) == {"executor": "in-process"}
    assert executor.run_tests(Path("/checkout"), timeout=9, expected_executor={"a": 1}) == "report"
    assert workspace.calls == [("probe", 7), ("run", Path("/checkout"), 9, {"a": 1})]


def test_admission_freezes_the_identity_of_the_injected_executor(runtime):
    injected = RecordingExecutor({"executor": "remote-agent", "isolation": "agent", "limits": {"seconds": 60}})
    engine = real_engine.RealEngine(executor=injected)
    mission = engine.launch_mission("PD00", request_id="injected-executor", defer=True)
    contract = json.loads(mission["contract_json"])
    assert contract["test_executor"] == injected.identity
    assert injected.probes == [180]
    assert injected.runs == []
    assert mission["status"] == "queued"


def test_delivery_accepts_an_injected_executor(runtime):
    injected = RecordingExecutor()
    delivery = LocalDelivery(runtime.store, runtime.sandbox, executor=injected)
    assert delivery.executor is injected
    default = LocalDelivery(runtime.store, runtime.sandbox)
    assert isinstance(default.executor, InProcessExecutor)
