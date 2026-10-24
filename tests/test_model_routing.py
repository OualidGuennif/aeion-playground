"""Authorized adaptive choices from controlled session facts; no real model calls."""
import copy
import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.agent_runner import AgentResult
from app.model_routing import ModelRouter, RoutingBlocked, availability_error
from app.operator_settings import load_routing_policy
from test_product_loop import bootstrap, coverage_runner, start_product_task

DS_FREE = "opencode/deepseek-v4.1-flash-free"
GLM_FREE = "opencode/glm-5.3-flash-free"
DS_GO = "opencode-go/deepseek-v4.1-flash"
GLM_GO = "opencode-go/glm-5.3-flash"


def policy(**changes):
    value = copy.deepcopy(load_routing_policy())
    value["authorization_expires_at"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    value.update(changes)
    value.pop("digest", None)
    value["digest"] = "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return value


def enable(runtime, value):
    runtime.monkeypatch.setattr(runtime.module, "load_routing_policy", lambda: copy.deepcopy(value))


def fixture_result(**kwargs):
    reply = coverage_runner(**kwargs)
    reply.run_id = uuid.uuid4().hex
    reply.model = kwargs["model_override"]
    reply.raw = {"finish_reason": "stop", "steps": 1, "tool_calls": 0, "duration_ms": 250,
                 "session_id": "controlled-session", "source_digest": hashlib.sha256(kwargs["source_context"].encode()).hexdigest()}
    return reply


def test_free_refusal_falls_back_once_per_role_and_records_every_cost_and_choice(runtime):
    bootstrap(runtime)
    value = policy()
    enable(runtime, value)
    calls = []
    def provider(**kwargs):
        calls.append((kwargs["agent_id"], kwargs["model_override"]))
        if kwargs["model_override"].endswith("-free"):
            return AgentResult(status="failed", runtime="controlled", run_id=uuid.uuid4().hex,
                               model=kwargs["model_override"], error="Free usage limit reached", cost_usd=.01)
        reply = fixture_result(**kwargs)
        reply.cost_usd = .02
        return reply
    mission = start_product_task(runtime, provider)
    assert calls == [("forge", DS_FREE), ("forge", DS_GO), ("sentinel", GLM_FREE), ("sentinel", GLM_GO)]
    assert mission["status"] == "completed" and mission["human_approval"] is None
    assert mission["cost_usd"] == pytest.approx(.06) and mission["profile"] == DS_GO
    runs = runtime.store.runs(mission["id"])
    assert len(runs) == 4 and all(json.loads(run["raw_json"])["routing"]["policy_digest"] == value["digest"] for run in runs)
    assert runtime.store.get_kv("provider_retry_at") is None  # A free tier cannot block Go globally.
    body = runtime.store.evidence_for(mission["id"])["payload"]["payload"]
    assert body["contract"]["routing"]["digest"] == value["digest"]
    fresh_router = ModelRouter(runtime.store, value)
    scenario = json.loads(mission["contract_json"])["scenario"]
    assert fresh_router.plan(scenario, "forge")["candidates"] == [DS_GO]
    assert fresh_router.plan(scenario, "sentinel", DS_GO)["candidates"] == [GLM_GO]


@pytest.mark.parametrize("error", ["Agent timed out; outcome unknown", "Invalid patch protocol response: bad JSON"])
def test_unknown_outcomes_and_protocol_errors_do_not_trigger_paid_retry(runtime, error):
    bootstrap(runtime)
    enable(runtime, policy())
    calls = []
    def failed(**kwargs):
        calls.append(kwargs["model_override"])
        return AgentResult(status="failed", runtime="controlled", run_id=uuid.uuid4().hex,
                           model=kwargs["model_override"], error=error)
    mission = start_product_task(runtime, failed)
    assert mission["status"] == "blocked" and calls == [DS_FREE]
    assert len(runtime.store.runs(mission["id"])) == 1


def test_budget_is_rechecked_before_the_paid_fallback(runtime):
    bootstrap(runtime)
    enable(runtime, policy(budget_usd=.015))
    calls = []
    def expensive_failure(**kwargs):
        calls.append(kwargs["model_override"])
        return AgentResult(status="failed", runtime="controlled", run_id=uuid.uuid4().hex,
                           model=kwargs["model_override"], error="Rate limit reached", cost_usd=.02)
    mission = start_product_task(runtime, expensive_failure)
    assert calls == [DS_FREE] and mission["gate_reason_code"] == "BUDGET_EXHAUSTED"
    assert mission["cost_usd"] == .02 and len(runtime.store.runs(mission["id"])) == 1


def test_policy_drift_and_expiry_stop_before_any_model_call(runtime):
    bootstrap(runtime)
    current = policy()
    enable(runtime, current)
    mission = runtime.engine.launch_mission("PD04", request_id="routing-drift", defer=True)
    current["digest"] = "changed"
    runtime.monkeypatch.setattr(runtime.module, "agent_runner", SimpleNamespace(run=lambda **kw: pytest.fail("No call")))
    assert runtime.engine.dispatch_once()
    assert runtime.store.mission(mission["id"])["gate_reason_code"] == "ROUTING_CHANGED"
    assert not runtime.store.runs(mission["id"])
    enable(runtime, policy(authorization_expires_at="2000-01-01T00:00:00Z"))
    with pytest.raises(RoutingBlocked, match="expired"):
        runtime.engine.launch_mission("PD04", request_id="expired", defer=True)


def test_role_timeout_is_clamped_to_the_authorization_window(runtime):
    bootstrap(runtime)
    expires = datetime.fromtimestamp(time.time() + 45, timezone.utc).isoformat()
    enable(runtime, policy(authorization_expires_at=expires))
    observed = []
    def provider(**kwargs):
        observed.append(kwargs["timeout_seconds"])
        return fixture_result(**kwargs)
    mission = start_product_task(runtime, provider)
    assert mission["status"] == "completed" and all(0 < limit <= 45 for limit in observed)


def test_quality_feedback_changes_family_for_the_same_task_class_only(runtime):
    value = policy()
    scenario = {"source": "local-product", "risk": "low"}
    records, missions = [], {}
    for i in range(3):
        identity = f"history-{i}"
        missions[identity] = {"contract_json": json.dumps({"scenario": scenario}), "gate_reason_code": "TEST_FAILURE",
                              "tests_json": json.dumps({"total": 20, "passed": 19})}
        records.append({"run_id": identity, "mission_id": identity, "agent": "forge", "model": DS_GO,
                        "status": "completed", "error": None, "cost_usd": .01, "raw_json": "{}"})
    fake = SimpleNamespace(runs=lambda **kw: records, mission=lambda key: missions[key],
                           metrics=lambda: {"cost": 0}, get_kv=runtime.store.get_kv, set_kv=runtime.store.set_kv)
    router = ModelRouter(fake, value)
    assert router.plan(scenario, "forge")["candidates"] == [GLM_FREE, GLM_GO]
    assert router.plan({"risk": "high"}, "forge")["candidates"] == [DS_FREE, DS_GO]
    assert router.plan(scenario, "sentinel", GLM_GO)["candidates"] == [DS_FREE, DS_GO]


def test_trusted_executor_drift_does_not_count_as_model_quality_failure(runtime):
    scenario = {"source": "local-product", "risk": "low"}
    mission = {"contract_json": json.dumps({"scenario": scenario}), "gate_reason_code": "TEST_EXECUTOR_CHANGED",
               "tests_json": json.dumps({"total": 0, "passed": 0})}
    run = {"run_id": "controlled-executor-drift", "mission_id": "history", "agent": "forge", "model": DS_GO,
           "status": "completed", "error": None}
    fake = SimpleNamespace(runs=lambda **kw: [run], mission=lambda key: mission,
                           metrics=lambda: {"cost": 0}, get_kv=runtime.store.get_kv, set_kv=runtime.store.set_kv)
    analysis = ModelRouter(fake, policy()).analyze(scenario, "forge")
    family = analysis["families"]["deepseek"]
    assert family["samples"] == family["passed"] == family["deterministic_failures"] == 0


def test_cooldown_survives_policy_revision_and_older_observation_cannot_replace_success(runtime):
    value = policy()
    router = ModelRouter(runtime.store, value)
    failed = AgentResult(status="failed", runtime="controlled", run_id="quota", error="Free usage limit reached")
    router.observe(DS_FREE, failed)
    newer = policy(history_window=21)
    planned = ModelRouter(runtime.store, newer).plan({"risk": "low"}, "forge")
    assert DS_FREE not in planned["candidates"]
    success = AgentResult(status="completed", runtime="controlled", run_id="accepted")
    router.observe(DS_FREE, success, observed_at=time.time() + 1)
    router.observe(DS_FREE, failed, observed_at=time.time() - 100)
    assert json.loads(runtime.store.get_kv(f"routing:health:{DS_FREE}"))["run_id"] == "accepted"


def test_free_availability_failure_does_not_consume_technical_review_recovery(runtime):
    bootstrap(runtime)
    enable(runtime, policy())
    def initial(**kwargs):
        reply = fixture_result(**kwargs)
        if kwargs.get("review"):
            reply.status = "failed"
            reply.error = ("Free usage limit reached" if kwargs["model_override"].endswith("-free")
                           else "Invalid patch protocol response: controlled prose")
        return reply
    mission = start_product_task(runtime, initial)
    assert mission["status"] == "awaiting_human"
    assert len(runtime.store.runs(mission["id"])) == 3
    assert runtime.engine.retry_review(mission["id"])["status"] == "queued"
    runtime.monkeypatch.setattr(runtime.module, "agent_runner", SimpleNamespace(run=fixture_result))
    assert runtime.engine.dispatch_once()
    final = runtime.store.mission(mission["id"])
    assert final["status"] == "completed" and final["human_approval"] is None
    assert len(runtime.store.runs(mission["id"])) == 4


def test_actual_fallback_author_family_is_excluded_from_review(runtime):
    bootstrap(runtime)
    value = policy()
    enable(runtime, value)
    router = ModelRouter(runtime.store, value)
    router.observe(DS_FREE, AgentResult(status="failed", runtime="controlled", run_id="free-refusal", error="Rate limit"))
    router.observe(DS_GO, AgentResult(status="failed", runtime="controlled", run_id="go-refusal", error="Insufficient account funds"))
    calls = []
    def provider(**kwargs):
        calls.append((kwargs["agent_id"], kwargs["model_override"]))
        return fixture_result(**kwargs)
    mission = start_product_task(runtime, provider)
    assert calls == [("forge", GLM_FREE)]
    assert mission["status"] == "blocked" and mission["gate_reason_code"] == "MODEL_UNAVAILABLE"
    assert len(runtime.store.runs(mission["id"])) == 1


def test_policy_change_between_free_and_paid_is_refused(runtime):
    bootstrap(runtime)
    current = policy()
    enable(runtime, current)
    calls = []
    def refusal(**kwargs):
        calls.append(kwargs["model_override"])
        current["digest"] = "changed"
        return AgentResult(status="failed", runtime="controlled", run_id="free", model=kwargs["model_override"], error="Rate limit")
    mission = start_product_task(runtime, refusal)
    assert calls == [DS_FREE] and mission["gate_reason_code"] == "ROUTING_CHANGED"


@pytest.mark.parametrize("change", [
    {"budget_usd": float("nan")}, {"budget_usd": 101}, {"authorization_expires_at": 42},
    {"authorization_expires_at": "2026-10-25T01:00:00"}, {"default_author": []},
    {"roles": {"forge": {"preferred_family": [], "models": []}, "sentinel": {}}},
])
def test_invalid_owner_policy_fails_closed(tmp_path, monkeypatch, change):
    from app import operator_settings
    value = policy(**change)
    value.pop("digest")
    path = tmp_path / "routing.json"
    path.write_text(json.dumps(value))
    monkeypatch.setattr(operator_settings, "ROUTING_PATH", path)
    with pytest.raises(ValueError):
        operator_settings.load_routing_policy()


@pytest.mark.parametrize("message,classification", [
    ("Free usage limit reached", "rate_limit"),
    ("This Go model requires Global regions", "operator_action"),
    ("Insufficient account funds", "operator_action"),
    ("free tier can only be used from within OpenCode", "access_restriction"),
    ("ModelNotFoundError", "catalog_unavailable"),
    ("Invalid patch protocol response", None),
    ("Agent timed out", None),
])
def test_provider_recovery_categories_are_explicit(message, classification):
    assert availability_error(message) == classification
