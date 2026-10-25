"""The executor boundary.

Everything that *runs untrusted code* or *launches a container* goes through this
interface. The controller keeps the ledger, the checkout and the Gate; the executor
only runs the canonical test suite, reports what happened, and runs the two model
roles when inference is containerised.

Two implementations exist:

- `InProcessExecutor`: the controller is a trusted host process and calls the sandbox
  and the agent runner directly. This is the default and the behaviour is unchanged.
- `RemoteExecutor` (in `executor_remote.py`): a loopback/private-network,
  token-authenticated client for the host executor agent. It is selected with
  `AEION_EXECUTOR_URL`, which is what the containerised cockpit does.

The contract is deliberately narrow. A client is never asked to run an arbitrary
command or to execute a path chosen by the caller. `run_tests` receives a prepared
checkout because the in-process implementation needs one; the remote implementation
archives that checkout, verifies it against a digest it computed itself, and the agent
resolves only a content-addressed snapshot. `run_role` accepts prompt material but
never a working directory, so the agent chooses where the role process starts.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .agent_runner import AgentResult
from .config import POC_DIR
from .sandbox import SandboxManager, TestResult

SCHEMA = "aeion.executor-agent/v1"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")

# Bounds are shared by the client and the agent so a refusal is predictable.
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_SNAPSHOT_UNPACKED = 64 * 1024 * 1024
MAX_SNAPSHOT_ENTRIES = 4096
MAX_SNAPSHOT_STORE = 256 * 1024 * 1024
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_TEST_TIMEOUT = 600
MAX_ROLE_TIMEOUT = 900
MAX_ROLE_TEXT = 512 * 1024
MAX_PATCH_TEXT = 1024 * 1024


class ExecutorUnavailable(OSError):
    """The executor could not be reached, refused the request or broke its contract."""


@runtime_checkable
class ExecutorClient(Protocol):
    """Read the executor identity, run the canonical suite and run a model role."""

    def probe(self, *, timeout: float = 180) -> dict[str, Any]:
        """Return the frozen identity of the executor that would run tests."""

    def run_tests(
        self,
        path: Path,
        *,
        timeout: float = 180,
        expected_executor: dict[str, Any] | None = None,
        profile: str = "legacy",
    ) -> TestResult:
        """Run the canonical suite on a prepared checkout and return its report."""

    def run_role(self, **kwargs: Any) -> AgentResult:
        """Run one bounded author or reviewer role and return its result."""

    def inference_probe(self) -> dict[str, Any]:
        """Report whether the model role runtime is ready, without provider calls."""


class InProcessExecutor:
    """Trusted-host executor: the current behaviour, behind the boundary."""

    def __init__(self, workspace: SandboxManager, role_runner=None, inference_probe=None):
        self._workspace = workspace
        self._role_runner = role_runner
        self._inference_probe = inference_probe

    def probe(self, *, timeout: float = 180) -> dict[str, Any]:
        return self._workspace.executor_contract(timeout=timeout)

    def run_tests(
        self,
        path: Path,
        *,
        timeout: float = 180,
        expected_executor: dict[str, Any] | None = None,
        profile: str = "legacy",
    ) -> TestResult:
        return self._workspace.run_tests(path, timeout=timeout, expected_executor=expected_executor,
                                         profile=profile)

    def run_role(self, **kwargs: Any) -> AgentResult:
        if self._role_runner is None:
            from .agent_runner import agent_runner

            return agent_runner.run(**kwargs)
        return self._role_runner(**kwargs)

    def inference_probe(self) -> dict[str, Any]:
        if self._inference_probe is not None:
            return self._inference_probe()
        from .agent_runner import agent_runner

        return agent_runner.probe()


def default_executor(workspace: SandboxManager, role_runner, inference_probe=None) -> ExecutorClient:
    """Use the remote agent when a URL is configured, never as a silent fallback."""
    url = os.environ.get("AEION_EXECUTOR_URL", "").strip()
    if not url:
        return InProcessExecutor(workspace, role_runner=role_runner, inference_probe=inference_probe)
    from .executor_remote import RemoteExecutor

    token_file = Path(os.environ.get("AEION_EXECUTOR_TOKEN_FILE", str(POC_DIR / "runtime/secrets/executor_agent")))
    if not token_file.is_absolute():
        token_file = POC_DIR / token_file
    return RemoteExecutor(url, token_file=token_file)
