"""Persistent technical-review recovery; controlled model replies, real worker checks."""
import hashlib
import json
import sqlite3
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from test_product_loop import bootstrap, coverage_runner, start_product_task


def answer(**kwargs):
    reply = coverage_runner(**kwargs)
    reply.run_id = uuid.uuid4().hex
    reply.cost_usd = .02 if kwargs.get("review") else .01
    reply.raw = {"finish_reason": "stop", "steps": 1, "tool_calls": 0,
                 "source_digest": hashlib.sha256(kwargs["source_context"].encode()).hexdigest()}
    return reply


def format_failed(**kwargs):
    reply = answer(**kwargs)
    if kwargs.get("review"):
        reply.status = "failed"
        reply.error = "Invalid patch protocol response: controlled summary before JSON"
        # Old adapter versions could retain a verdict on a failed run.
        reply.verdict = "OK"
    return reply


def checkpoint(runtime):
    bootstrap(runtime)
    mission = start_product_task(runtime, format_failed)
    assert mission["status"] == "awaiting_human"
    assert runtime.store.evidence_for(mission["id"])["review"] == "FAILED"
    return mission


def test_queued_review_retry_survives_restart_reuses_patch_and_keeps_every_attempt(runtime):
    original = checkpoint(runtime)
    original_runs = runtime.store.runs(original["id"])
    assert runtime.engine.retry_review(original["id"])["status"] == "queued"
    assert runtime.engine.retry_review(original["id"])["status"] == "queued"
    runtime.engine.recover_interrupted()  # Queued retry remains queued; no external replay.
    calls = []
    def reviewer(**kwargs):
        assert kwargs.get("review"), "The already-completed author must not run again"
        calls.append(kwargs)
        reply = answer(**kwargs)
        reply.cost_usd = .03
        return reply
    runtime.monkeypatch.setattr(runtime.module, "agent_runner", SimpleNamespace(run=reviewer))
    assert runtime.engine.dispatch_once()
    final = runtime.store.mission(original["id"])
    assert final["status"] == "completed" and final["outcome"] == "autonomous"
    assert final["human_approval"] is None and final["started_at"] == original["started_at"]
    assert final["cost_usd"] == pytest.approx(.06)
    assert len(calls) == 1 and len(runtime.store.runs(final["id"])) == 3
    assert all(run in runtime.store.runs(final["id"]) for run in original_runs)
    assert json.loads(final["tests_json"])["passed"] == 49
    assert runtime.store.evidence_for(final["id"])["review"] == "OK"
    assert json.loads(runtime.store.get_kv(f"delivery:{final['id']}"))["tests"]["passed"] == 49
    assert runtime.engine.retry_review(final["id"])["status"] == "completed"
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["business-risk", "semantic-rejection", "review-already-ok"])
def test_review_retry_cannot_bypass_business_or_semantic_checkpoints(runtime, mode):
    bootstrap(runtime)
    if mode == "business-risk":
        runtime.monkeypatch.setitem(runtime.module.SCENARIOS["PD04"], "risk", "high")
    def initial(**kwargs):
        reply = format_failed(**kwargs)
        if kwargs.get("review") and mode != "business-risk":
            reply.status, reply.error = "completed", None
            if mode == "semantic-rejection":
                reply.verdict = "CHANGES_REQUIRED"
            else:
                reply.model = "opencode/deepseek-review-fixture"  # Same family checkpoint.
        return reply
    mission = start_product_task(runtime, initial)
    with pytest.raises(ValueError, match="eligible"):
        runtime.engine.retry_review(mission["id"])
    assert runtime.store.get_kv(f"review_retry:{mission['id']}") is None
    assert runtime.store.mission(mission["id"])["human_approval"] is None


def test_second_format_failure_consumes_the_retry_instead_of_looping(runtime):
    mission = checkpoint(runtime)
    runtime.engine.retry_review(mission["id"])
    runtime.monkeypatch.setattr(runtime.module, "agent_runner", SimpleNamespace(run=format_failed))
    assert runtime.engine.dispatch_once()
    assert runtime.store.mission(mission["id"])["status"] == "awaiting_human"
    with pytest.raises(ValueError, match="consumed"):
        runtime.engine.retry_review(mission["id"])
    assert len(runtime.store.runs(mission["id"])) == 3


def test_changed_retry_patch_is_refused_before_tests_or_inference(runtime):
    mission = checkpoint(runtime)
    runtime.engine.retry_review(mission["id"])
    (runtime.sandbox.runs_dir / mission["id"] / "tests/test_boundaries_extended.py").write_text("# changed\n")
    runtime.monkeypatch.setattr(runtime.module, "agent_runner", SimpleNamespace(run=lambda **kw: pytest.fail("No model call")))
    assert runtime.engine.dispatch_once()
    assert runtime.store.mission(mission["id"])["gate_reason_code"] == "REVIEW_RETRY_CHANGED"
    assert len(runtime.store.runs(mission["id"])) == 2


def test_review_retry_intent_and_queue_transition_are_atomic(runtime):
    mission = checkpoint(runtime)
    runtime.store._execute("CREATE TRIGGER refuse_retry BEFORE INSERT ON kv WHEN NEW.key LIKE 'review_retry:%' BEGIN SELECT RAISE(ABORT, 'disk-like failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="disk-like failure"):
        runtime.engine.retry_review(mission["id"])
    assert runtime.store.mission(mission["id"])["status"] == "awaiting_human"
    assert runtime.store.get_kv(f"review_retry:{mission['id']}") is None


def test_interrupted_review_retry_is_unknown_and_never_automatically_replayed(runtime):
    mission = checkpoint(runtime)
    runtime.engine.retry_review(mission["id"])
    assert runtime.store.claim_mission(mission["id"], 1)
    runtime.monkeypatch.setattr(runtime.module, "agent_runner", SimpleNamespace(run=lambda **kw: pytest.fail("No replay")))
    runtime.engine.recover_interrupted()
    assert runtime.store.mission(mission["id"])["gate_reason_code"] == "EXECUTION_INTERRUPTED"
    assert not runtime.engine.dispatch_once()


def test_http_retry_is_idempotent_and_preserves_origin_guard(runtime):
    mission = checkpoint(runtime)
    with TestClient(runtime.main.app, base_url="http://127.0.0.1") as client:
        endpoint = f"/api/missions/{mission['id']}/review/retry"
        assert client.post(endpoint, headers={"Origin": "https://foreign.test"}).status_code == 403
        first, replay = client.post(endpoint), client.post(endpoint)
        assert first.status_code == replay.status_code == 200
        assert first.json()["mission"]["id"] == replay.json()["mission"]["id"] == mission["id"]
