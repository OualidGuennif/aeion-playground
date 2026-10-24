"""User-defined coverage tasks share the existing ledger, worker and policy.

Real Git/pytest/delivery run locally; model answers are controlled fixtures.
"""
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from test_missions import result
from test_product_loop import bootstrap, start_product_task, proposal


TASK = {
    "request_id": "tariff-invariants-001",
    "title": "Tariff invariant regression coverage",
    "task": "Add exhaustive regression tests for pricing monotonicity and the 300-cent increase for each additional kilogram. Preserve implementation and existing tests.",
    "test_file": "tests/test_tariff_invariants.py",
    "minimum_new_tests": 2,
}


def invariant_runner(**kwargs):
    if kwargs.get("review"):
        assert "monotonicity" in kwargs["task"]
        return result(uuid.uuid4().hex, model="opencode/glm-review-fixture", verdict="OK", reason="Two exhaustive invariants; only the admitted new test file changes.")
    text = (
        'from app.pricing import quote\n'
        'def test_monotonicity():\n'
        '    assert all(quote(g) <= quote(g+1) for g in range(1, 30000))\n'
        'def test_one_more_kilogram():\n'
        '    assert all(quote(g+1000) - quote(g) == 300 for g in range(1, 29001))\n'
    )
    if kwargs.get("protocol"):
        reply = proposal(TASK["test_file"], text)
        reply.run_id = uuid.uuid4().hex
        return reply
    (kwargs["cwd"] / TASK["test_file"]).write_text(text)
    return result(uuid.uuid4().hex, model="opencode/deepseek-author-fixture")


def test_defined_task_runs_the_full_local_loop_without_adding_a_catalog_scenario(runtime):
    source = bootstrap(runtime)
    runtime.monkeypatch.setattr(runtime.module, "agent_runner", SimpleNamespace(run=invariant_runner))
    with TestClient(runtime.main.app, base_url="http://127.0.0.1") as client:
        admitted = client.post("/api/tasks/tests", json=TASK)
        assert admitted.status_code == 200
        mission = admitted.json()["mission"]
        assert mission["fixture"] == "TASK" and mission["status"] == "queued"
        contract = json.loads(mission["contract_json"])
        assert contract["request"] == {"type": "product-test-task", **{k: v for k, v in TASK.items() if k != "request_id"}}
        assert contract["scenario"]["allowed_paths"] == [TASK["test_file"]]
        assert contract["scenario"]["minimum_tests"] == 21
        assert contract["source_release"]["commit"] == source["commit"]
        assert runtime.engine.dispatch_once()
        payload = client.get(f"/api/missions/{mission['id']}").json()
        assert payload["mission"]["status"] == "completed"
        assert payload["mission"]["outcome"] == "autonomous"
        assert payload["mission"]["human_approval"] is None
        assert json.loads(payload["mission"]["tests_json"])["passed"] == 21
        assert payload["delivery"]["tests"]["passed"] == 21 and payload["delivery_error"] is None
        assert Path(payload["delivery"]["path"]).is_dir()
        assert len(payload["runs"]) == 2
        assert client.post("/api/tasks/tests", json=TASK).json()["mission"]["id"] == mission["id"]
        assert set(runtime.module.SCENARIOS) == {"PD00", "PD01", "PD02", "PD03", "PD04"}


@pytest.mark.parametrize("changes", [
    {"test_file": "app/pricing.py"}, {"test_file": "tests/../app/pricing.py"},
    {"test_file": "/tmp/test_escape.py"}, {"test_file": "tests/subdir/test_escape.py"},
    {"test_file": "tests/test_new.py\n"}, {"minimum_new_tests": 0},
    {"minimum_new_tests": 101}, {"minimum_new_tests": True}, {"minimum_new_tests": "2"},
    {"risk": "low"}, {"source": "/tmp/repo"}, {"policy": {}},
    {"minimum_tests": 0}, {"task": " " * 25}, {"title": "    "},
    {"request_id": "bad/id"},
])
def test_task_input_cannot_widen_authority_or_escape_bounds(runtime, changes):
    with TestClient(runtime.main.app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/tasks/tests", json={**TASK, **changes})
    assert response.status_code == 422
    assert runtime.store.count_missions() == 0


def test_a_task_requires_an_accepted_product_and_a_new_test_file(runtime):
    with TestClient(runtime.main.app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/tasks/tests", json=TASK).status_code == 409
        bootstrap(runtime)
        response = client.post("/api/tasks/tests", json={**TASK, "test_file": "tests/test_pricing.py"})
    assert response.status_code == 422
    assert "new test file" in response.json()["detail"]
    assert runtime.store.count_missions() == 1


@pytest.mark.parametrize("changes", [
    {"title": "A different task title"}, {"task": TASK["task"] + " A changed requirement."},
    {"test_file": "tests/test_other.py"}, {"minimum_new_tests": 3},
])
def test_request_id_binds_every_supplied_task_field(runtime, changes):
    bootstrap(runtime)
    with TestClient(runtime.main.app, base_url="http://127.0.0.1") as client:
        first = client.post("/api/tasks/tests", json=TASK)
        events = runtime.store.count_events()
        response = client.post("/api/tasks/tests", json={**TASK, **changes})
        assert first.status_code == 200 and response.status_code == 422
        assert "another task" in response.json()["detail"]
        assert runtime.store.count_missions() == 2
        assert runtime.store.count_events() == events


def test_replay_precedes_product_dependency_reads_and_capacity_checks(runtime, monkeypatch):
    bootstrap(runtime)
    mission = runtime.engine.enqueue_test_task(**TASK)
    before = runtime.store.count_events()
    monkeypatch.setattr(runtime.module.LocalDelivery, "verify", lambda *args: pytest.fail("Replay must not resolve the current product"))
    runtime.store.set_kv("product_head", "not-json")
    assert runtime.engine.enqueue_test_task(**TASK)["id"] == mission["id"]
    assert runtime.store.count_events() == before
    assert not runtime.store.runs(mission["id"])


def test_legacy_admission_replays_without_reading_changed_template(runtime, monkeypatch):
    mission = runtime.engine.launch_mission("PD00", request_id="old-contract", defer=True)
    contract = json.loads(mission["contract_json"])
    contract.pop("request")
    runtime.store.update_mission(mission["id"], contract_json=json.dumps(contract))
    monkeypatch.setattr(runtime.sandbox, "template_digest", lambda *args: pytest.fail("Replay must not hash a changed template"))
    assert runtime.engine.launch_mission("PD00", request_id="old-contract", defer=True)["id"] == mission["id"]
    with pytest.raises(ValueError, match="another task"):
        runtime.engine.launch_mission("PD03", request_id="old-contract", defer=True)


def test_minimum_test_count_tracks_the_frozen_product_and_completed_scope_cannot_repeat(runtime):
    bootstrap(runtime)
    completed = start_product_task(runtime)
    assert completed["status"] == "completed"
    mission = runtime.engine.enqueue_test_task(**TASK)
    contract = json.loads(mission["contract_json"])
    assert contract["scenario"]["minimum_tests"] == 51
    assert contract["source_release"]["tests"]["total"] == 49
    assert set(contract["source_release"]) == {"mission", "packet_digest", "commit", "tree_digest", "path", "tests"}
    with pytest.raises(ValueError, match="already exists"):
        runtime.engine.launch_mission("PD04", request_id="redundant-boundaries", defer=True)


def test_policy_drift_blocks_a_product_task_before_inference(runtime, monkeypatch):
    bootstrap(runtime)
    mission = runtime.engine.enqueue_test_task(**TASK)
    monkeypatch.setattr(runtime.sandbox, "policy_digest", lambda: "changed-policy")
    monkeypatch.setattr(runtime.module, "agent_runner", SimpleNamespace(run=lambda **kwargs: pytest.fail("Policy drift must block before inference")))
    assert runtime.engine.dispatch_once()
    current = runtime.store.mission(mission["id"])
    assert current["gate_reason_code"] == "CONTRACT_CHANGED"
    assert not runtime.store.runs(mission["id"])
    assert not (runtime.sandbox.runs_dir / mission["id"]).exists()
