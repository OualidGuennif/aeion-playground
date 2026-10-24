"""Known provider usage and immutable run identity share a SQLite transaction."""
from concurrent.futures import ThreadPoolExecutor
import copy
import sqlite3

import pytest

from app.store import Store


def admitted(runtime):
    mission = runtime.engine.launch_mission("PD00", request_id="usage", defer=True)
    assert runtime.store.claim_mission(mission["id"], 1)
    return mission


def reported(mission_identity, **changes):
    value = {"run_id": "controlled-run", "mission_id": mission_identity, "agent": "forge", "runtime": "controlled",
             "model": "controlled-deepseek", "status": "completed", "cost_usd": .123456789,
             "tokens_in": 7, "tokens_out": 3, "raw": {"session_id": "controlled-session"}}
    value.update(changes)
    return value


def test_failure_between_run_and_usage_updates_rolls_back_both_then_retries_once(runtime):
    mission = admitted(runtime)
    runtime.store._execute("""CREATE TRIGGER reject_usage BEFORE UPDATE OF cost_usd ON missions
                             BEGIN SELECT RAISE(ABORT, 'controlled storage failure'); END""")
    run = reported(mission["id"])
    with pytest.raises(sqlite3.IntegrityError, match="controlled storage failure"):
        runtime.store.add_run(run)
    assert not runtime.store.runs(mission["id"])
    assert runtime.store.mission(mission["id"])["cost_usd"] == 0
    runtime.store._execute("DROP TRIGGER reject_usage")
    assert runtime.store.add_run(run)
    assert not runtime.store.add_run(copy.deepcopy(run))
    current = runtime.store.mission(mission["id"])
    assert current["cost_usd"] == .123456789 and current["tokens_in"] == 7 and current["tokens_out"] == 3
    assert runtime.store.runs(mission["id"])[0]["cost_usd"] == .123456789


def test_two_connections_cannot_double_settle_the_same_run(runtime):
    mission = admitted(runtime)
    other = Store(runtime.store.path)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda ledger: ledger.add_run(reported(mission["id"])), (runtime.store, other)))
        assert sorted(results) == [False, True]
        assert runtime.store.mission(mission["id"])["cost_usd"] == .123456789
        assert len(runtime.store.runs(mission["id"])) == 1
    finally:
        other._conn.close()


@pytest.mark.parametrize("change", [{"cost_usd": .3}, {"model": "other"}, {"mission_id": "another-mission"}, {"raw": {"changed": True}}])
def test_conflicting_run_identity_cannot_replace_history_or_move_usage(runtime, change):
    mission = admitted(runtime)
    runtime.store.add_run(reported(mission["id"]))
    before = runtime.store.runs(mission["id"])
    with pytest.raises(ValueError, match="different immutable result"):
        runtime.store.add_run(reported(mission["id"], **change))
    assert runtime.store.runs(mission["id"]) == before
    assert runtime.store.mission(mission["id"])["cost_usd"] == .123456789


def test_completed_mission_accepts_exact_replay_but_no_new_usage(runtime):
    mission = admitted(runtime)
    runtime.store.add_run(reported(mission["id"]))
    runtime.store.update_mission(mission["id"], status="completed")
    assert not runtime.store.add_run(reported(mission["id"]))
    with pytest.raises(ValueError, match="running mission"):
        runtime.store.add_run(reported(mission["id"], run_id="new-result"))
    assert len(runtime.store.runs(mission["id"])) == 1


@pytest.mark.parametrize("change", [
    {"cost_usd": float("nan")}, {"cost_usd": float("inf")}, {"cost_usd": -1}, {"cost_usd": True},
    {"tokens_in": -1}, {"tokens_out": 1.5}, {"tokens_out": True},
    {"run_id": None}, {"run_id": ""},
])
def test_invalid_reported_usage_never_changes_totals(runtime, change):
    mission = admitted(runtime)
    with pytest.raises(ValueError):
        runtime.store.add_run(reported(mission["id"], **change))
    assert not runtime.store.runs(mission["id"])
    assert runtime.store.mission(mission["id"])["cost_usd"] == 0
