"""Boundary tests for the host executor agent and its container-side client.

No Docker daemon and no model provider are used. The workspace and the role runner
are fakes, so these tests measure the protocol: token, content-addressed snapshots,
digest verification, path redaction, identity refusal and the role allowlist.
"""
from __future__ import annotations

import copy
import io
import json
import os
import stat
import subprocess
import tarfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from app import container_owner
from app.agent_runner import AgentResult
from app.executor import ExecutorUnavailable, MAX_SNAPSHOT_BYTES
from app.executor_agent import Denied, ExecutorAgent, handler, load_token, validate_bind
from app.executor_remote import RemoteExecutor, build_snapshot, read_token
from app.inference_gateway import BoundedServer
from app.patch_protocol import PATCH_PROTOCOL
from app.sandbox import TestResult as SandboxTestResult

TOKEN = "a" * 64
IDENTITY = {"backend": "docker/v1", "isolation": "disposable", "limits": {"seconds": 180}, "image_id": "sha256:" + "b" * 64}


class FakeWorkspace:
    def __init__(self, identity=None):
        self.identity = copy.deepcopy(identity or IDENTITY)
        self.calls = []
        self.seen = []
        self.existed = False

    def executor_contract(self, *, timeout: float = 180):
        return copy.deepcopy(self.identity)

    def run_tests(self, path, *, timeout: float = 180, expected_executor=None):
        self.calls.append({"path": Path(path), "timeout": timeout, "expected": expected_executor})
        self.seen.append(Path(path))
        self.existed = Path(path).is_dir()
        return SandboxTestResult(
            available=True, exit_code=0, total=3, passed=3,
            junit_path=str(Path(path) / "junit.xml"),
            command=["python", "-m", "pytest", str(Path(path) / "junit.xml")],
            stdout="3 passed", stderr="", executor=copy.deepcopy(self.identity),
        )


def role_result(**kwargs):
    assert kwargs["cwd"].is_dir()
    assert kwargs["cwd"].name == kwargs["run_id"]
    return AgentResult(
        status="completed", runtime="fake", run_id=kwargs["run_id"],
        result_text='{"patch":"diff --git a/app/pricing.py b/app/pricing.py\\n","summary":"bounded"}',
        stdout_path="/host/private/stdout.jsonl", stderr_path="/host/private/stderr.txt",
        prompt_path="/host/private/prompt.txt", raw={"inference": {"lease_id": "lease-1"}, "events": []},
    )


@pytest.fixture()
def checkout(tmp_path):
    root = tmp_path / "checkout"
    (root / "app").mkdir(parents=True)
    (root / "app/pricing.py").write_text("def quote(grams):\n    return 0\n")
    (root / "pytest.ini").write_text("[pytest]\n")
    (root / "policy").mkdir()
    (root / "policy/policy.yaml").write_text("version: 1\n")
    return root


@pytest.fixture()
def agent(tmp_path):
    workspace = FakeWorkspace()
    instance = ExecutorAgent(tmp_path / "agent", TOKEN, workspace=workspace, role_runner=role_result,
                             inference_probe=lambda: {"ready": True, "runtime": "fake", "reason": None})
    server = BoundedServer(("127.0.0.1", 0), handler(instance))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance, workspace, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def raw_status(url: str, *, token: str | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_probe_requires_the_capability_and_health_leaks_nothing(agent):
    instance, workspace, base = agent
    assert raw_status(base + "/health")[0] == 200
    status, body = raw_status(base + "/v1/probe")
    assert status == 401 and "executor" not in body
    assert raw_status(base + "/v1/probe", token="c" * 64)[0] == 401
    status, body = raw_status(base + "/v1/probe", token=TOKEN)
    assert status == 200 and body["executor"] == IDENTITY
    assert body["inference"] == {"ready": True, "runtime": "fake", "reason": None}
    assert RemoteExecutor(base, token=TOKEN).inference_probe() == body["inference"]


def test_an_unprepared_snapshot_is_refused_before_any_test_run(agent):
    instance, workspace, base = agent
    client = RemoteExecutor(base, token=TOKEN)
    with pytest.raises(ExecutorUnavailable, match="never prepared"):
        client._json("POST", "/v1/tests", {"digest": "sha256:" + "0" * 64, "timeout": 10,
                                          "expected_executor": None}, timeout=30)
    assert workspace.calls == []


def test_snapshot_digest_is_verified_and_the_store_is_content_addressed(agent, checkout):
    instance, workspace, base = agent
    client = RemoteExecutor(base, token=TOKEN)
    archive, digest = build_snapshot(checkout)
    with pytest.raises(ExecutorUnavailable, match="digest does not match"):
        client._request("POST", "/v1/snapshots", archive, headers={"X-Aeion-Digest": "sha256:" + "f" * 64}, timeout=30)
    assert client._request("POST", "/v1/snapshots", archive,
                           headers={"X-Aeion-Digest": digest}, timeout=30)
    client._request("POST", "/v1/snapshots", archive, headers={"X-Aeion-Digest": digest}, timeout=30)
    stored = list((instance.snapshots).iterdir())
    assert len(stored) == 1 and stored[0].name == digest.removeprefix("sha256:") + ".tar.gz"
    stored[0].write_bytes(b"tampered")
    with pytest.raises(ExecutorUnavailable, match="no longer matches"):
        client._json("POST", "/v1/tests", {"digest": digest, "timeout": 10, "expected_executor": None}, timeout=30)
    assert workspace.calls == []


def test_an_unsafe_archive_is_refused_without_writing_outside_the_root(agent, tmp_path):
    instance, workspace, base = agent
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        payload = b"escape"
        info = tarfile.TarInfo("../escape")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    data = buffer.getvalue()
    digest = "sha256:" + __import__("hashlib").sha256(data).hexdigest()
    client = RemoteExecutor(base, token=TOKEN)
    client._request("POST", "/v1/snapshots", data, headers={"X-Aeion-Digest": digest}, timeout=30)
    with pytest.raises(ExecutorUnavailable, match="safe tar"):
        client._json("POST", "/v1/tests", {"digest": digest, "timeout": 10, "expected_executor": None}, timeout=30)
    assert not (tmp_path / "escape").exists()
    assert workspace.calls == []


def test_tests_run_on_a_temporary_snapshot_and_host_paths_are_redacted(agent, checkout, tmp_path):
    instance, workspace, base = agent
    client = RemoteExecutor(base, token=TOKEN)
    report = client.run_tests(checkout, timeout=30)
    assert report.available and report.total == 3 and report.passed == 3
    assert report.junit_path is None
    assert workspace.calls[0]["timeout"] == 30
    seen = workspace.seen[0]
    assert workspace.existed and checkout not in seen.parents
    assert not seen.exists()
    rendered = json.dumps(report.to_dict())
    assert str(instance.root) not in rendered and str(tmp_path) not in rendered


def test_executor_identity_mismatch_stops_before_any_test_run(agent, checkout):
    instance, workspace, base = agent
    client = RemoteExecutor(base, token=TOKEN)
    archive, digest = build_snapshot(checkout)
    client._request("POST", "/v1/snapshots", archive, headers={"X-Aeion-Digest": digest}, timeout=30)
    with pytest.raises(ExecutorUnavailable, match="differs from the admitted identity"):
        client._json("POST", "/v1/tests", {"digest": digest, "timeout": 10,
                                          "expected_executor": {"executor": "other"}}, timeout=30)
    assert workspace.calls == []


def test_role_requires_the_patch_protocol_and_refuses_a_working_directory(agent):
    instance, workspace, base = agent
    client = RemoteExecutor(base, token=TOKEN)
    body = {
        "mission_id": "PD-001", "run_id": "run-test-1", "agent_id": "forge", "role": "the patch author",
        "title": "Started-kilogram rounding", "scenario": "agent", "risk": "high", "task": "Repair the rule.",
        "review": False, "protocol": PATCH_PROTOCOL,
    }
    leases = []
    result = client.run_role(**body, on_lease=leases.append, cwd=Path("/host/private"))
    assert result.status == "completed" and result.raw["inference"]["lease_id"] == "lease-1"
    assert result.stdout_path is None and result.stderr_path is None and result.prompt_path is None
    assert result.result_text.startswith('{"patch":"diff --git')
    assert leases == ["lease-1"]
    assert not (instance.roles / "run-test-1").exists()
    with pytest.raises(ExecutorUnavailable, match="patch protocol"):
        client.run_role(**{**body, "run_id": "run-test-2", "protocol": None})
    with pytest.raises(ExecutorUnavailable, match="Unsupported role fields"):
        client._json("POST", "/v1/roles", {**body, "cwd": "/etc"}, timeout=30)
    with pytest.raises(ExecutorUnavailable):
        client.run_role(**{**body, "run_id": "run-test-3", "agent_id": "atlas"})


def test_bind_and_token_files_are_restricted(tmp_path):
    assert validate_bind("localhost") == "127.0.0.1"
    assert validate_bind("192.168.1.10") == "192.168.1.10"
    for host in ("0.0.0.0", "8.8.8.8", "example.com"):
        with pytest.raises(Denied):
            validate_bind(host)
    created = tmp_path / "secrets/executor_agent"
    token = load_token(created)
    assert len(token) == 64 and stat.S_IMODE(created.stat().st_mode) == 0o600
    assert read_token(created) == token
    created.chmod(0o644)
    with pytest.raises(Denied):
        load_token(created)
    created.chmod(0o666)
    with pytest.raises(ExecutorUnavailable):
        read_token(created)


def test_the_agent_reaps_only_containers_with_its_own_owner_label(tmp_path, monkeypatch):
    instance = ExecutorAgent(tmp_path / "agent", TOKEN, workspace=FakeWorkspace(), role_runner=role_result,
                             inference_probe=lambda: {"ready": True})
    listing = "\n".join(["a" * 64, "b" * 64]) + "\n"
    observed = []

    def fake_run(argv, **kwargs):
        observed.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=listing if argv[1] == "ps" else "", stderr="")

    monkeypatch.setattr("app.executor_agent.subprocess.run", fake_run)
    assert instance.reap_stale_containers() == 2
    assert observed[0] == ["docker", "ps", "-aq", "--filter",
                           f"label=aeion.playground.owner={instance.owner}"]
    assert observed[1] == ["docker", "rm", "--force", "a" * 64, "b" * 64]


def test_an_agent_restart_keeps_its_owner_and_does_not_remove_nothing(tmp_path, monkeypatch):
    first = ExecutorAgent(tmp_path / "agent", TOKEN, workspace=FakeWorkspace(), role_runner=role_result)
    second = ExecutorAgent(tmp_path / "agent", TOKEN, workspace=FakeWorkspace(), role_runner=role_result)
    assert first.owner == second.owner
    assert stat.S_IMODE((tmp_path / "agent" / "container-owner").stat().st_mode) == 0o600
    observed = []

    def fake_run(argv, **kwargs):
        observed.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("app.executor_agent.subprocess.run", fake_run)
    assert second.reap_stale_containers() == 0
    assert len(observed) == 1  # a listing with no match never issues docker rm


def test_a_failed_reap_is_an_error_not_a_silent_continue(tmp_path, monkeypatch):
    instance = ExecutorAgent(tmp_path / "agent", TOKEN, workspace=FakeWorkspace(), role_runner=role_result)

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="daemon unavailable")

    monkeypatch.setattr("app.executor_agent.subprocess.run", fake_run)
    with pytest.raises(OSError, match="stale workers may remain"):
        instance.reap_stale_containers()


def test_container_label_flags_never_accept_an_untrusted_owner(monkeypatch):
    monkeypatch.delenv(container_owner.ENV, raising=False)
    assert container_owner.owner_label_args() == []
    monkeypatch.setenv(container_owner.ENV, "c" * 32)
    assert container_owner.owner_label_args() == ["--label", "aeion.playground.owner=" + "c" * 32]
    for value in ("", "C" * 32, "--privileged", "c" * 31, "c" * 33):
        monkeypatch.setenv(container_owner.ENV, value)
        assert container_owner.owner_label_args() == []


def test_oversized_snapshots_are_refused_by_the_client(checkout, tmp_path, monkeypatch):
    import app.executor_remote as remote

    monkeypatch.setattr(remote, "MAX_SNAPSHOT_BYTES", 16)
    with pytest.raises(ExecutorUnavailable, match="compressed snapshot bound"):
        build_snapshot(checkout)
