"""Controlled accounting records, never a provider/billing attestation."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from app.agent_runner import AgentResult, AgentRunner
from app.patch_protocol import PATCH_PROTOCOL
from app.model_routing import ModelRouter, RoutingBlocked
from app.store import InferenceBudgetError, Store
from test_usage_atomicity import admitted, reported

MODEL = "opencode-go/deepseek-v4.1-flash"
EXECUTOR = {"backend": "docker-opencode/v1", "controlled": True}


def fixture_mission(runtime, budget=100):
    mission = admitted(runtime)
    policy = json.loads((Path(__file__).parents[1] / "config/model-routing.example.json").read_text())
    policy.update(authorization_expires_at="2099-01-01T00:00:00Z", budget_usd=budget, digest="d" * 64)
    contract = json.loads(mission["contract_json"])
    contract["scenario"]["kind"] = "agent"
    contract["routing"] = policy
    contract["runtime"] = {"inference_executor": EXECUTOR}
    runtime.store.update_mission(mission["id"], contract_json=json.dumps(contract))
    return mission, contract


def reserve(runtime, mission, run_id="run-controlled", model=MODEL):
    return runtime.store.reserve_attempt(run_id=run_id, mission_id=mission["id"], agent="forge", model=model, mission_limit=2)


def result(mission, run_id="run-controlled", lease_id="a" * 32, state="completed", model=MODEL, cost=.125):
    raw = {"steps": 1, "tool_calls": 0, "finish_reason": "stop", "session_id": "controlled-session",
           "inference": {"lease_id": lease_id, "run_id": run_id, "state": state, "executor": EXECUTOR,
                         "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                         "response_sha256": "b" * 64}}
    return reported(mission["id"], run_id=run_id, runtime="opencode-cli", model=model, cost_usd=cost, raw=raw)


def test_canonical_result_requires_prior_reservation(runtime):
    mission, _ = fixture_mission(runtime)
    with pytest.raises(ValueError, match="prior budget"):
        runtime.store.add_run(result(mission))
    assert not runtime.store.runs(mission["id"])


def test_precise_usage_and_reservation_release_commit_once(runtime):
    mission, _ = fixture_mission(runtime)
    reserve(runtime, mission)
    runtime.store.bind_attempt_lease("run-controlled", "a" * 32)
    run = result(mission, cost=.123456789)
    assert runtime.store.add_run(run)
    assert not runtime.store.add_run(copy.deepcopy(run))
    assert runtime.store.mission(mission["id"])["cost_usd"] == .123456789
    assert runtime.store.attempt("run-controlled")["state"] == "settled"
    assert runtime.store.budget_snapshot() == {"reported_usd": .123456789, "carried_usd": 0, "held_usd": 0,
                                             "unsettled_attempts": 0, "exposure_usd": .123456789}
    assert reserve(runtime, mission, "run-next")["reserved_usd"] == pytest.approx(2 - .123456789)


def test_settlement_failure_rolls_back_result_cost_and_hold_then_exact_retry(runtime):
    mission, _ = fixture_mission(runtime)
    reserve(runtime, mission)
    runtime.store.bind_attempt_lease("run-controlled", "a" * 32)
    runtime.store._execute("""CREATE TRIGGER reject_settlement BEFORE UPDATE OF state ON inference_attempts
                             BEGIN SELECT RAISE(ABORT,'controlled settlement failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="settlement failure"):
        runtime.store.add_run(result(mission))
    assert not runtime.store.runs(mission["id"])
    assert runtime.store.mission(mission["id"])["cost_usd"] == 0
    assert runtime.store.attempt("run-controlled")["held_usd"] == 2
    assert runtime.store.attempt("run-controlled")["state"] == "reserved"
    runtime.store._execute("DROP TRIGGER reject_settlement")
    assert runtime.store.add_run(result(mission))
    assert not runtime.store.add_run(result(mission))


def test_two_connections_cannot_reserve_concurrent_dispatch(runtime):
    mission, _ = fixture_mission(runtime)
    other = Store(runtime.store.path)
    try:
        def attempt(pair):
            ledger, identity = pair
            try:
                ledger.reserve_attempt(run_id=identity, mission_id=mission["id"], agent="forge", model=MODEL, mission_limit=2)
                return "reserved"
            except InferenceBudgetError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, [(runtime.store, "run-first"), (other, "run-second")]))
        assert sorted(results) == ["EXPOSURE_HELD", "reserved"]
        assert runtime.store.budget_snapshot()["held_usd"] == 2
    finally:
        other._conn.close()


def test_expiry_does_not_authorize_a_dispatch_or_refund_an_existing_hold(runtime):
    mission, contract = fixture_mission(runtime)
    reserve(runtime, mission)
    contract["routing"]["authorization_expires_at"] = "2000-01-01T00:00:00Z"
    runtime.store.update_mission(mission["id"], contract_json=json.dumps(contract))
    with pytest.raises(InferenceBudgetError) as outcome:
        reserve(runtime, mission, "run-expired")
    assert outcome.value.code == "AUTHORIZATION_EXPIRED"
    assert runtime.store.budget_snapshot()["held_usd"] == 2


def test_carried_spend_is_immutable_and_counts_before_new_exposure(runtime):
    assert runtime.store.register_carried_usage(99, "c" * 64)
    assert not runtime.store.register_carried_usage(99, "c" * 64)
    with pytest.raises(ValueError, match="immutable"):
        runtime.store.register_carried_usage(0, "c" * 64)
    mission, _ = fixture_mission(runtime)
    with pytest.raises(InferenceBudgetError) as outcome:
        reserve(runtime, mission)
    assert outcome.value.code == "BUDGET_RESERVATION_DENIED"
    assert runtime.store.budget_snapshot()["exposure_usd"] == 99


@pytest.mark.parametrize("change", ["unknown", "consumed", "usage_mismatch", "lease_mismatch", "executor_mismatch"])
def test_unknown_usage_remains_held_across_restart_and_blocks_new_work(runtime, change):
    mission, contract = fixture_mission(runtime)
    reserve(runtime, mission)
    runtime.store.bind_attempt_lease("run-controlled", "a" * 32)
    run = result(mission, cost=.25)
    mediated = run["raw"]["inference"]
    if change == "usage_mismatch":
        mediated["usage"]["prompt_tokens"] = 99
    elif change == "lease_mismatch":
        mediated["lease_id"] = "f" * 32
    elif change == "executor_mismatch":
        mediated["executor"] = {"changed": True}
    else:
        mediated["state"] = change
    runtime.store.add_run(run)
    reopened = Store(runtime.store.path)
    try:
        assert reopened.attempt("run-controlled")["state"] == "unknown"
        assert reopened.budget_snapshot()["held_usd"] == 1.75
        assert reopened.budget_snapshot()["exposure_usd"] == 2
        with pytest.raises(RoutingBlocked) as outcome:
            ModelRouter(reopened, contract["routing"]).remaining()
        assert outcome.value.code == "EXPOSURE_HELD"
    finally:
        reopened._conn.close()


@pytest.mark.parametrize("mode", ["refused", "revoked", "not_started"])
def test_only_proven_zero_dispatch_or_refusal_releases_without_usage(runtime, mode):
    mission, _ = fixture_mission(runtime)
    reserve(runtime, mission)
    run = result(mission, state=mode, cost=0)
    run.update(status="failed", tokens_in=0, tokens_out=0)
    if mode == "not_started":
        run["raw"] = {"dispatch": "not_started"}
    else:
        runtime.store.bind_attempt_lease("run-controlled", "a" * 32)
        if mode == "refused":
            run["raw"]["inference"]["refusal"] = "HTTP 429: controlled quota refusal"
    runtime.store.add_run(run)
    assert runtime.store.attempt("run-controlled")["state"] == ("refused" if mode == "refused" else "not_dispatched")
    assert runtime.store.budget_snapshot()["unsettled_attempts"] == 0
    assert reserve(runtime, mission, "run-next")["held_usd"] == 2


def test_actual_adapter_readiness_failure_keeps_owned_identity_and_releases_zero_usage(runtime, tmp_path):
    mission, contract = fixture_mission(runtime)
    reserve(runtime, mission)
    runner = AgentRunner()
    runner.log_dir = tmp_path
    runtime.monkeypatch.setattr(runner, "probe", lambda: {"ready": False, "reason": "controlled local backend unavailable"})
    outcome = runner.run(mission_id=mission["id"], agent_id="forge", role="author", title="controlled",
        scenario="controlled", risk="low", task="must not dispatch", cwd=tmp_path, protocol=PATCH_PROTOCOL,
        run_id="run-controlled", model_override=MODEL)
    assert outcome.status == "blocked" and outcome.model == MODEL and outcome.run_id == "run-controlled"
    runtime.store.add_run({"mission_id": mission["id"], "agent": "forge", **outcome.to_dict()})
    assert runtime.store.attempt("run-controlled")["state"] == "not_dispatched"
    ModelRouter(runtime.store, contract["routing"]).observe(MODEL, outcome)
    assert not runtime.store.get_kv("routing:health:" + MODEL)


def test_reported_overrun_is_recorded_and_blocks_all_further_inference(runtime):
    mission, _ = fixture_mission(runtime)
    reserve(runtime, mission)
    runtime.store.bind_attempt_lease("run-controlled", "a" * 32)
    runtime.store.add_run(result(mission, cost=3))
    assert runtime.store.mission(mission["id"])["cost_usd"] == 3
    assert runtime.store.attempt("run-controlled")["state"] == "overrun"
    assert runtime.store.budget_snapshot()["unsettled_attempts"] == 1


def test_recovery_keeps_interrupted_reservation_unknown_and_never_redispatches(runtime):
    mission, contract = fixture_mission(runtime)
    reserve(runtime, mission)
    runtime.engine.recover_interrupted()
    assert runtime.store.mission(mission["id"])["status"] == "blocked"
    assert runtime.store.attempt("run-controlled")["state"] == "unknown"
    assert runtime.store.budget_snapshot()["held_usd"] == 2
    with pytest.raises(RoutingBlocked) as outcome:
        ModelRouter(runtime.store, contract["routing"]).remaining()
    assert outcome.value.code == "EXPOSURE_HELD"


@pytest.mark.parametrize("change", [{"model": "opencode-go/glm-5.3-flash"}, {"agent": "sentinel"}])
def test_foreign_result_cannot_settle_a_reserved_attempt(runtime, change):
    mission, _ = fixture_mission(runtime)
    reserve(runtime, mission)
    run = result(mission)
    run.update(change)
    with pytest.raises(ValueError, match="immutable attempt"):
        runtime.store.add_run(run)
    assert runtime.store.budget_snapshot()["held_usd"] == 2


@pytest.mark.parametrize("unknown", [False, True])
def test_engine_reserves_before_io_and_falls_back_only_after_known_settlement(runtime, unknown):
    mission, contract = fixture_mission(runtime)
    runtime.monkeypatch.setattr(runtime.module, "load_routing_policy", lambda: contract["routing"])
    runtime.monkeypatch.setattr(runtime.engine, "_runtime_contract", lambda: contract["runtime"])
    calls = []
    def run(**kwargs):
        identity, model = kwargs["run_id"], kwargs["model_override"]
        assert runtime.store.attempt(identity)["state"] == "reserved"
        assert runtime.store.budget_snapshot()["held_usd"] > 0
        kwargs["on_lease"](identity[4:])
        calls.append(identity)
        value = result(mission, run_id=identity, lease_id=identity[4:], model=model)
        if model.endswith("-free"):
            value.update(status="failed", cost_usd=0, tokens_in=0, tokens_out=0,
                         error="Free usage limit reached" if not unknown else "controlled lost stream")
            value["raw"]["inference"].update(state="unknown" if unknown else "refused", refusal="HTTP 429: Free usage limit reached")
        return AgentResult(**{key: value[key] for key in ("run_id", "runtime", "model", "status", "cost_usd", "tokens_in", "tokens_out", "raw")},
                           error=value.get("error"))
    runtime.monkeypatch.setattr(runtime.module, "agent_runner", SimpleNamespace(run=run))
    outcome = runtime.engine._call_agent(mission_id=mission["id"], agent_id="forge")
    assert len(calls) == (1 if unknown else 2)
    assert outcome.status == ("blocked" if unknown else "completed")
    assert runtime.store.budget_snapshot()["unsettled_attempts"] == (1 if unknown else 0)
