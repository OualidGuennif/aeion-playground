"""Persistent backlog tests use real SQLite, git, pytest and worker locks.

Model replies are controlled test fixtures, never claims of provider execution.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agent_runner import AgentResult
from app.store import SCHEMA, Store


def enqueue(runtime, fixture="PD00", request_id="backlog-1"):
    return runtime.engine.launch_mission(fixture, request_id=request_id, defer=True)


def test_retrying_submission_returns_original_mission_without_duplicate_events(runtime):
    mission = enqueue(runtime)
    before = runtime.store.count_events()
    assert enqueue(runtime)["id"] == mission["id"]
    assert runtime.store.count_events() == before
    assert runtime.store.count_missions() == 1
    assert runtime.store.mission(mission["id"])["contract_json"]
    with pytest.raises(ValueError, match="another task"):
        enqueue(runtime, "PD03")


def test_local_patch_request_id_cannot_be_reused_for_another_patch(runtime):
    runtime.engine.launch_mission("PD01", local_patch="first", request_id="patch", defer=True)
    with pytest.raises(ValueError, match="another task"):
        runtime.engine.launch_mission("PD01", local_patch="different", request_id="patch", defer=True)


def test_queue_api_requires_a_valid_id_and_returns_a_durable_backlog(runtime):
    with TestClient(runtime.main.app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/missions/queue", json={"fixture": "PD00"}).status_code == 422
        assert client.post("/api/missions/queue", json={"request_id": "bad/id"}).status_code == 422
        payload = {"fixture": "PD00", "request_id": "night:baseline"}
        first = client.post("/api/missions/queue", json=payload).json()["mission"]
        assert client.post("/api/missions/queue", json=payload).json()["mission"]["id"] == first["id"]
        assert client.get("/api/missions/queue").json()["missions"][0]["id"] == first["id"]
        assert client.get("/api/missions/" + first["id"]).json()["mission"]["request_id"] == payload["request_id"]
        assert client.get("/api/missions/missing").status_code == 404
        assert client.post("/api/missions/queue", json={**payload, "fixture": "PD03"}).status_code == 422
        assert client.post("/api/missions", json={"fixture": "PD00"}).status_code == 409


def test_pending_backlog_has_a_bound(runtime, monkeypatch):
    monkeypatch.setattr(runtime.module, "MAX_QUEUED_MISSIONS", 2)
    enqueue(runtime, request_id="one")
    enqueue(runtime, request_id="two")
    with pytest.raises(RuntimeError, match="queue is full"):
        enqueue(runtime, request_id="three")
    assert enqueue(runtime, request_id="one")["request_id"] == "one"


def test_new_engine_drains_persisted_work_without_repeating_it(runtime, monkeypatch):
    one = enqueue(runtime)
    two = enqueue(runtime, "PD03", "protected")
    reopened = Store(runtime.store.path)
    monkeypatch.setattr(runtime.module, "store", reopened)
    engine = runtime.module.RealEngine()
    try:
        assert engine.dispatch_once()
        assert reopened.mission(one["id"])["gate_reason_code"] == "NO_PATCH"
        assert engine.dispatch_once()
        assert reopened.mission(two["id"])["gate_reason_code"] == "PROTECTED_PATH"
        packet = reopened.evidence_for(two["id"])
        assert packet["payload"]["payload"]["contract"]["request_id"] == "protected"
        assert not engine.dispatch_once()
        assert not engine._execute_mission(two["id"])
        assert reopened.count_missions() == 2
    finally:
        reopened._conn.close()


def test_claims_across_connections_enforce_single_worker_capacity(runtime):
    one = enqueue(runtime)
    two = enqueue(runtime, request_id="two")
    other = Store(runtime.store.path)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = [pool.submit(ledger.claim_mission, mission["id"], 1)
                      for ledger, mission in [(runtime.store, one), (other, two)]]
            assert sorted(item.result() for item in claims) == [False, True]
        assert len(runtime.store.running_missions()) == 1
        running = runtime.store.running_missions()[0]
        assert not other.claim_mission(running["id"], 2)
    finally:
        other._conn.close()


def test_concurrent_admission_deduplicates_identity(runtime):
    original = enqueue(runtime)
    record = {**original, "request_id": "same-external-request"}
    other = Store(runtime.store.path)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            admissions = [pool.submit(ledger.admit_mission, record, defer=True, capacity=1, queue_limit=20)
                          for ledger in [runtime.store, other]]
            outcomes = [item.result() for item in admissions]
        assert sum(created for _, created in outcomes) == 1
        assert len({mission["id"] for mission, _ in outcomes}) == 1
        assert runtime.store.count_missions() == 2
    finally:
        other._conn.close()


def test_recovery_distinguishes_live_worker_from_killed_process(runtime, tmp_path):
    mission = enqueue(runtime)
    ready = tmp_path / "worker-ready"
    code = """
import sys,time
from pathlib import Path
from app.store import Store
store=Store(Path(sys.argv[1]))
with store.mission_lock(sys.argv[2]):
    assert store.claim_mission(sys.argv[2],1)
    Path(sys.argv[3]).touch()
    time.sleep(30)
"""
    child = subprocess.Popen([sys.executable, "-c", code, str(runtime.store.path), mission["id"], str(ready)],
                             env={**os.environ, "AEION_DB_PATH": str(tmp_path / "child-bootstrap.db")})
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        runtime.engine.recover_interrupted()
        assert runtime.store.mission(mission["id"])["status"] == "running"
        child.kill()
        child.wait(timeout=5)
        runtime.engine.recover_interrupted()
        recovered = runtime.store.mission(mission["id"])
        assert recovered["gate_reason_code"] == "EXECUTION_INTERRUPTED"
        assert recovered["outcome"] == "unknown"
        assert runtime.store.evidence_for(mission["id"])["status"] == "draft"
        events = runtime.store.count_events()
        runtime.engine.recover_interrupted()
        assert runtime.store.count_events() == events
        assert not runtime.engine.dispatch_once()
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_waiting_human_checkpoint_pauses_backlog_and_survives_recovery(runtime):
    checkpoint = enqueue(runtime)
    runtime.store.update_mission(checkpoint["id"], status="awaiting_human", gate_reason_code="HUMAN_CHECKPOINT")
    queued = enqueue(runtime, "PD03", "later")
    runtime.engine.recover_interrupted()
    assert not runtime.engine.dispatch_once()
    assert runtime.store.mission(checkpoint["id"])["status"] == "awaiting_human"
    assert runtime.store.mission(queued["id"])["status"] == "queued"
    assert not (runtime.sandbox.runs_dir / queued["id"]).exists()


@pytest.mark.parametrize("contract", [None, "not-json", "[]", '{"scenario":[]}'])
def test_missing_or_malformed_queue_contract_blocks_without_model_call(runtime, contract):
    mission = enqueue(runtime)
    runtime.store.update_mission(mission["id"], contract_json=contract)
    assert runtime.engine.dispatch_once()
    assert runtime.store.mission(mission["id"])["gate_reason_code"] == "CONTRACT_CHANGED"
    assert not runtime.store.runs(mission["id"])


def test_template_drift_is_rejected_before_execution(runtime, tmp_path, monkeypatch):
    template = tmp_path / "trusted-template"
    shutil.copytree(runtime.sandbox.template_dir, template)
    monkeypatch.setattr(runtime.sandbox, "template_dir", template)
    mission = enqueue(runtime)
    source = template / "app/pricing.py"
    source.write_text(source.read_text() + "\n# new trusted version\n")
    assert runtime.engine.dispatch_once()
    assert runtime.store.mission(mission["id"])["gate_reason_code"] == "CONTRACT_CHANGED"
    assert not (runtime.sandbox.runs_dir / mission["id"]).exists()


@pytest.mark.parametrize("name", [".env", "secrets/token.txt"])
def test_private_template_files_are_rejected_before_admission(runtime, tmp_path, monkeypatch, name):
    template = tmp_path / "trusted-template"
    shutil.copytree(runtime.sandbox.template_dir, template)
    monkeypatch.setattr(runtime.sandbox, "template_dir", template)
    private = template / name
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text("test-only placeholder")
    with pytest.raises(RuntimeError, match="Private files"):
        enqueue(runtime)
    assert runtime.store.count_missions() == 0


def test_model_configuration_drift_requires_new_explicit_admission(runtime, monkeypatch):
    mission = enqueue(runtime, "PD01")
    monkeypatch.setattr(runtime.module, "settings", replace(runtime.module.settings, opencode_model="changed/model"))
    assert runtime.engine.dispatch_once()
    assert runtime.store.mission(mission["id"])["gate_reason_code"] == "RUNTIME_CHANGED"
    assert not runtime.store.runs(mission["id"])


def test_exhausted_recorded_budget_prevents_provider_call(runtime, monkeypatch):
    mission = enqueue(runtime, "PD01")
    monkeypatch.setattr(runtime.module, "settings", replace(runtime.module.settings, budget_usd=0))
    assert runtime.engine.dispatch_once()
    assert runtime.store.mission(mission["id"])["gate_reason_code"] == "BUDGET_EXHAUSTED"
    assert not runtime.store.runs(mission["id"])


def test_provider_cooldown_holds_agent_work_but_allows_offline_checks(runtime, monkeypatch):
    calls = []
    def blocked(**kwargs):
        calls.append(kwargs["mission_id"])
        return AgentResult(status="failed", runtime="test", run_id="quota-fixture", error="Free usage limit reached")
    monkeypatch.setattr(runtime.module, "agent_runner", SimpleNamespace(run=blocked))
    first = enqueue(runtime, "PD01")
    second = enqueue(runtime, "PD02", "after-cooldown")
    drill = enqueue(runtime, "PD03", "offline-check")
    assert runtime.engine.dispatch_once()
    assert runtime.engine.dispatch_once()
    assert calls == [first["id"]]
    assert runtime.store.mission(second["id"])["current_step"] == "Provider cooldown"
    assert runtime.store.mission(drill["id"])["gate_reason_code"] == "PROTECTED_PATH"
    assert not runtime.engine.dispatch_once()


def test_existing_checkout_is_never_overwritten(runtime):
    run = runtime.sandbox.create_run("PD-001")
    file = Path(run["path"]) / "keep.txt"
    file.write_text("Preserve interrupted work")
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        runtime.sandbox.create_run("PD-001")
    assert file.read_text() == "Preserve interrupted work"


def test_canonical_tests_do_not_inherit_provider_or_unrelated_workspace_tokens(runtime, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "test-only-provider-token")
    monkeypatch.setenv("AEION_TEST_SENTINEL", "test-only-workspace-token")
    run = runtime.sandbox.create_run("PD-001")
    path = Path(run["path"])
    (path / "tests/test_environment.py").write_text(
        'import os\n'
        'def test_environment_is_filtered():\n'
        '    assert "OPENCODE_API_KEY" not in os.environ\n'
        '    assert "AEION_TEST_SENTINEL" not in os.environ\n'
    )
    result = runtime.sandbox.run_tests(path)
    case = next(case for case in result.cases if case["name"] == "test_environment_is_filtered")
    assert case["status"] == "passed"
    assert result.total == 20
    assert (result.passed, result.failed) == (13, 7)  # Original failing baseline is preserved.


def test_legacy_ledger_migration_preserves_original_rows(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    legacy = SCHEMA.replace(",\n    request_id TEXT,\n    contract_json TEXT", "")
    connection.executescript(legacy)
    connection.execute("""INSERT INTO missions (id,scenario,fixture,title,subtitle,risk,priority,profile,status,created_at)
                          VALUES ('PD-005','agent','PD01','Original','kept','high','high','original','completed','2026-10-23')""")
    connection.commit()
    connection.close()
    ledger = Store(path)
    try:
        original = ledger.mission("PD-005")
        assert original["title"] == "Original"
        assert original["status"] == "completed"
        assert original["request_id"] is None and original["contract_json"] is None
    finally:
        ledger._conn.close()
