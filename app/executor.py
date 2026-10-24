"""The executor boundary.

Everything that *runs untrusted code* or *launches a container* goes through this
interface. The controller keeps the ledger, the checkout and the Gate; the executor
only runs the canonical test suite and reports what happened.

Two implementations are intended:

- `InProcessExecutor` (today): the controller is a trusted host process and calls the
  sandbox directly. This is the default and the behaviour is unchanged.
- a remote executor reached over a loopback, token-authenticated API, used once the
  cockpit itself runs in a container and must not receive a Docker socket.

The contract is deliberately narrow. A client is never asked to run an arbitrary
command, open a path chosen by the caller, or accept a result without an executor
identity. `run_tests` receives a prepared checkout because the in-process
implementation needs one; a remote implementation archives that checkout, verifies it
against the digest it computed itself, and never forwards the host path to the agent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .sandbox import SandboxManager, TestResult


@runtime_checkable
class ExecutorClient(Protocol):
    """Read the executor identity and run the canonical test suite."""

    def probe(self, *, timeout: float = 180) -> dict[str, Any]:
        """Return the frozen identity of the executor that would run tests."""

    def run_tests(
        self,
        path: Path,
        *,
        timeout: float = 180,
        expected_executor: dict[str, Any] | None = None,
    ) -> TestResult:
        """Run the canonical suite on a prepared checkout and return its report."""


class InProcessExecutor:
    """Trusted-host executor: the current behaviour, behind the new boundary."""

    def __init__(self, workspace: SandboxManager):
        self._workspace = workspace

    def probe(self, *, timeout: float = 180) -> dict[str, Any]:
        return self._workspace.executor_contract(timeout=timeout)

    def run_tests(
        self,
        path: Path,
        *,
        timeout: float = 180,
        expected_executor: dict[str, Any] | None = None,
    ) -> TestResult:
        return self._workspace.run_tests(path, timeout=timeout, expected_executor=expected_executor)
