"""The observer must not report acceptance as a finished local delivery."""
import json
import sys

import pytest

from scripts import factory_queue


@pytest.mark.parametrize("delivery_error", [None, "stale local base"])
def test_wait_observes_delivery_after_acceptance_and_reports_delivery_failures(monkeypatch, capsys, delivery_error):
    contract = json.dumps({"scenario": {"delivery": "local"}})
    queued = {"id": "PD-010", "status": "queued", "contract_json": contract}
    completed = {**queued, "status": "completed"}
    replies = iter([
        {"mission": queued},
        {"mission": completed, "delivery": None, "delivery_error": None},
        {"mission": completed, "delivery": None if delivery_error else {"commit": "abc"}, "delivery_error": delivery_error},
    ])
    calls = []
    def request(*args):
        calls.append(args)
        return next(replies)
    monkeypatch.setattr(factory_queue, "request", request)
    monkeypatch.setattr(factory_queue.time, "sleep", lambda _: None)
    monkeypatch.setattr(sys, "argv", ["factory_queue", "--fixture", "PD04", "--request-id", "coverage", "--wait"])
    assert factory_queue.main() == (1 if delivery_error else 0)
    assert len(calls) == 3
    output = capsys.readouterr()
    if delivery_error:
        assert delivery_error in output.err
    else:
        assert '"commit": "abc"' in output.out


def test_task_file_submits_the_explicit_definition_to_the_existing_queue(monkeypatch, tmp_path):
    definition = {"title": "Pricing invariant coverage", "task": "Add exhaustive pricing invariant tests.",
                  "test_file": "tests/test_invariants.py", "minimum_new_tests": 2}
    file = tmp_path / "task.json"
    file.write_text(json.dumps(definition))
    calls = []
    def submit(base, path, payload):
        calls.append((path, payload))
        return {"mission": {"id": "PD-010", "status": "queued"}}
    monkeypatch.setattr(factory_queue, "request", submit)
    monkeypatch.setattr(sys, "argv", ["factory_queue", "--task-file", str(file), "--request-id", "invariants"])
    assert factory_queue.main() == 0
    assert calls == [("/api/tasks/tests", {**definition, "request_id": "invariants"})]


@pytest.mark.parametrize("definition", [None, [], {}, {"risk": "low"}])
def test_malformed_task_file_is_rejected_without_a_request(monkeypatch, tmp_path, definition):
    file = tmp_path / "task.json"
    file.write_text(json.dumps(definition))
    monkeypatch.setattr(factory_queue, "request", lambda *args: pytest.fail("Invalid task must not be submitted"))
    monkeypatch.setattr(sys, "argv", ["factory_queue", "--task-file", str(file), "--request-id", "invalid"])
    assert factory_queue.main() == 1
