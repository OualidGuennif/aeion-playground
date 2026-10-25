"""Host-side GitHub delivery refuses unsealed or diverged releases."""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from app.evidence import _digest
from app.github_delivery import (
    DeliveryError,
    GitHubDelivery,
    _issue_body,
    _pr_body,
    _scope_error,
    _run,
    load_release,
)

MISSION = "PD-900"


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(path), "-c", "user.name=Test",
                             "-c", "user.email=test@localhost", *args],
                            check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _completed(code: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, stdout, stderr)


def _make_release(tmp_path: Path, *, decision: str = "allow", approval=None,
                  outcome: str = "autonomous") -> tuple[Path, Path]:
    runtime = tmp_path / "runtime/cockpit"
    template = tmp_path / "template"
    product = template / "sandbox/parceldesk/product"
    (product / "nested").mkdir(parents=True)
    (product / "a.txt").write_text("one\n")
    (product / "nested/b.txt").write_text("two\n")
    _git(template, "init", "-q", "-b", "main")
    _git(template, "add", "-A")
    _git(template, "commit", "-q", "-m", "template")

    repo = runtime / "sandbox/releases" / MISSION / "repo"
    repo.mkdir(parents=True)
    shutil.copytree(product, repo / "product")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "baseline")
    baseline = _git(repo, "rev-parse", "HEAD")
    (repo / "product/a.txt").write_text("changed\n")
    (repo / "product/new.txt").write_text("new\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "accepted change")
    head = _git(repo, "rev-parse", "HEAD")

    payload = {
        "schema": "aeion.mission.evidence/v1",
        "mission": {"id": MISSION, "title": "Refund ledger", "status": "completed",
                    "risk": "high", "outcome": outcome, "human_approval": approval},
        "gate": {"decision": decision, "reason_code": "CLEAN" if decision == "allow" else "HUMAN_APPROVED",
                 "requires_human": decision == "approved"},
        "review": "OK",
        "tests": {"total": 17, "passed": 17, "failed": 0, "skipped": 0},
        "sealed": True,
    }
    digest = "sha256:" + _digest(payload)
    payload["digest"] = digest
    packet = runtime / "croa/packets" / f"CROA-{MISSION}.json"
    packet.parent.mkdir(parents=True)
    packet.write_text(json.dumps(payload))

    receipt = {
        "schema": "aeion.local-delivery/v1", "mission": MISSION, "commit": head,
        "packet_digest": digest, "product": "product",
        "parent_release": {"mission": "PD-899", "commit": baseline},
        "path": str(repo),
    }
    (runtime / "sandbox/releases" / MISSION / "receipt.json").write_text(json.dumps(receipt))

    connection = sqlite3.connect(runtime / "parceldesk.db")
    connection.execute("""CREATE TABLE tickets (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, outcome TEXT NOT NULL, area TEXT NOT NULL,
        risk TEXT NOT NULL, acceptance TEXT NOT NULL, constraints TEXT NOT NULL DEFAULT '',
        mission_id TEXT, status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL,
        product TEXT NOT NULL DEFAULT 'legacy')""")
    connection.execute("INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("TCK-TEST900", "Expose the refund ledger", "An operator reads refunds.",
                        "refunds", "high", "GET /api/refunds returns refunded orders.", "",
                        MISSION, "completed", None, "2026-10-25T04:00:00+00:00", "product"))
    connection.commit()
    connection.close()
    return runtime, template


def test_a_sealed_release_is_verified_against_its_receipt_and_packet(tmp_path):
    runtime, template = _make_release(tmp_path)
    release = load_release(runtime, MISSION)
    assert release.head == release.receipt["commit"]
    assert release.ticket["id"] == "TCK-TEST900"
    assert release.template_tree == _git(template, "rev-parse", "HEAD:sandbox/parceldesk/product")
    assert release.parent_tree == _git(release.repo, "rev-parse", "HEAD^:product")
    assert release.head_tree != release.parent_tree


def test_a_changed_packet_or_unrecorded_approval_is_refused(tmp_path):
    runtime, _ = _make_release(tmp_path)
    packet = runtime / f"croa/packets/CROA-{MISSION}.json"
    body = json.loads(packet.read_text())
    body["tests"]["passed"] = 16
    packet.write_text(json.dumps(body))
    with pytest.raises(DeliveryError, match="changed"):
        load_release(runtime, MISSION)

    runtime, _ = _make_release(tmp_path / "approved")
    packet = runtime / f"croa/packets/CROA-{MISSION}.json"
    body = json.loads(packet.read_text())
    body["gate"] = {"decision": "approved", "reason_code": "HUMAN_APPROVED"}
    body["digest"] = "sha256:" + _digest({k: v for k, v in body.items() if k != "digest"})
    packet.write_text(json.dumps(body))
    receipt = runtime / f"sandbox/releases/{MISSION}/receipt.json"
    record = json.loads(receipt.read_text())
    record["packet_digest"] = body["digest"]
    receipt.write_text(json.dumps(record))
    with pytest.raises(DeliveryError, match="operator approval"):
        load_release(runtime, MISSION)


def test_prepare_syncs_only_the_product_tree_and_commits(tmp_path):
    runtime, template = _make_release(tmp_path)
    release = load_release(runtime, MISSION)
    calls: list[list[str]] = []

    def fake(argv, cwd, *, input_text=None, check=True, timeout=180):
        calls.append(list(argv))
        if argv[0] == "gh" and argv[1:3] == ["repo", "clone"]:
            subprocess.run(["git", "clone", "-q", str(template), argv[4]], check=True)
            return _completed()
        return _run(argv, cwd, input_text=input_text, check=check, timeout=timeout)

    delivery = GitHubDelivery(release, "owner/repo", work_root=tmp_path / "work",
                              state_root=tmp_path / "state", run=fake)
    plan = delivery.prepare()
    assert plan.branch == "factory/pd-900"
    assert plan.changed_files == ["sandbox/parceldesk/product/a.txt",
                                  "sandbox/parceldesk/product/new.txt"]
    assert (plan.checkout / "sandbox/parceldesk/product/a.txt").read_text() == "changed\n"
    assert (plan.checkout / "sandbox/parceldesk/product/nested/b.txt").read_text() == "two\n"
    assert _git(plan.checkout, "status", "--porcelain") == ""
    assert "Closes" not in plan.pr_body
    assert "TCK-TEST900" in plan.issue_body
    assert delivery.state()["commit"] == plan.commit


def test_prepare_refuses_a_diverged_product_tree(tmp_path):
    runtime, template = _make_release(tmp_path)
    (template / "sandbox/parceldesk/product/drift.txt").write_text("operator drift\n")
    _git(template, "add", "-A")
    _git(template, "commit", "-q", "-m", "drift")
    release = load_release(runtime, MISSION)

    def fake(argv, cwd, *, input_text=None, check=True, timeout=180):
        if argv[0] == "gh" and argv[1:3] == ["repo", "clone"]:
            subprocess.run(["git", "clone", "-q", str(template), argv[4]], check=True)
            return _completed()
        return _run(argv, cwd, input_text=input_text, check=check, timeout=timeout)

    with pytest.raises(DeliveryError, match="does not match"):
        GitHubDelivery(release, "owner/repo", work_root=tmp_path / "w2",
                       state_root=tmp_path / "s2", run=fake).prepare()


def test_scope_and_body_helpers_are_strict(tmp_path):
    assert _scope_error(["sandbox/parceldesk/product/a.txt"]) is None
    assert _scope_error(["sandbox/parceldesk/product"]) is None
    assert _scope_error(["app/main.py"]) == "Change outside sandbox/parceldesk/product/: app/main.py"
    runtime, _ = _make_release(tmp_path)
    release = load_release(runtime, MISSION)
    title, body = _issue_body(release)
    assert "TCK-TEST900" in body and f"aeion-mission:{MISSION}" in body
    assert title == "Expose the refund ledger"
    title, body = _pr_body(release, 7)
    assert "product-tests" in body and "Closes #7" in body
    assert "17/17" in body and "OK" in body


def test_publish_creates_the_issue_pr_waits_and_merges(tmp_path):
    runtime, template = _make_release(tmp_path)
    release = load_release(runtime, MISSION)
    calls: list[list[str]] = []
    checks = [
        {"name": "product-tests", "state": "SUCCESS", "bucket": "pass"},
        {"name": "native-tests", "state": "SUCCESS", "bucket": "pass"},
    ]

    def fake(argv, cwd, *, input_text=None, check=True, timeout=180):
        calls.append(list(argv))
        if argv[0] == "gh" and argv[1:3] == ["repo", "clone"]:
            subprocess.run(["git", "clone", "-q", str(template), argv[4]], check=True)
            return _completed()
        if argv[0] == "gh" and argv[1:3] == ["issue", "list"]:
            return _completed(stdout="[]")
        if argv[0] == "gh" and argv[1:3] == ["issue", "create"]:
            calls.append(["ISSUE", argv[-1]])
            return _completed(stdout="https://github.com/owner/repo/issues/7\n")
        if argv[0] == "gh" and argv[1:3] == ["pr", "list"]:
            return _completed(stdout="[]")
        if argv[0] == "gh" and argv[1:3] == ["pr", "create"]:
            calls.append(["PR", argv[-1]])
            return _completed(stdout="https://github.com/owner/repo/pull/8\n")
        if argv[0] == "gh" and argv[1:3] == ["pr", "checks"]:
            return _completed(stdout=json.dumps(checks))
        if argv[0] == "gh" and argv[1:3] == ["pr", "merge"]:
            return _completed()
        return _run(argv, cwd, input_text=input_text, check=check, timeout=timeout)

    delivery = GitHubDelivery(release, "owner/repo", work_root=tmp_path / "work",
                              state_root=tmp_path / "state", run=fake)
    plan = delivery.prepare()
    result = delivery.publish(plan, wait=True, merge=True)
    assert result["merged"] is True
    assert result["issue"]["number"] == 7 and result["pr"]["number"] == 8
    assert any(call[:2] == ["git", "-C"] and "push" in call for call in calls)
    assert delivery.state()["pr"]["state"] == "MERGED"


def test_merge_is_refused_when_a_check_fails_or_product_tests_are_missing(tmp_path):
    runtime, template = _make_release(tmp_path)
    release = load_release(runtime, MISSION)

    def fake_failed(argv, cwd, *, input_text=None, check=True, timeout=180):
        if argv[0] == "gh" and argv[1:3] == ["repo", "clone"]:
            subprocess.run(["git", "clone", "-q", str(template), argv[4]], check=True)
            return _completed()
        if argv[0] == "gh" and argv[1:3] in (["issue", "list"], ["pr", "list"]):
            return _completed(stdout="[]")
        if argv[0] == "gh" and argv[1:3] == ["issue", "create"]:
            return _completed(stdout="https://github.com/owner/repo/issues/7\n")
        if argv[0] == "gh" and argv[1:3] == ["pr", "create"]:
            return _completed(stdout="https://github.com/owner/repo/pull/8\n")
        if argv[0] == "gh" and argv[1:3] == ["pr", "checks"]:
            return _completed(stdout=json.dumps([{"name": "product-tests", "bucket": "fail"}]))
        return _run(argv, cwd, input_text=input_text, check=check, timeout=timeout)

    delivery = GitHubDelivery(release, "owner/repo", work_root=tmp_path / "wf",
                              state_root=tmp_path / "sf", run=fake_failed)
    plan = delivery.prepare()
    with pytest.raises(DeliveryError, match="failed"):
        delivery.publish(plan, wait=True, merge=True)


def test_a_new_pull_request_without_registered_checks_is_pending(tmp_path):
    runtime, _ = _make_release(tmp_path)
    release = load_release(runtime, MISSION)

    def fake(argv, cwd, *, input_text=None, check=True, timeout=180):
        if argv[0] == "gh" and argv[1:3] == ["pr", "checks"]:
            return _completed(1, stderr="no checks reported on the 'factory/pd-900' branch")
        return _run(argv, cwd, input_text=input_text, check=check, timeout=timeout)

    delivery = GitHubDelivery(release, "owner/repo", work_root=tmp_path / "w",
                              state_root=tmp_path / "s", run=fake)
    assert delivery._checks(1) == []


def test_merge_existing_waits_for_the_recorded_pr_and_merges(tmp_path):
    runtime, _ = _make_release(tmp_path)
    release = load_release(runtime, MISSION)
    calls: list[list[str]] = []

    def fake(argv, cwd, *, input_text=None, check=True, timeout=180):
        calls.append(list(argv))
        if argv[0] == "gh" and argv[1:3] == ["pr", "checks"]:
            return _completed(stdout=json.dumps([{"name": "product-tests", "bucket": "pass"}]))
        if argv[0] == "gh" and argv[1:3] == ["pr", "merge"]:
            return _completed()
        return _run(argv, cwd, input_text=input_text, check=check, timeout=timeout)

    delivery = GitHubDelivery(release, "owner/repo", work_root=tmp_path / "w2",
                              state_root=tmp_path / "s2", run=fake)
    delivery._record(issue={"number": 7, "url": "https://example/7", "state": "OPEN"},
                     pr={"number": 8, "url": "https://example/8", "state": "OPEN"})
    result = delivery.merge_existing()
    assert result["merged"] is True and result["pr"]["state"] == "MERGED"
    assert any(call[1:3] == ["pr", "merge"] for call in calls)
    assert delivery.state()["pr"]["state"] == "MERGED"


def test_publish_moves_an_existing_open_pr_to_the_new_commit(tmp_path):
    runtime, template = _make_release(tmp_path)
    release = load_release(runtime, MISSION)
    calls: list[list[str]] = []

    def fake(argv, cwd, *, input_text=None, check=True, timeout=180):
        calls.append(list(argv))
        if argv[0] == "gh" and argv[1:3] == ["repo", "clone"]:
            subprocess.run(["git", "clone", "-q", str(template), argv[4]], check=True)
            return _completed()
        if argv[0] == "gh" and argv[1:3] == ["pr", "checks"]:
            return _completed(stdout=json.dumps([{"name": "product-tests", "bucket": "pass"}]))
        if argv[0] == "gh" and argv[1:3] == ["pr", "merge"]:
            return _completed()
        return _run(argv, cwd, input_text=input_text, check=check, timeout=timeout)

    delivery = GitHubDelivery(release, "owner/repo", work_root=tmp_path / "w3",
                              state_root=tmp_path / "s3", run=fake)
    plan = delivery.prepare()
    delivery._record(issue={"number": 7, "state": "OPEN"},
                     pr={"number": 8, "url": "https://example/8", "state": "OPEN"})
    result = delivery.publish(plan, create_issue=False, wait=True, merge=True)
    assert result["merged"] is True
    assert any(call[:2] == ["git", "-C"] and "push" in call for call in calls)
