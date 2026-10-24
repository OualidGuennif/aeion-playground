"""A bounded product loop with actual Git, pytest, sealing and local promotion.

All author/reviewer answers here are controlled fixtures, not OpenCode executions.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.delivery import LocalDelivery
from test_missions import patch_runner, result, run_scenario


def bootstrap(runtime):
    mission = run_scenario(runtime, "PD01", patch_runner)
    assert runtime.engine.approve(mission["id"])
    return LocalDelivery(runtime.store, runtime.sandbox).deliver(mission["id"])


def coverage_runner(**kwargs):
    if kwargs.get("review"):
        return result("coverage-review", model="opencode/glm-review-fixture", verdict="OK", reason="30 meaningful boundary cases; original code and tests unchanged.")
    text = (
        'import pytest\n'
        'from app.pricing import quote\n'
        '@pytest.mark.parametrize("k", range(30))\n'
        'def test_every_started_kilogram(k):\n'
        '    assert quote(1000*k + 1) == 500 + 300*k\n'
    )
    if kwargs.get("protocol"):
        return proposal("tests/test_boundaries_extended.py", text)
    (kwargs["cwd"] / "tests/test_boundaries_extended.py").write_text(text)
    return result("coverage-author", model="opencode/deepseek-author-fixture")


def proposal(filename, text):
    patch = (f"diff --git a/{filename} b/{filename}\nnew file mode 100644\n"
             f"--- /dev/null\n+++ b/{filename}\n@@ -0,0 +1,{len(text.splitlines())} @@\n"
             + "".join("+" + line + "\n" for line in text.splitlines()))
    return result("coverage-author", model="opencode/deepseek-author-fixture",
                  result_text=json.dumps({"patch": patch, "summary": "Add coverage."}))


def start_product_task(runtime, runner=coverage_runner, request_id="coverage-1"):
    runtime.monkeypatch.setattr(runtime.module, "agent_runner", SimpleNamespace(run=runner))
    mission = runtime.engine.launch_mission("PD04", request_id=request_id, defer=True)
    assert runtime.engine.dispatch_once()
    return runtime.store.mission(mission["id"])


def test_real_local_loop_freezes_base_runs_49_tests_seals_and_advances_product(runtime):
    initial = bootstrap(runtime)
    mission = start_product_task(runtime)
    assert mission["status"] == "completed" and mission["outcome"] == "autonomous"
    assert mission["human_approval"] is None
    assert mission["gate_reason_code"] == "CLEAN"
    assert json.loads(mission["tests_json"])["passed"] == 49
    body = runtime.store.evidence_for(mission["id"])["payload"]["payload"]
    assert body["contract"]["source_release"]["commit"] == initial["commit"]
    assert body["mission"]["human_approval"] is None and body["sealed"]
    receipt = json.loads(runtime.store.get_kv(f"delivery:{mission['id']}"))
    assert receipt["parent_release"]["commit"] == initial["commit"]
    assert receipt["base_commit"] == initial["commit"]
    assert receipt["tests"] == {"total": 49, "passed": 49, "exit_code": 0}
    assert json.loads(runtime.store.get_kv("product_head")) == receipt
    assert runtime.sandbox.template_digest() != receipt["tree_digest"]  # Defective original template is preserved.
    assert LocalDelivery(runtime.store, runtime.sandbox).deliver(initial["mission"]) == initial
    assert json.loads(runtime.store.get_kv("product_head")) == receipt  # An old replay never rolls it back.
    assert runtime.engine.launch_mission("PD04", request_id="coverage-1", defer=True)["id"] == mission["id"]
    assert len(runtime.store.runs(mission["id"])) == 2
    with TestClient(runtime.main.app, base_url="http://127.0.0.1") as client:
        payload = client.get(f"/api/missions/{mission['id']}").json()
    assert payload["delivery"] == receipt and payload["delivery_error"] is None


def test_no_approved_base_means_no_product_admission_or_model_call(runtime):
    with pytest.raises(RuntimeError, match="No accepted local product"):
        runtime.engine.launch_mission("PD04", request_id="unseeded", defer=True)
    assert runtime.store.count_missions() == 0


def test_queued_old_product_base_is_refused_before_model_call(runtime):
    initial = bootstrap(runtime)
    runtime.monkeypatch.setattr(runtime.module, "agent_runner", SimpleNamespace(run=coverage_runner))
    one = runtime.engine.launch_mission("PD04", request_id="first", defer=True)
    two = runtime.engine.launch_mission("PD04", request_id="old-base", defer=True)
    assert runtime.engine.dispatch_once()
    assert runtime.store.mission(one["id"])["status"] == "completed"
    assert runtime.engine.dispatch_once()
    assert runtime.store.mission(two["id"])["gate_reason_code"] == "STALE_BASE"
    assert not runtime.store.runs(two["id"])
    assert not (runtime.sandbox.runs_dir / two["id"]).exists()


@pytest.mark.parametrize("attack", ["pricing", "weaken-existing", "too-few-tests"])
def test_product_task_cannot_widen_scope_weaken_old_tests_or_skip_new_coverage(runtime, attack):
    bootstrap(runtime)
    def attacking_runner(**kwargs):
        reply = coverage_runner(**kwargs)
        if not kwargs.get("review"):
            if attack == "pricing":
                reply = proposal("app/unrequested.py", "# outside admitted test-only task\n")
            elif attack == "weaken-existing":
                patch = json.loads(reply.result_text)
                patch["patch"] += ('diff --git a/tests/test_pricing.py b/tests/test_pricing.py\n'
                                   '--- a/tests/test_pricing.py\n+++ b/tests/test_pricing.py\n'
                                   '@@ -5,1 +5,2 @@\n+# forbidden change to original tests\n import pytest\n')
                reply.result_text = json.dumps(patch)
            else:
                reply = proposal("tests/test_boundaries_extended.py", 'def test_trivial():\n    assert True\n')
        return reply
    mission = start_product_task(runtime, attacking_runner)
    assert mission["status"] == "blocked"
    assert mission["gate_reason_code"] == ("TEST_COVERAGE" if attack == "too-few-tests" else "TASK_SCOPE")
    assert runtime.store.get_kv(f"delivery:{mission['id']}") is None


@pytest.mark.parametrize("review_mode", ["same-family", "unidentified", "failed", "rejected"])
def test_a_failed_or_same_family_review_cannot_be_claimed_as_full_autonomy(runtime, review_mode):
    bootstrap(runtime)
    def no_independence(**kwargs):
        reply = coverage_runner(**kwargs)
        if kwargs.get("review"):
            assert "30 parametrized regression cases" in kwargs["task"]
            if review_mode == "same-family":
                reply.model = "opencode/deepseek-review-fixture"
            elif review_mode == "unidentified":
                reply.model = ""
            elif review_mode == "failed":
                reply.status = "failed"
                reply.error = "controlled review failure"
            else:
                reply.verdict = "CHANGES_REQUIRED"
        return reply
    mission = start_product_task(runtime, no_independence)
    assert mission["status"] == ("blocked" if review_mode == "rejected" else "awaiting_human")
    assert runtime.store.get_kv(f"delivery:{mission['id']}") is None


def test_accepted_but_unpublished_delivery_recovers_after_restart_without_model_replay(runtime, monkeypatch):
    bootstrap(runtime)
    with monkeypatch.context() as patch:
        patch.setattr(runtime.engine, "_deliver_local", lambda mission_id: None)
        mission = start_product_task(runtime)
    assert mission["status"] == "completed"
    assert runtime.store.get_kv(f"delivery:{mission['id']}") is None
    calls = len(runtime.store.runs(mission["id"]))
    runtime.engine.recover_interrupted()
    receipt = json.loads(runtime.store.get_kv(f"delivery:{mission['id']}"))
    assert receipt["mission"] == mission["id"]
    assert len(runtime.store.runs(mission["id"])) == calls
    events = runtime.store.count_events()
    runtime.engine.recover_interrupted()
    assert runtime.store.count_events() == events


def test_stale_release_promotion_cannot_overwrite_a_newer_product_head(runtime):
    initial = bootstrap(runtime)
    fake = {**initial, "mission": "PD-999", "commit": "0" * 40, "parent_release": {"commit": "1" * 40}}
    with pytest.raises(ValueError, match="STALE_BASE"):
        runtime.store.record_delivery(fake)
    assert runtime.store.get_kv("delivery:PD-999") is None
    assert json.loads(runtime.store.get_kv("product_head")) == initial
