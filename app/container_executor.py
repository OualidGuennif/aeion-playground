"""Disposable Docker pytest, launched by the trusted controller without a worker socket."""
from contextlib import contextmanager
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import uuid

from .container_owner import owner_label_args

ROOT = Path(__file__).resolve().parents[1]
IMAGE_RECORD = ROOT / "runtime/executor-image.json"
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
MAX_FILES = 1024
MAX_WIRE_BYTES = 5 * 1024 * 1024
LIMITS = {"memory_bytes": 1024 * 1024 * 1024, "cpus": 1.5, "pids": 256,
          "scratch_bytes": 512 * 1024 * 1024, "file_bytes": 4 * 1024 * 1024, "open_files": 4096}
CODE_DIGEST = "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class WorkerReportError(RuntimeError):
    """The container ran, but its output cannot establish a test result."""


def docker_env():
    # Docker's trusted client needs its selected local context. None of these
    # host variables are passed as environment variables to the test process.
    return {key: value for key, value in os.environ.items()
            if key in {"PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "LANG", "LC_ALL"}}


def executor_identity():
    if IMAGE_RECORD.is_symlink() or not IMAGE_RECORD.is_file():
        raise OSError("Qualified Docker worker image missing; run scripts/build_worker.py")
    record = json.loads(IMAGE_RECORD.read_text())
    image = record.get("image_id", "")
    if record.get("schema") != "aeion.worker-image/v1" or not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise OSError("Invalid qualified worker image identity")
    try:
        inspected = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                                   env=docker_env(), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OSError("Docker worker backend unavailable; no native fallback") from error
    if inspected.returncode or inspected.stdout.strip() != image:
        raise OSError("Qualified Docker image unavailable; no native fallback")
    return {"backend": "docker-pytest/v1", "image_id": image, "implementation_digest": CODE_DIGEST,
            "wire_bytes": MAX_WIRE_BYTES,
            "logging": "none",
            "user": "65532:65532", "network": "none", "rootfs": "read-only", "capabilities": [],
            "no_new_privileges": True, "limits": LIMITS.copy()}


def snapshot(root: Path, target: Path):
    paths, size, manifest = [], 0, {}
    for name in ("app", "tests", "product"):
        directory = root / name
        if directory.is_symlink():
            raise OSError("Worker source directories cannot be symlinks")
        if directory.is_dir():
            paths.extend(directory.rglob("*"))
    paths.append(root / "pytest.ini")
    for source in sorted(paths):
        relative = source.relative_to(root)
        if "__pycache__" in relative.parts or source.suffix == ".pyc":
            continue
        if source.is_symlink():
            raise OSError("Worker source snapshot refuses symlinks")
        if source.is_dir():
            continue
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SNAPSHOT_BYTES:
                raise OSError("Worker source snapshot requires bounded regular files")
            data = stream.read(MAX_SNAPSHOT_BYTES + 1)
        size += len(data)
        if size > MAX_SNAPSHOT_BYTES or len(manifest) >= MAX_FILES:
            raise OSError("Worker source snapshot exceeds its size/count bound")
        if any(part == "secrets" or part.startswith(".env") for part in relative.parts):
            raise OSError("Private files are forbidden in the worker snapshot")
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        destination.chmod(0o444)
        manifest[str(relative)] = hashlib.sha256(data).hexdigest()
    # The agent runs with umask 077, so mkdir leaves the snapshot unreadable by
    # the container's uid 65532 on Linux. The snapshot contains only public
    # checkout files and no secrets; make its directories traversable.
    for directory, _, _ in os.walk(target):
        os.chmod(directory, 0o755, follow_symlinks=False)
    return "sha256:" + hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


@contextmanager
def isolated_pytest(root, *, timeout, profile="legacy"):
    if profile not in {"legacy", "product"}:
        raise OSError("Unknown test profile")
    identity = executor_identity()
    name = "aeion-test-" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="aeion-worker-") as temporary:
        source = Path(temporary) / "source"
        source.mkdir(mode=0o755)
        digest = snapshot(root, source)
        argv = ["docker", "run", "--rm", "--name", name, "--label", "aeion.playground.role=test",
                *owner_label_args(), "--log-driver", "none",
                "--network", "none", "--read-only", "--user", identity["user"],
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--memory", str(LIMITS["memory_bytes"]), "--memory-swap", str(LIMITS["memory_bytes"]),
                "--cpus", str(LIMITS["cpus"]), "--pids-limit", str(LIMITS["pids"]),
                "--ulimit", f"fsize={LIMITS['file_bytes']}:{LIMITS['file_bytes']}",
                "--ulimit", f"nofile={LIMITS['open_files']}:{LIMITS['open_files']}",
                "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={LIMITS['scratch_bytes']},mode=1777",
                "--mount", f"type=bind,source={source},target=/work,readonly",
                identity["image_id"], str(timeout), profile]
        metadata = {"backend": identity["backend"], "executor": identity, "source_digest": digest,
                    "container": name, "profile": profile, "required": True, "checkout": "read-only minimal snapshot",
                    "network": "denied", "child_processes": "container PID namespace and quota",
                    "writes": "bounded /tmp tmpfs only"}
        try:
            yield argv, docker_env(), metadata
        finally:
            # Killing the Docker client alone does not stop a timed-out container.
            try:
                cleanup = subprocess.run(["docker", "rm", "--force", name], env=docker_env(),
                                         capture_output=True, text=True, timeout=10)
            except subprocess.TimeoutExpired as error:
                raise OSError(f"Docker worker cleanup timed out for {name}; operator inspection required") from error
            if cleanup.returncode and "No such container" not in cleanup.stderr:
                raise OSError(f"Docker worker cleanup failed for {name}; operator inspection required")


def decode_result(process, report: Path, *, output_limit, report_limit):
    if process.returncode:
        failure = OSError if process.returncode in {125, 126, 127} else WorkerReportError
        raise failure(f"Docker worker failed with exit {process.returncode}; no native fallback")
    try:
        def unique_object(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("Duplicate worker wire field")
                value[key] = item
            return value
        payload = json.loads(process.stdout, object_pairs_hook=unique_object)
        if set(payload) != {"schema", "exit_code", "error", "stdout", "stderr", "report", "duration_ms"} or payload["schema"] != "aeion.test-wire/v1":
            raise ValueError("Invalid worker wire schema")
        if payload["error"]:
            raise ValueError(str(payload["error"])[:1000])
        if type(payload["exit_code"]) is not int:
            raise ValueError("Worker result has no integer exit code")
        stdout, stderr, junit = (base64.b64decode(payload[key], validate=True) for key in ("stdout", "stderr", "report"))
        if len(stdout) + len(stderr) > output_limit:
            raise ValueError(f"Combined worker output exceeded {output_limit} bytes")
        if len(junit) > report_limit:
            raise ValueError("Worker report exceeds its size bound")
        with report.open("xb") as stream:
            stream.write(junit)
        return subprocess.CompletedProcess(process.args, payload["exit_code"], stdout.decode(errors="replace"), stderr.decode(errors="replace"))
    except (ValueError, TypeError, KeyError) as error:
        raise WorkerReportError(f"Unusable worker report: {error}") from error
