"""Local delivery reconstructs sealed facts, with no provider or external publish."""
import json
from pathlib import Path

import pytest

from app.delivery import LocalDelivery
from test_missions import patch_runner, run_scenario


def accepted(runtime):
    mission = run_scenario(runtime, "PD01", patch_runner)
    assert runtime.engine.approve(mission["id"])
    return runtime.store.mission(mission["id"])


def test_local_delivery_reconstructs_tests_commits_and_replays_without_changes(runtime, monkeypatch):
    mission = accepted(runtime)
    packet = runtime.store.evidence_for(mission["id"])
    original_packet = Path(packet["path"]).read_bytes()
    source = Path(mission["sandbox_path"])
    original_source = (source / "app/pricing.py").read_bytes()
    delivery = LocalDelivery(runtime.store, runtime.sandbox)
    receipt = delivery.deliver(mission["id"])
    repo = Path(receipt["path"])
    assert receipt["tests"] == {"total": 19, "passed": 19, "exit_code": 0}
    assert receipt["packet_digest"] == packet["digest"]
    assert receipt["base_commit"] == mission["base_commit"]
    assert delivery._git(repo, "rev-parse", "HEAD^") == mission["base_commit"]
    assert delivery._git(repo, "status", "--porcelain") == ""
    assert delivery._git(repo, "remote") == ""
    assert original_packet == Path(packet["path"]).read_bytes()
    assert original_source == (source / "app/pricing.py").read_bytes()
    assert runtime.sandbox.collect_changes(source, base_commit=mission["base_commit"]).digest == mission["diff_digest"]
    events = runtime.store.count_events()
    monkeypatch.setattr(runtime.sandbox, "run_tests", lambda *args, **kwargs: pytest.fail("Idempotent receipt must not rebuild"))
    assert delivery.deliver(mission["id"]) == receipt
    assert runtime.store.count_events() == events


def test_delivery_uses_the_recorded_base_and_patch_not_later_working_tree_edits(runtime):
    mission = accepted(runtime)
    source = Path(mission["sandbox_path"])
    (source / "app/pricing.py").write_text("raise RuntimeError('later unaccepted source')\n")
    (source / "ignored-local-data.txt").write_text("unaccepted local artifact")
    receipt = LocalDelivery(runtime.store, runtime.sandbox).deliver(mission["id"])
    repo = Path(receipt["path"])
    assert receipt["tests"]["passed"] == 19
    assert "later unaccepted" not in (repo / "app/pricing.py").read_text()
    assert not (repo / "ignored-local-data.txt").exists()
    assert "later unaccepted" in (source / "app/pricing.py").read_text()


def test_delivery_refuses_a_human_checkpoint_or_a_failed_mission(runtime):
    waiting = run_scenario(runtime, "PD01", patch_runner)
    with pytest.raises(ValueError, match="completed"):
        LocalDelivery(runtime.store, runtime.sandbox).deliver(waiting["id"])
    assert not (runtime.sandbox.runs_dir.parent / "releases" / waiting["id"]).exists()


def test_changed_local_release_is_refused_and_preserved(runtime):
    mission = accepted(runtime)
    delivery = LocalDelivery(runtime.store, runtime.sandbox)
    receipt = delivery.deliver(mission["id"])
    source = Path(receipt["path"]) / "app/pricing.py"
    source.write_text("# changed release\n")
    with pytest.raises(ValueError, match="changed"):
        delivery.deliver(mission["id"])
    assert source.read_text() == "# changed release\n"


def test_lost_receipt_index_is_recovered_from_exact_published_release(runtime, monkeypatch):
    mission = accepted(runtime)
    delivery = LocalDelivery(runtime.store, runtime.sandbox)
    with monkeypatch.context() as patch:
        patch.setattr(delivery, "_record", lambda receipt: (_ for _ in ()).throw(OSError("test index failure")))
        with pytest.raises(RuntimeError, match="index failure"):
            delivery.deliver(mission["id"])
    assert runtime.store.get_kv(f"delivery:{mission['id']}") is None
    receipt = delivery.deliver(mission["id"])
    assert json.loads(runtime.store.get_kv(f"delivery:{mission['id']}")) == receipt
    assert len(list(delivery.directory.glob("PD-*"))) == 1


def test_changed_policy_or_evidence_cannot_be_delivered(runtime, monkeypatch):
    mission = accepted(runtime)
    delivery = LocalDelivery(runtime.store, runtime.sandbox)
    with monkeypatch.context() as patch:
        patch.setattr(runtime.sandbox, "policy_digest", lambda: "changed")
        with pytest.raises(ValueError, match="policy changed"):
            delivery.deliver(mission["id"])
    packet = runtime.store.evidence_for(mission["id"])
    Path(packet["path"]).write_text("not-json")
    with pytest.raises(ValueError, match="sealed file"):
        delivery.deliver(mission["id"])


def test_failing_revalidation_never_publishes_a_release_and_keeps_diagnostics(runtime, monkeypatch):
    mission = accepted(runtime)
    delivery = LocalDelivery(runtime.store, runtime.sandbox)
    run_tests = runtime.sandbox.run_tests
    def fail_real_tests(repo, **kwargs):
        (repo / "tests/test_delivery_failure.py").write_text("def test_failure():\n    assert False\n")
        return run_tests(repo, **kwargs)
    monkeypatch.setattr(runtime.sandbox, "run_tests", fail_real_tests)
    with pytest.raises(RuntimeError, match="did not pass completely"):
        delivery.deliver(mission["id"])
    assert not (delivery.directory / mission["id"]).exists()
    assert runtime.store.get_kv(f"delivery:{mission['id']}") is None
    retained = next(delivery.directory.glob(f".{mission['id']}-*"))
    assert json.loads((retained / "tests.json").read_text())["failed"] == 1
    assert runtime.store.mission(mission["id"])["status"] == "completed"  # Acceptance is distinct from delivery.
