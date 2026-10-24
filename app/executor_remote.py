"""Client for the trusted host executor agent.

The cockpit container uses this client instead of receiving a Docker socket. The
agent is reached only on a loopback or private address, every request carries a
capability token, and responses are bounded before parsing. Redirects are refused so
a compromised agent cannot move the token to another host.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import ipaddress
import json
import os
import re
import socket
import stat
import tarfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .agent_runner import AgentResult
from .executor import (
    MAX_PATCH_TEXT,
    MAX_RESPONSE_BYTES,
    MAX_ROLE_TEXT,
    MAX_SNAPSHOT_BYTES,
    MAX_SNAPSHOT_ENTRIES,
    MAX_SNAPSHOT_UNPACKED,
    SCHEMA,
    ExecutorUnavailable,
)
from .patch_protocol import PATCH_PROTOCOL
from .sandbox import TestResult

EXCLUDED_PARTS = {".git", "__pycache__", ".pytest_cache", "secrets"}
EXCLUDED_NAMES = {"junit.xml", ".DS_Store"}
ROLE_FIELDS = {
    "mission_id", "run_id", "agent_id", "role", "title", "scenario", "risk", "task",
    "memory_context", "graph_context", "review", "diff_text", "protocol",
    "source_context", "model_override", "timeout_seconds",
}
ROLE_TEXT_LIMITS = {
    "role": 200,
    "title": 500,
    "scenario": 200,
    "risk": 32,
    "memory_context": MAX_ROLE_TEXT,
    "graph_context": MAX_ROLE_TEXT,
    "source_context": MAX_ROLE_TEXT,
    "task": MAX_PATCH_TEXT,
    "diff_text": MAX_PATCH_TEXT,
}
TEST_FIELDS = {field.name for field in dataclasses.fields(TestResult)}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise ExecutorUnavailable("Executor agent attempted a redirect; request refused")


def _private_host(host: str, port: int) -> None:
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except OSError as error:
        raise ExecutorUnavailable(f"Executor agent host cannot be resolved: {error}") from error
    if not addresses:
        raise ExecutorUnavailable("Executor agent host resolved to no address")
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if not (parsed.is_loopback or parsed.is_private):
            raise ExecutorUnavailable("Executor agent must resolve to a loopback or private address")


def read_token(path: Path) -> str:
    try:
        info = path.lstat()
    except OSError as error:
        raise ExecutorUnavailable(f"Executor token file cannot be read: {error}") from error
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o002:
        raise ExecutorUnavailable("Executor token must be a regular file, not world-writable")
    token = path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise ExecutorUnavailable("Executor token is not a 64-character lowercase hex capability")
    return token


def build_snapshot(root: Path) -> tuple[bytes, str]:
    """Archive a checkout deterministically and return (bytes, digest)."""
    root = root.resolve()
    if not root.is_dir():
        raise ExecutorUnavailable("Test checkout is not a directory")
    buffer = io.BytesIO()
    entries = 0
    unpacked = 0
    with tarfile.open(fileobj=buffer, mode="w:gz", compresslevel=1, format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if any(part in EXCLUDED_PARTS for part in relative.parts):
                continue
            if path.is_symlink():
                raise ExecutorUnavailable(f"Symlink in test checkout: {relative}")
            if path.is_dir():
                continue
            if not path.is_file() or path.name in EXCLUDED_NAMES or path.suffix == ".pyc":
                continue
            if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
                continue
            entries += 1
            unpacked += path.stat().st_size
            if entries > MAX_SNAPSHOT_ENTRIES or unpacked > MAX_SNAPSHOT_UNPACKED:
                raise ExecutorUnavailable("Test checkout exceeds the snapshot bound")
            info = archive.gettarinfo(str(path), arcname=relative.as_posix())
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            info.mode = 0o755 if os.access(path, os.X_OK) else 0o644
            with path.open("rb") as stream:
                archive.addfile(info, stream)
    data = buffer.getvalue()
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise ExecutorUnavailable("Test checkout exceeds the compressed snapshot bound")
    return data, "sha256:" + hashlib.sha256(data).hexdigest()


class RemoteExecutor:
    def __init__(self, base_url: str, *, token_file: Path | None = None, token: str | None = None,
                 timeout: float = 30):
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
            raise ExecutorUnavailable("Executor agent URL must be a plain http URL without credentials")
        if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ExecutorUnavailable("Executor agent URL must not contain a path, query or fragment")
        port = parsed.port or 80
        _private_host(parsed.hostname, port)
        self.base_url = f"http://{parsed.hostname}:{port}"
        self.token = token or read_token(token_file or Path("runtime/secrets/executor_agent"))
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect)

    # ------------------------------------------------------------------ transport
    def _request(self, method: str, path: str, body: bytes | None = None, *,
                 headers: dict[str, str] | None = None, timeout: float) -> bytes:
        request = urllib.request.Request(self.base_url + path, data=body, method=method)
        request.add_header("Authorization", "Bearer " + self.token)
        request.add_header("Accept", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with self._opener.open(request, timeout=max(1.0, timeout)) as response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                raw = error.read(4096)
                detail = str(json.loads(raw.decode("utf-8", "replace")).get("error", ""))[:500]
            except (OSError, ValueError, AttributeError):
                pass
            raise ExecutorUnavailable(f"Executor agent refused ({error.code}): {detail or 'no detail'}") from None
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            raise ExecutorUnavailable(f"Executor agent unavailable: {error}") from None
        if len(data) > MAX_RESPONSE_BYTES:
            raise ExecutorUnavailable("Executor agent response exceeds the bound")
        return data

    def _json(self, method: str, path: str, body: dict[str, Any] | None = None, *,
              headers: dict[str, str] | None = None, timeout: float) -> dict[str, Any]:
        raw = self._request(method, path, None if body is None else json.dumps(body).encode(),
                            headers={"Content-Type": "application/json", **(headers or {})}, timeout=timeout)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            raise ExecutorUnavailable(f"Executor agent returned invalid JSON: {error}") from None
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
            raise ExecutorUnavailable("Executor agent response does not match the schema")
        return payload

    # ------------------------------------------------------------------ operations
    def probe(self, *, timeout: float = 180) -> dict[str, Any]:
        payload = self._json("GET", "/v1/probe", timeout=min(self.timeout, 30))
        executor = payload.get("executor")
        if not isinstance(executor, dict) or not executor:
            raise ExecutorUnavailable("Executor agent returned no executor identity")
        return executor

    def run_tests(self, path: Path, *, timeout: float = 180,
                  expected_executor: dict[str, Any] | None = None) -> TestResult:
        archive, digest = build_snapshot(path)
        self._request("POST", "/v1/snapshots", archive,
                      headers={"Content-Type": "application/x-tar", "X-Aeion-Digest": digest}, timeout=60)
        payload = self._json("POST", "/v1/tests", {
            "digest": digest, "timeout": timeout, "expected_executor": expected_executor,
        }, timeout=timeout + 60)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ExecutorUnavailable("Executor agent returned no test result")
        values = {key: value for key, value in result.items() if key in TEST_FIELDS}
        try:
            report = TestResult(**values)
        except TypeError as error:
            raise ExecutorUnavailable(f"Executor agent test result is malformed: {error}") from None
        if expected_executor is not None and report.executor != expected_executor:
            raise ExecutorUnavailable("Executor agent ran a different executor identity than admitted")
        return report

    def run_role(self, **kwargs: Any) -> AgentResult:
        on_lease = kwargs.pop("on_lease", None)
        kwargs.pop("cwd", None)  # the agent chooses the working directory
        kwargs.setdefault("review", False)
        unknown = set(kwargs) - ROLE_FIELDS
        if unknown:
            raise ExecutorUnavailable(f"Unsupported role fields: {sorted(unknown)}")
        for key, limit in ROLE_TEXT_LIMITS.items():
            value = kwargs.get(key)
            if value is not None and (not isinstance(value, str) or len(value.encode()) > limit):
                raise ExecutorUnavailable(f"Role field {key} exceeds its bound")
        if kwargs.get("protocol") != PATCH_PROTOCOL or kwargs.get("review") not in (True, False):
            raise ExecutorUnavailable("Remote roles require the text-only patch protocol")
        timeout = float(kwargs.get("timeout_seconds") or 900)
        payload = self._json("POST", "/v1/roles", kwargs, timeout=min(timeout + 60, 1200))
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ExecutorUnavailable("Executor agent returned no role result")
        names = {field.name for field in dataclasses.fields(AgentResult)}
        values = {key: value for key, value in result.items() if key in names}
        try:
            agent_result = AgentResult(**values)
        except TypeError as error:
            raise ExecutorUnavailable(f"Executor agent role result is malformed: {error}") from None
        lease = (agent_result.raw.get("inference") or {}).get("lease_id")
        if on_lease and lease:
            on_lease(lease)
        return agent_result
