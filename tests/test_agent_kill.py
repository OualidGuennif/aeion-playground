"""Opt-in qualification: a killed agent leaves a worker, and the next start reaps it.

A real agent process launches a real worker container running a sleeping test. The
test kills the agent's whole process group, proves the request fails and the worker
is still running, then starts the agent again on the same state directory and proves
the owner-label reaper removes it. No model, provider or credential is involved.

Enable with ``AEION_DOCKER_TESTS=1``; requires the qualified worker image.
"""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest

from app.executor import ExecutorUnavailable
from app.executor_remote import RemoteExecutor

pytestmark = pytest.mark.skipif(
    os.environ.get("AEION_DOCKER_TESTS") != "1",
    reason="Requires explicitly enabled Docker qualification",
)
ROOT = Path(__file__).parents[1]


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60)


def owned_containers(owner: str, *, include_stopped: bool = False) -> list[str]:
    flags = "-aq" if include_stopped else "-q"
    result = docker("ps", flags, "--filter", f"label=aeion.playground.owner={owner}")
    assert result.returncode == 0, "Docker container listing failed"
    return result.stdout.split()


def start_agent(root: Path, token_file: Path, port: int, log: Path) -> subprocess.Popen:
    stream = log.open("ab")
    # The test harness defaults to the native backend; this qualification must
    # measure the real Docker worker that the agent launches.
    environment = {**os.environ, "AEION_TEST_BACKEND": "docker", "PYTHONUNBUFFERED": "1"}
    try:
        return subprocess.Popen(
            [sys.executable, "-m", "app.executor_agent", "--root", str(root),
             "--token-file", str(token_file), "--bind", "127.0.0.1", "--port", str(port)],
            cwd=ROOT, env=environment, start_new_session=True,
            stdout=stream, stderr=subprocess.STDOUT,
        )
    finally:
        stream.close()


def wait_until(predicate, timeout: float, message: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(message)


def wait_for_running_worker(owner: str, caller: threading.Thread, outcome: dict, log: Path) -> str:
    deadline = time.monotonic() + 60
    last = "no container"
    while time.monotonic() < deadline:
        identifiers = owned_containers(owner, include_stopped=True)
        if identifiers:
            state = docker("inspect", identifiers[0], "--format", "{{.State.Status}}|{{.State.Running}}")
            last = state.stdout.strip() or last
            if last == "running|true":
                return identifiers[0]
        if not caller.is_alive():
            break
        time.sleep(0.1)
    raise AssertionError(
        f"worker did not start ({last}): {outcome}; agent log: {log.read_text()[-1500:]}"
    )


def wait_ready(url: str, token_file: Path, timeout: float = 30) -> None:
    def ready() -> bool:
        try:
            RemoteExecutor(url, token_file=token_file).probe()
            return True
        except ExecutorUnavailable:
            return False

    wait_until(ready, timeout, "agent did not become ready")


def stop_agent(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)


def test_a_killed_agent_leaves_a_worker_that_the_next_start_reaps(tmp_path):
    root, token_file = tmp_path / "agent", tmp_path / "token"
    token_file.write_text(secrets.token_hex(32))
    token_file.chmod(0o600)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    log = tmp_path / "agent.log"

    checkout = tmp_path / "checkout"
    (checkout / "tests").mkdir(parents=True)
    (checkout / "pytest.ini").write_text("[pytest]\npythonpath = .\ntestpaths = tests\n")
    (checkout / "tests/test_slow.py").write_text("import time\n\ndef test_slow():\n    time.sleep(30)\n")

    agent = start_agent(root, token_file, port, log)
    restarted = None
    owner = None
    try:
        wait_ready(url, token_file)
        owner = (root / "container-owner").read_text().strip()
        assert len(owner) == 32

        outcome = {}

        def request():
            try:
                outcome["tests"] = RemoteExecutor(url, token_file=token_file, timeout=120).run_tests(checkout, timeout=120).to_dict()
            except Exception as error:  # the killed connection is the expected outcome
                outcome["error"] = f"{type(error).__name__}: {error}"

        caller = threading.Thread(target=request, daemon=True)
        caller.start()
        container = wait_for_running_worker(owner, caller, outcome, log)
        found_at = time.monotonic()

        stop_agent(agent)
        caller.join(timeout=30)
        assert not caller.is_alive()
        assert str(outcome.get("error", "")).startswith("ExecutorUnavailable")
        assert owned_containers(owner) == [container], "the killed agent's worker no longer exists"
        assert docker("inspect", container, "--format", "{{.State.Running}}").stdout.strip() == "true"

        restarted = start_agent(root, token_file, port, log)
        wait_ready(url, token_file)
        wait_until(lambda: not owned_containers(owner), 20, "the restarted agent did not reap its stale worker")
        # The worker sleeps for 30 seconds, so a disappearance within 10 seconds
        # of finding it cannot be the container exiting on its own.
        assert time.monotonic() - found_at < 10, "the worker ended on its own; reaping was not proven"
        assert docker("ps", "-aq", "--filter", f"id={container}").stdout.strip() == ""
    finally:
        stop_agent(agent)
        if restarted is not None:
            stop_agent(restarted)
        if owner:
            identifiers = owned_containers(owner, include_stopped=True)
            if identifiers:
                docker("rm", "--force", *identifiers)
