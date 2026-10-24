"""Crash boundaries use durable SQLite and real atomic file/process operations.

Agent replies remain controlled fixtures; no provider inference runs in this suite.
"""
import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.evidence import EvidenceWriter
from app.store import Store
from test_missions import patch_runner, run_scenario


class ProcessCrash(BaseException):
    """Simulate death without triggering the worker's normal exception handler."""


def checkpoint(runtime):
    mission = run_scenario(runtime, "PD01", patch_runner)
    assert mission["status"] == "awaiting_human"
    return mission["id"]


def reopen_and_recover(runtime, monkeypatch):
    reopened = Store(runtime.store.path)
    monkeypatch.setattr(runtime.module, "store", reopened)
    engine = runtime.module.RealEngine()
    engine.recover_interrupted()
    return reopened, engine


def test_publication_failure_preserves_intent_and_never_reports_completion(runtime, monkeypatch):
    mission_id = checkpoint(runtime)
    writer = runtime.module.evidence_writer
    draft = Path(runtime.store.evidence_for(mission_id)["path"])
    original = draft.read_bytes()
    atomic_write = writer._atomic_write
    def fail_json(path, content):
        assert runtime.store.mission(mission_id)["status"] == "running"
        if path.suffix == ".json":
            raise OSError("test disk unavailable")
        atomic_write(path, content)
    with monkeypatch.context() as patch:
        patch.setattr(writer, "_atomic_write", fail_json)
        assert runtime.engine.approve(mission_id)  # Authority is recorded, publication is pending.
    pending = runtime.store.mission(mission_id)
    assert pending["human_approval"] == "operator"
    assert pending["status"] == "running" and pending["outcome"] is None
    assert pending["current_step"] == "Seal pending"
    assert "disk unavailable" in pending["blocked_reason"]
    assert draft.read_bytes() == original
    assert runtime.store.evidence_for(mission_id)["status"] == "draft"
    assert runtime.store.pending_seal(mission_id)["status"] == "sealed"
    reopened, engine = reopen_and_recover(runtime, monkeypatch)
    try:
        assert reopened.mission(mission_id)["status"] == "completed"
        assert reopened.mission(mission_id)["outcome"] == "assisted"
        assert reopened.pending_seal(mission_id) is None
        packet = reopened.evidence_for(mission_id)
        assert packet["status"] == "sealed"
        assert EvidenceWriter.verify(Path(packet["path"]), packet["digest"])["valid"]
        events = reopened.count_events()
        original = draft.read_bytes()
        engine.recover_interrupted()
        assert reopened.count_events() == events
        assert draft.read_bytes() == original
    finally:
        reopened._conn.close()


def test_database_abort_rolls_back_completion_evidence_and_step_together(runtime, monkeypatch):
    mission_id = checkpoint(runtime)
    runtime.store._conn.execute("""CREATE TRIGGER fail_completion BEFORE UPDATE OF status ON missions
        WHEN NEW.status = 'completed' BEGIN SELECT RAISE(ABORT, 'test transaction interruption'); END""")
    runtime.store._conn.commit()
    assert runtime.engine.approve(mission_id)
    assert runtime.store.mission(mission_id)["status"] == "running"
    assert runtime.store.evidence_for(mission_id)["status"] == "draft"
    assert next(step for step in runtime.store.steps(mission_id) if step["idx"] == 6)["status"] == "running"
    packet = runtime.store.pending_seal(mission_id)
    path = Path(packet["path"])
    original = path.read_bytes()
    assert EvidenceWriter.verify(path)["valid"]
    assert json.loads(original)["sealed"]  # Disk publication happened, ledger acceptance did not.
    runtime.store._conn.execute("DROP TRIGGER fail_completion")
    runtime.store._conn.commit()
    reopened, _ = reopen_and_recover(runtime, monkeypatch)
    try:
        assert reopened.mission(mission_id)["status"] == "completed"
        assert reopened.evidence_for(mission_id)["status"] == "sealed"
        assert next(step for step in reopened.steps(mission_id) if step["idx"] == 6)["status"] == "done"
        assert path.read_bytes() == original  # Recovery indexes the exact original seal.
    finally:
        reopened._conn.close()


@pytest.mark.parametrize("publish_before_death", [False, True])
def test_killed_sealing_process_resumes_only_the_prepared_local_commit(runtime, monkeypatch, tmp_path, publish_before_death):
    mission_id = checkpoint(runtime)
    with monkeypatch.context() as patch:
        patch.setattr(runtime.module.evidence_writer, "publish", lambda packet: (_ for _ in ()).throw(ProcessCrash()))
        with pytest.raises(ProcessCrash):
            runtime.engine.approve(mission_id)
    assert runtime.store.pending_seal(mission_id)
    ready = tmp_path / "seal-worker-ready"
    child_code = """
import sys,time
from pathlib import Path
from app.evidence import EvidenceWriter
from app.store import Store
ledger=Store(Path(sys.argv[1]))
with ledger.mission_lock(sys.argv[2]):
    packet=ledger.pending_seal(sys.argv[2])
    if sys.argv[4]=='publish':
        EvidenceWriter(Path(packet['path']).parent).publish(packet)
    Path(sys.argv[3]).touch()
    time.sleep(30)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", child_code, str(runtime.store.path), mission_id, str(ready), "publish" if publish_before_death else "wait"],
        env={**os.environ, "AEION_DB_PATH": str(tmp_path / "child-bootstrap.db")},
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        runtime.engine.recover_interrupted()
        assert runtime.store.mission(mission_id)["status"] == "running"  # Live lock is respected.
        child.kill()
        child.wait(timeout=5)
        reopened, engine = reopen_and_recover(runtime, monkeypatch)
        try:
            mission = reopened.mission(mission_id)
            assert mission["status"] == "completed" and mission["human_approval"] == "operator"
            assert reopened.pending_seal(mission_id) is None
            assert len(reopened.runs(mission_id)) == 2  # No author or review replay.
            packet = reopened.evidence_for(mission_id)
            assert EvidenceWriter.verify(Path(packet["path"]), packet["digest"])["valid"]
            assert not engine.dispatch_once()
        finally:
            reopened._conn.close()
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_late_projection_error_cannot_revoke_committed_acceptance(runtime, monkeypatch):
    mission_id = checkpoint(runtime)
    complete = runtime.store.complete_mission
    def fail_after_commit(packet):
        complete(packet)
        raise RuntimeError("test post-commit projection failure")
    monkeypatch.setattr(runtime.store, "complete_mission", fail_after_commit)
    assert runtime.engine.approve(mission_id)
    mission = runtime.store.mission(mission_id)
    packet = runtime.store.evidence_for(mission_id)
    original = Path(packet["path"]).read_bytes()
    assert mission["status"] == "completed" and packet["status"] == "sealed"
    assert runtime.store.pending_seal(mission_id) is None
    runtime.engine._block(mission_id, "LATE_ERROR", "must not overwrite acceptance")
    runtime.engine._finish_blocked(mission_id, "must not overwrite acceptance", "LATE_ERROR")
    runtime.engine._seal(mission_id, outcome="assisted")
    assert runtime.store.mission(mission_id) == mission
    assert Path(packet["path"]).read_bytes() == original
    different = copy.deepcopy(packet["payload"])
    different["status"] = "draft"
    with pytest.raises(ValueError, match="immutable"):
        runtime.store.add_evidence(different)
    body = different["payload"]
    body["mission"]["title"] = "must not replace original"
    unsigned = {key: value for key, value in body.items() if key != "digest"}
    body["digest"] = "sha256:" + hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    different["digest"] = body["digest"]
    with pytest.raises(ValueError, match="immutable"):
        runtime.module.evidence_writer.publish(different)
    assert Path(packet["path"]).read_bytes() == original


def test_corrupted_seal_is_preserved_and_never_completed_on_recovery(runtime, monkeypatch):
    mission_id = checkpoint(runtime)
    with monkeypatch.context() as patch:
        patch.setattr(runtime.store, "complete_mission", lambda packet: (_ for _ in ()).throw(sqlite3.OperationalError("test interruption")))
        assert runtime.engine.approve(mission_id)
    packet = runtime.store.pending_seal(mission_id)
    path = Path(packet["path"])
    path.write_text('{"sealed": true, "corrupted": true}')
    corrupted = path.read_bytes()
    reopened, _ = reopen_and_recover(runtime, monkeypatch)
    try:
        mission = reopened.mission(mission_id)
        assert mission["status"] == "running" and mission["current_step"] == "Seal pending"
        assert "immutable" in mission["blocked_reason"]
        assert reopened.evidence_for(mission_id)["status"] == "draft"
        assert path.read_bytes() == corrupted
        assert not EvidenceWriter.verify(path)["valid"]
    finally:
        reopened._conn.close()


def test_seal_requires_recorded_acceptance_and_cannot_invent_human_approval(runtime):
    mission_id = checkpoint(runtime)
    with pytest.raises(ValueError, match="recorded Gate"):
        runtime.engine._seal(mission_id, outcome="assisted")
    assert runtime.store.mission(mission_id)["human_approval"] is None
    assert runtime.store.pending_seal(mission_id) is None


@pytest.mark.parametrize("content", [None, "not-json", "[]", "null"])
def test_invalid_or_missing_packet_verification_is_explicit(tmp_path, content):
    path = tmp_path / "packet.json"
    if content is not None:
        path.write_text(content)
    assert EvidenceWriter.verify(path)["valid"] is False


def test_verification_api_binds_the_file_to_the_ledger_digest(runtime):
    mission_id = checkpoint(runtime)
    assert runtime.engine.approve(mission_id)
    packet = runtime.store.evidence_for(mission_id)
    path = Path(packet["path"])
    tampered = json.loads(path.read_text())
    tampered["mission"]["title"] = "tampered and rehashed"
    unsigned = {key: value for key, value in tampered.items() if key != "digest"}
    tampered["digest"] = "sha256:" + hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    path.write_text(json.dumps(tampered))
    assert EvidenceWriter.verify(path)["valid"]  # Self-consistency alone is insufficient.
    with TestClient(runtime.main.app, base_url="http://127.0.0.1") as client:
        result = client.get(f"/api/evidence/{packet['id']}/verify")
    assert result.status_code == 200
    assert result.json()["valid"] is False
