"""Trusted host executor agent.

The cockpit runs in a container and must not receive a Docker socket. This agent is
the only component that launches the disposable test worker and the model role
containers. It listens on a loopback or private address, requires a capability token,
accepts only four allowlisted operations, and never accepts a command, a path or an
image from the caller.

Run it on the host as the operator:

    .venv/bin/python -m app.executor_agent --bind 127.0.0.1 --port 8131

The token is created once under `runtime/secrets/executor_agent` (owner-only) and is
shared with the containerised cockpit by an explicit read-only mount. Nothing here is
a substitute for operating-system hardening: the agent can launch containers, so it
must run as the operator, with its own monitored logs, on a host the operator trusts.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import io
import ipaddress
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from .agent_runner import AgentResult
from .config import POC_DIR, settings
from .container_executor import docker_env
from .container_owner import ENV as OWNER_ENV, LABEL as OWNER_LABEL, load_or_create_owner
from .executor import (
    DIGEST,
    MAX_PATCH_TEXT,
    MAX_REQUEST_BYTES,
    MAX_ROLE_TEXT,
    MAX_ROLE_TIMEOUT,
    MAX_SNAPSHOT_BYTES,
    MAX_SNAPSHOT_ENTRIES,
    MAX_SNAPSHOT_STORE,
    MAX_SNAPSHOT_UNPACKED,
    MAX_TEST_TIMEOUT,
    SCHEMA,
)
from .inference_gateway import BoundedServer
from .patch_protocol import PATCH_PROTOCOL
from .sandbox import SandboxManager

ROLE_AGENTS = {"forge", "sentinel"}
ROLE_FIELDS = {
    "mission_id", "run_id", "agent_id", "role", "title", "scenario", "risk", "task",
    "memory_context", "graph_context", "review", "diff_text", "protocol",
    "source_context", "model_override", "timeout_seconds",
}
ROLE_TEXT_LIMITS = {
    "mission_id": 128, "run_id": 128, "agent_id": 32, "role": 200, "title": 500,
    "scenario": 200, "risk": 32, "memory_context": MAX_ROLE_TEXT,
    "graph_context": MAX_ROLE_TEXT, "source_context": MAX_ROLE_TEXT, "task": MAX_PATCH_TEXT,
    "diff_text": MAX_PATCH_TEXT, "model_override": 200,
}
IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


class Denied(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _owner_directory(path: Path) -> Path:
    if path.is_symlink():
        raise Denied("Executor state cannot be a symlink", 500)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise Denied("Executor state must be an operator-owned directory", 500)
    path.chmod(0o700)
    return path


def load_token(path: Path, *, create: bool = True) -> str:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise Denied("Executor token file is missing", 500) from None
        if path.parent.is_symlink():
            raise Denied("Executor token directory cannot be a symlink", 500) from None
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(secrets.token_hex(32) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise Denied("Executor token must be a regular operator-owned file with mode 0600", 500)
    token = path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise Denied("Executor token is not a 64-character lowercase hex capability", 500)
    return token


def validate_bind(host: str) -> str:
    if host == "localhost":
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise Denied("Executor bind must be an IP address or localhost", 500) from None
    if address.is_unspecified or address.is_multicast or address.is_reserved or not (address.is_loopback or address.is_private):
        raise Denied("Executor bind must be loopback or private, never a public interface", 500)
    return str(address)


def _bounded_text(body: dict[str, Any], key: str, limit: int, *, required: bool = False) -> str | None:
    value = body.get(key)
    if value is None:
        if required:
            raise Denied(f"Role field {key} is required")
        return None
    if not isinstance(value, str) or "\0" in value or len(value.encode("utf-8")) > limit:
        raise Denied(f"Role field {key} exceeds its bound")
    return value


class ExecutorAgent:
    def __init__(self, root: Path, token: str, *, workspace: SandboxManager | None = None,
                 role_runner=None, inference_probe=None):
        if not re.fullmatch(r"[0-9a-f]{64}", token):
            raise Denied("Invalid executor capability", 500)
        self.token = token
        self.root = _owner_directory(root)
        self.owner = load_or_create_owner(self.root)
        self.snapshots = _owner_directory(self.root / "snapshots")
        self.runs = _owner_directory(self.root / "runs")
        self.roles = _owner_directory(self.root / "roles")
        self.workspace = workspace or SandboxManager(self.runs / "workspace")
        self.role_runner = role_runner
        self._inference_probe = inference_probe
        self._store_lock = threading.Lock()

    # ------------------------------------------------------------------ probe
    def probe(self) -> dict[str, Any]:
        try:
            identity = self.workspace.executor_contract()
        except Exception as error:
            raise Denied(f"Executor identity unavailable: {type(error).__name__}", 503) from None
        if not isinstance(identity, dict) or not identity:
            raise Denied("Executor identity is empty", 503)
        return identity

    def inference_probe(self) -> dict[str, Any]:
        try:
            if self._inference_probe is not None:
                probe = self._inference_probe()
            else:
                from .agent_runner import agent_runner

                probe = agent_runner.probe()
        except Exception as error:
            return {"ready": False, "runtime": "unknown", "reason": f"Inference probe failed: {type(error).__name__}"}
        return probe if isinstance(probe, dict) else {"ready": False, "runtime": "unknown", "reason": "Invalid inference probe"}

    def reap_stale_containers(self) -> int:
        """Remove containers this state directory's previous agent left running.

        A worker or role container is normally removed by its own cleanup or by
        ``docker run --rm`` at exit. If the agent is killed (SIGKILL, power loss),
        the container keeps running without a client. Only containers carrying
        this agent's random owner label are touched, so a different agent state
        directory is never disturbed.
        """
        listing = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"label={OWNER_LABEL}={self.owner}"],
            env=docker_env(), capture_output=True, text=True, timeout=20,
        )
        if listing.returncode:
            raise OSError("Docker container listing failed; stale workers may remain")
        identifiers = [line for line in listing.stdout.splitlines() if re.fullmatch(r"[0-9a-f]{12,64}", line)]
        if not identifiers:
            return 0
        removal = subprocess.run(
            ["docker", "rm", "--force", *identifiers],
            env=docker_env(), capture_output=True, text=True, timeout=30,
        )
        if removal.returncode:
            raise OSError("Stale worker cleanup failed; operator inspection required")
        return len(identifiers)

    # ------------------------------------------------------------------ snapshots
    def _snapshot_path(self, digest: str) -> Path:
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise Denied("Snapshot identity must be sha256:<64 hex>")
        return self.snapshots / (digest.removeprefix("sha256:") + ".tar.gz")

    def put_snapshot(self, archive: bytes, digest: str) -> str:
        if not isinstance(archive, bytes) or not archive:
            raise Denied("Snapshot body is empty")
        if len(archive) > MAX_SNAPSHOT_BYTES:
            raise Denied("Snapshot exceeds the compressed bound", 413)
        path = self._snapshot_path(digest)
        actual = "sha256:" + hashlib.sha256(archive).hexdigest()
        if not hmac.compare_digest(actual, digest):
            raise Denied("Snapshot digest does not match the body")
        if path.exists():
            return digest
        with self._store_lock:
            total = sum(item.stat().st_size for item in self.snapshots.iterdir() if item.is_file())
            if total + len(archive) > MAX_SNAPSHOT_STORE:
                raise Denied("Snapshot store is full; operator pruning is required", 507)
            temporary = self.snapshots / (".upload-" + secrets.token_hex(8))
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(archive)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return digest

    def _read_snapshot(self, digest: str) -> bytes:
        path = self._snapshot_path(digest)
        if not path.is_file() or path.is_symlink():
            raise Denied("Snapshot was never prepared for this agent")
        if path.stat().st_size > MAX_SNAPSHOT_BYTES:
            raise Denied("Stored snapshot exceeds the compressed bound", 500)
        archive = path.read_bytes()
        actual = "sha256:" + hashlib.sha256(archive).hexdigest()
        if not hmac.compare_digest(actual, digest):
            raise Denied("Stored snapshot no longer matches its identity", 500)
        return archive

    @staticmethod
    def _extract(archive: bytes, destination: Path) -> None:
        try:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
                members = tar.getmembers()
                if len(members) > MAX_SNAPSHOT_ENTRIES:
                    raise Denied("Snapshot has too many entries", 413)
                unpacked = sum(member.size for member in members if member.isfile())
                if unpacked > MAX_SNAPSHOT_UNPACKED:
                    raise Denied("Snapshot exceeds the unpacked bound", 413)
                tar.extractall(destination, members=members, filter="data")
        except (tarfile.TarError, EOFError, OSError) as error:
            raise Denied(f"Snapshot is not a safe tar archive: {type(error).__name__}") from None

    def run_tests(self, digest: str, timeout: float, expected_executor: dict | None) -> dict[str, Any]:
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            raise Denied("Test timeout must be numeric") from None
        if not 1 <= timeout <= MAX_TEST_TIMEOUT:
            raise Denied(f"Test timeout must be between 1 and {MAX_TEST_TIMEOUT} seconds")
        if expected_executor is not None and not isinstance(expected_executor, dict):
            raise Denied("Expected executor identity must be an object")
        identity = self.probe()
        if expected_executor is not None and identity != expected_executor:
            raise Denied("Executor identity differs from the admitted identity; no tests started", 409)
        archive = self._read_snapshot(digest)
        with tempfile.TemporaryDirectory(dir=self.runs, prefix="snapshot-") as temporary:
            root = Path(temporary)
            self._extract(archive, root)
            result = self.workspace.run_tests(root, timeout=timeout, expected_executor=expected_executor)
        payload = result.to_dict()
        payload["junit_path"] = None
        system_temp = tempfile.gettempdir()
        paths = (str(self.root), temporary) + ((system_temp,) if system_temp not in {"/tmp", "/var/tmp"} else ())
        return _redact(payload, paths)

    # ------------------------------------------------------------------ roles
    def run_role(self, body: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise Denied("Role request must be an object")
        unknown = sorted(set(body) - ROLE_FIELDS)
        if unknown:
            raise Denied(f"Unsupported role fields: {unknown}")
        for key, limit in ROLE_TEXT_LIMITS.items():
            _bounded_text(body, key, limit, required=key in {"mission_id", "agent_id", "role", "title", "scenario", "risk", "task"})
        for key in ("mission_id", "run_id", "agent_id"):
            if key in body and not IDENTITY.fullmatch(body[key]):
                raise Denied(f"Role field {key} has an invalid identity")
        if body["agent_id"] not in ROLE_AGENTS:
            raise Denied("Only the forge and sentinel roles are allowed")
        if body.get("protocol") != PATCH_PROTOCOL:
            raise Denied("Remote roles require the text-only patch protocol")
        if not isinstance(body.get("review"), bool):
            raise Denied("Role field review must be a boolean")
        timeout = body.get("timeout_seconds")
        if timeout is not None and (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
                                    or not 1 <= timeout <= MAX_ROLE_TIMEOUT):
            raise Denied("Role timeout is out of bounds")
        run_id = body.get("run_id") or "run-" + uuid.uuid4().hex
        body = {**body, "run_id": run_id}
        scratch = self.roles / run_id
        try:
            scratch.mkdir(mode=0o700)
        except FileExistsError:
            raise Denied("Role run identity was already used", 409) from None
        arguments = {key: body[key] for key in ("mission_id", "run_id", "agent_id", "role", "title",
                                                "scenario", "risk", "task") if key in body}
        for key in ("memory_context", "graph_context", "diff_text", "source_context", "model_override", "timeout_seconds"):
            if key in body:
                arguments[key] = body[key]
        arguments["review"] = body["review"]
        arguments["protocol"] = body["protocol"]
        arguments["cwd"] = scratch
        try:
            runner = self.role_runner or _default_role_runner
            result = runner(**arguments)
            if not isinstance(result, AgentResult):
                raise Denied("Role runner returned no result", 500)
            payload = result.to_dict()
            payload["result_text"] = result.result_text[:MAX_PATCH_TEXT]
            payload["argv"] = []
            payload["raw"].pop("events", None)  # event streams stay on the agent, bounded by its logs
            for key in ("stdout_path", "stderr_path", "prompt_path"):
                payload[key] = None
            return _redact(payload, (str(self.root), str(scratch)))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


def _default_role_runner(**kwargs: Any) -> AgentResult:
    if settings.inference_backend != "docker":
        raise Denied("Remote roles require the Docker inference backend", 503)
    from .agent_runner import agent_runner

    return agent_runner.run(**kwargs)


def _redact(value: Any, paths: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        for path in paths:
            value = value.replace(path, "<agent>")
        return value
    if isinstance(value, list):
        return [_redact(item, paths) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item, paths) for key, item in value.items()}
    return value


def handler(agent: ExecutorAgent):
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        server_version = "aeion-executor/1"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:
            sys.stderr.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), format % args))

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            if not header.startswith("Bearer ") or not hmac.compare_digest(header[7:], agent.token):
                self._send(401, {"error": "missing or invalid capability"})
                return False
            return True

        def _body(self, limit: int) -> bytes:
            sizes = self.headers.get_all("Content-Length", [])
            if len(sizes) != 1 or len(sizes[0]) > 9 or not sizes[0].isdigit() or self.headers.get("Transfer-Encoding"):
                raise Denied("One bounded Content-Length is required", 411)
            size = int(sizes[0])
            if not 0 < size <= limit:
                raise Denied(f"Request body must be between 1 and {limit} bytes", 413)
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise Denied("Truncated request body", 400)
            return raw

        def do_GET(self) -> None:
            if self.path == "/health":
                return self._send(200, {"status": "ok", "schema": SCHEMA})
            if not self._authorized():
                return
            if self.path == "/v1/probe":
                try:
                    return self._send(200, {"schema": SCHEMA, "executor": agent.probe(),
                                            "inference": agent.inference_probe()})
                except Denied as error:
                    return self._send(error.status, {"error": str(error)})
            return self._send(404, {"error": "unknown operation"})

        def do_POST(self) -> None:
            if not self._authorized():
                return
            try:
                if self.path == "/v1/snapshots":
                    digest = self.headers.get("X-Aeion-Digest", "")
                    agent.put_snapshot(self._body(MAX_SNAPSHOT_BYTES), digest)
                    return self._send(200, {"schema": SCHEMA, "digest": digest})
                if self.path == "/v1/tests":
                    body = json.loads(self._body(MAX_REQUEST_BYTES).decode("utf-8"))
                    if not isinstance(body, dict):
                        raise Denied("Test request must be an object")
                    result = agent.run_tests(body.get("digest"), body.get("timeout", 180),
                                             body.get("expected_executor"))
                    return self._send(200, {"schema": SCHEMA, "result": result})
                if self.path == "/v1/roles":
                    body = json.loads(self._body(MAX_REQUEST_BYTES).decode("utf-8"))
                    result = agent.run_role(body)
                    return self._send(200, {"schema": SCHEMA, "result": result})
            except Denied as error:
                return self._send(error.status, {"error": str(error)})
            except (ValueError, UnicodeDecodeError):
                return self._send(400, {"error": "invalid JSON request"})
            except Exception:
                traceback.print_exc(file=sys.stderr)
                return self._send(500, {"error": "executor operation failed"})
            return self._send(404, {"error": "unknown operation"})

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=POC_DIR / "runtime/executor-agent")
    parser.add_argument("--token-file", type=Path, default=POC_DIR / "runtime/secrets/executor_agent")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8131)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        token = load_token(args.token_file)
        host = validate_bind(args.bind)
        if not 1 <= args.port <= 65535:
            raise Denied("Port is out of range", 500)
        agent = ExecutorAgent(args.root, token)
        server = BoundedServer((host, args.port), handler(agent))
    except (Denied, OSError) as error:
        print(f"executor agent refused to start: {error}", file=sys.stderr)
        return 2
    # Every container launched from now on carries this agent's owner label.
    os.environ[OWNER_ENV] = agent.owner
    try:
        reaped = agent.reap_stale_containers()
    except OSError as error:
        print(f"executor agent warning: {error}", file=sys.stderr)
    else:
        if reaped:
            print(f"executor agent removed {reaped} container(s) left by a previous run", flush=True)
    print(f"executor agent listening on {host}:{args.port}; token file {args.token_file}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
