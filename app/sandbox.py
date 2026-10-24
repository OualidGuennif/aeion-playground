"""Real sandbox, git diff and pytest execution.

Every mission gets a fresh copy of `sandbox/parceldesk`, committed to a local
git repository. Diffs are real `git diff` output and tests are real `pytest`
runs. Nothing in this module fabricates a result: a missing tool returns an
explicit error.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import POC_DIR, settings
from .test_isolation import executor_identity, isolated_pytest

MAX_TEST_OUTPUT_BYTES = 1024 * 1024
MAX_JUNIT_BYTES = 2 * 1024 * 1024
MAX_JUNIT_CASES = 1000
PYTEST_ARGUMENTS = ("-m", "pytest", "-q", "--capture=no", "-p", "no:cacheprovider", "--junitxml")
EXECUTOR_CODE_DIGEST = "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

IGNORE = shutil.ignore_patterns(
    ".git",
    "__pycache__",
    ".pytest_cache",
    "*.pyc",
    "junit.xml",
    ".DS_Store",
)


def _sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _local_process_env() -> dict[str, str]:
    """Canonical git/test subprocesses do not inherit the workspace secrets."""
    keep = {"PATH", "LANG", "LC_ALL", "TERM", "TMPDIR", "SYSTEMROOT"}
    env = {key: value for key, value in os.environ.items() if key in keep}
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return env


def _run(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int = 180,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    output_limit: int | None = None,
) -> subprocess.CompletedProcess[str]:
    if output_limit is not None:
        if input_text is not None or output_limit < 1:
            raise ValueError("Bounded capture requires a positive limit and no stdin payload")
        return _bounded_run(argv, cwd=cwd, timeout=timeout, env=env, output_limit=output_limit)
    return subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        input=input_text,
        stdin=subprocess.DEVNULL if input_text is None else None,
        close_fds=True,
    )


class _OutputLimitExceeded(subprocess.SubprocessError):
    def __init__(self, limit: int, stdout: str, stderr: str):
        super().__init__(f"Process output exceeded {limit} combined stdout/stderr bytes")
        self.stdout, self.stderr = stdout, stderr


def _bounded_run(argv, *, cwd, timeout, env, output_limit):
    """Drain both pipes without buffering unbounded output; reap on every failure."""
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    def decoded(name):
        return buffers[name].decode("utf-8", errors="replace")
    deadline = time.monotonic() + timeout
    with subprocess.Popen(
        argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, start_new_session=True,
    ) as process, selectors.DefaultSelector() as selector:
        completed = False
        try:
            for name in buffers:
                selector.register(getattr(process, name), selectors.EVENT_READ, name)
            total = 0
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout)
                for key, _ in selector.select(min(.1, remaining)):
                    chunk = os.read(key.fd, min(65536, output_limit - total + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    retained = chunk[:output_limit - total]
                    buffers[key.data].extend(retained)
                    total += len(retained)
                    if len(retained) != len(chunk):
                        raise _OutputLimitExceeded(output_limit, decoded("stdout"), decoded("stderr"))
            code = process.wait(timeout=max(0, deadline - time.monotonic()))
            completed = True
            return subprocess.CompletedProcess(argv, code, decoded("stdout"), decoded("stderr"))
        except subprocess.TimeoutExpired:
            raise subprocess.TimeoutExpired(argv, timeout, output=decoded("stdout"), stderr=decoded("stderr")) from None
        finally:
            if not completed:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            process.stdout.close()
            process.stderr.close()


@dataclass
class TestResult:
    available: bool
    exit_code: int | None = None
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration: float = 0.0
    stdout: str = ""
    stderr: str = ""
    cases: list[dict[str, str]] = field(default_factory=list)
    junit_path: str | None = None
    error: str | None = None
    command: list[str] = field(default_factory=list)
    isolation: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)
    executor: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        if self.error:
            return self.error
        if not self.available:
            return self.error or "pytest unavailable"
        return (
            f"{self.total} tests · {self.passed} passed · {self.failed} failed"
            f" · {self.errors} errors · {self.skipped} skipped"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "exit_code": self.exit_code,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "skipped": self.skipped,
            "duration": round(self.duration, 3),
            "stdout": self.stdout[-8000:],
            "stderr": self.stderr[-8000:],
            "cases": self.cases[:80],
            "junit_path": self.junit_path,
            "error": self.error,
            "command": self.command,
            "isolation": self.isolation,
            "limits": self.limits,
            "executor": self.executor,
        }


@dataclass
class DiffResult:
    available: bool
    patch: str = ""
    files: list[dict[str, Any]] = field(default_factory=list)
    lines_added: int = 0
    lines_removed: int = 0
    digest: str = ""
    error: str | None = None

    @property
    def changed_files(self) -> list[str]:
        return [item["path"] for item in self.files]

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "files": self.files,
            "changed_files": self.changed_files,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
            "digest": self.digest,
            "patch": self.patch,
            "error": self.error,
        }


class SandboxManager:
    template_dir: Path = POC_DIR / "sandbox" / "parceldesk"

    def __init__(self, runs_dir: Path | None = None) -> None:
        self.runs_dir = runs_dir or (settings.sandbox_dir.parent / "runs")
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ setup
    def _git(self, path: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return _run(["git", *args], cwd=path, timeout=60, env=_local_process_env())

    def apply_patch(self, path: Path, patch: str) -> subprocess.CompletedProcess[str]:
        return _run(["git", "apply", "--index", "-"], cwd=path, timeout=30,
                    env=_local_process_env(), input_text=patch)

    def create_run(self, mission_id: str, *, source_release: dict[str, Any] | None = None) -> dict[str, Any]:
        """Copy the template, initialize git and return a baseline commit."""
        if not self.template_dir.is_dir():
            raise RuntimeError(f"Sandbox template missing: {self.template_dir}")
        target = self.runs_dir / mission_id
        if target.exists():
            raise RuntimeError(f"Sandbox already exists; refusing to overwrite {target}")
        if source_release:
            source = Path(source_release["path"])
            if self.template_digest(source) != source_release["tree_digest"]:
                raise RuntimeError("Accepted source release changed")
            cloned = self._git(target.parent, "clone", "--quiet", "--no-hardlinks", "--local", "--", str(source), str(target))
            if cloned.returncode:
                raise RuntimeError(f"Local release clone failed: {cloned.stderr.strip()}")
            self._git(target, "remote", "remove", "origin")
            if self._git(target, "rev-parse", "HEAD").stdout.strip() != source_release["commit"]:
                raise RuntimeError("Accepted release commit changed")
        else:
            shutil.copytree(self.template_dir, target, ignore=IGNORE)

        commands = [
            ("init", "-q"),
            ("config", "user.name", "Æion POC"),
            ("config", "user.email", "poc@local"),
            ("config", "commit.gpgsign", "false"),
            ("config", "core.hooksPath", os.devnull),
        ]
        if not source_release:
            commands.extend([("add", "-A"), ("commit", "-q", "-m", "baseline")])
        for argv in commands:
            result = self._git(target, *argv)
            if result.returncode != 0:
                raise RuntimeError(f"git {' '.join(argv)} failed: {result.stderr.strip()}")

        head = self._git(target, "rev-parse", "HEAD")
        return {
            "path": str(target),
            "base_commit": head.stdout.strip(),
            "created_at": time.time(),
            "template": str(self.template_dir),
        }

    def remove_run(self, mission_id: str) -> None:
        target = self.runs_dir / mission_id
        if target.exists():
            shutil.rmtree(target)

    # ------------------------------------------------------------------ diff
    def collect_changes(self, path: Path, *, base_commit: str | None = None) -> DiffResult:
        if not (path / ".git").exists():
            return DiffResult(available=False, error="Not a git sandbox")
        if base_commit is not None and not re.fullmatch(r"[0-9a-f]{40,64}", base_commit):
            return DiffResult(available=False, error="Invalid admitted Git base")
        revision = [base_commit] if base_commit is not None else []
        add = self._git(path, "add", "-A")
        if add.returncode != 0:
            return DiffResult(available=False, error=add.stderr.strip() or "git add failed")

        # Show renames as deletion + addition so a protected source path cannot
        # disappear behind an allowed destination name.
        names = self._git(path, "diff", "--cached", "--no-renames", "--name-status", *revision, "--")
        numstat = self._git(path, "diff", "--cached", "--no-renames", "--numstat", *revision, "--")
        patch = self._git(path, "diff", "--cached", "--no-renames", "--binary", *revision, "--")
        if patch.returncode != 0:
            return DiffResult(available=False, error=patch.stderr.strip() or "git diff failed")

        files: list[dict[str, Any]] = []
        for line in names.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                files.append({"status": parts[0], "path": parts[-1]})

        added = removed = 0
        for line in numstat.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                try:
                    added += int(parts[0])
                    removed += int(parts[1])
                except ValueError:
                    continue

        diff_text = patch.stdout
        line_count = sum(
            1
            for line in diff_text.splitlines()
            if (line.startswith("+") or line.startswith("-"))
            and not line.startswith(("+++", "---"))
        )
        return DiffResult(
            available=True,
            patch=diff_text,
            files=files,
            lines_added=added,
            lines_removed=removed,
            digest=_sha256(diff_text),
            error=None if diff_text.strip() else "No changes produced",
        )

    def file_hashes(self, path: Path, names: list[str] | None = None) -> dict[str, str]:
        output: dict[str, str] = {}
        candidates = names or [
            "app/pricing.py",
            "tests/test_pricing.py",
            "policy/policy.yaml",
            "AGENTS.md",
            ".github/workflows/release.yml",
        ]
        for name in candidates:
            target = path / name
            if target.is_file():
                output[name] = _sha256(target.read_bytes())
        return output

    def template_digest(self, path: Path | None = None) -> str:
        """Bind the entire trusted source tree, excluding generated artifacts."""
        root = path or self.template_dir
        names = []
        for target in root.rglob("*"):
            relative = target.relative_to(root)
            if any(part in {".git", "__pycache__", ".pytest_cache"} for part in relative.parts):
                continue
            if target.name in {"junit.xml", ".DS_Store"} or target.suffix == ".pyc":
                continue
            private = target.name == ".env" or (target.name.startswith(".env.") and target.name != ".env.example")
            if target.is_symlink() or private or "secrets" in relative.parts:
                raise RuntimeError(f"Private files and symlinks are not allowed in the trusted template: {relative}")
            if target.is_file():
                names.append(relative.as_posix())
        return _sha256(json.dumps(self.file_hashes(root, sorted(names)), sort_keys=True))

    # ----------------------------------------------------------------- tests
    @staticmethod
    def executor_contract(*, timeout: float = 180) -> dict[str, Any]:
        return {
            "schema": "aeion.pytest-executor/v1", "isolation": executor_identity(),
            "implementation_digest": EXECUTOR_CODE_DIGEST,
            "arguments": list(PYTEST_ARGUMENTS),
            "limits": {"output_bytes": MAX_TEST_OUTPUT_BYTES, "report_bytes": MAX_JUNIT_BYTES,
                       "report_cases": MAX_JUNIT_CASES, "timeout_seconds": timeout},
        }

    def run_tests(self, path: Path, *, timeout: float = 180, expected_executor: dict | None = None) -> TestResult:
        path = path.resolve()
        junit = path / "junit.xml"
        command = [sys.executable, *PYTEST_ARGUMENTS, str(junit)]
        isolation = {}
        executor = {}
        limits = {"output_bytes": MAX_TEST_OUTPUT_BYTES, "report_bytes": MAX_JUNIT_BYTES,
                  "report_cases": MAX_JUNIT_CASES, "timeout_seconds": timeout}
        try:
            executor = self.executor_contract(timeout=timeout)
            if expected_executor is not None and executor != expected_executor:
                raise OSError("Canonical test executor differs from the admitted identity; no test process started")
            if junit.exists() or junit.is_symlink():
                junit.unlink()
            with isolated_pytest(path, junit, command, _local_process_env()) as (command, env, isolation):
                if isolation["executor"] != executor["isolation"]:
                    raise OSError("Canonical test executor changed while preparing isolation; no test process started")
                result = _run(command, cwd=path, timeout=timeout, env=env, output_limit=MAX_TEST_OUTPUT_BYTES)
            if self.executor_contract(timeout=timeout) != executor:
                raise OSError("Canonical test executor changed during execution; results cannot establish acceptance")
        except _OutputLimitExceeded as error:
            return TestResult(available=True, error=str(error), stdout=error.stdout, stderr=error.stderr,
                              command=command, isolation=isolation, limits=limits, executor=executor)
        except subprocess.TimeoutExpired as error:
            return TestResult(
                available=True,
                exit_code=None,
                error=f"pytest timed out after {timeout}s",
                stdout=(error.stdout or "") if isinstance(error.stdout, str) else "",
                stderr=(error.stderr or "") if isinstance(error.stderr, str) else "",
                command=command,
                isolation=isolation,
                limits=limits,
                executor=executor,
            )
        except OSError as error:
            return TestResult(available=False, error=f"Isolated pytest verification failed: {error}", command=command, isolation=isolation, limits=limits, executor=executor)

        if "sandbox-exec:" in result.stderr and not junit.exists():
            return TestResult(available=False, exit_code=result.returncode,
                              error="OS isolation could not be applied; tests were not run unconfined",
                              stdout=result.stdout, stderr=result.stderr, command=command, isolation=isolation, limits=limits, executor=executor)

        parsed = self._parse_junit(junit)
        return TestResult(
            available=True,
            exit_code=result.returncode,
            total=parsed["total"],
            passed=parsed["passed"],
            failed=parsed["failed"],
            errors=parsed["errors"],
            skipped=parsed["skipped"],
            duration=parsed["duration"],
            stdout=result.stdout,
            stderr=result.stderr,
            cases=parsed["cases"],
            junit_path=str(junit),
            command=command,
            isolation=isolation,
            limits=limits,
            executor=executor,
            error=parsed.get("error"),
        )

    @staticmethod
    def _parse_junit(path: Path) -> dict[str, Any]:
        try:
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as report:
                info = os.fstat(report.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_JUNIT_BYTES:
                    raise ValueError("report must be a regular file within the byte limit")
                data = report.read(MAX_JUNIT_BYTES + 1)
            if len(data) > MAX_JUNIT_BYTES:
                raise ValueError("report exceeds the byte limit")
            text = data.decode("utf-8-sig")
            if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.I):
                raise ValueError("DTD/entity declarations are forbidden")
            root = ET.fromstring(text)
            if root.tag not in {"testsuite", "testsuites"}:
                raise ValueError("unexpected JUnit root")
            cases, identities = [], set()
            for case in root.iter("testcase"):
                identity = (case.get("classname", ""), case.get("name", ""))
                if not identity[1] or identity in identities or len(cases) >= MAX_JUNIT_CASES:
                    raise ValueError("missing/duplicate case identity or too many cases")
                identities.add(identity)
                seconds = float(case.get("time", "0") or 0)
                if not math.isfinite(seconds) or seconds < 0:
                    raise ValueError("invalid case duration")
                status = "passed"
                for tag, label in (("failure", "failed"), ("error", "error"), ("skipped", "skipped")):
                    if case.find(tag) is not None:
                        status = label
                        break
                cases.append({"name": identity[1], "classname": identity[0], "time": case.get("time", "0"), "status": status})
            suites = list(root.iter("testsuite"))
            duration = sum(float(suite.get("time", "0") or 0) for suite in suites)
            if not math.isfinite(duration) or duration < 0:
                raise ValueError("invalid suite duration")
        except (OSError, ValueError, ET.ParseError) as error:
            return {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0,
                    "duration": 0.0, "cases": [], "error": f"Invalid JUnit report: {error}"}
        failed = sum(1 for case in cases if case["status"] == "failed")
        errors = sum(1 for case in cases if case["status"] == "error")
        skipped = sum(1 for case in cases if case["status"] == "skipped")
        return {
            "total": len(cases),
            "passed": len(cases) - failed - errors - skipped,
            "failed": failed,
            "errors": errors,
            "skipped": skipped,
            "duration": duration,
            "cases": cases,
        }

    # ---------------------------------------------------------------- policy
    def read_policy(self) -> dict[str, Any]:
        path = self.template_dir / "policy" / "policy.yaml"
        return yaml.safe_load(path.read_text())

    def policy_digest(self) -> str:
        path = self.template_dir / "policy" / "policy.yaml"
        return _sha256(path.read_bytes())

    # -------------------------------------------------------------- baseline
    def baseline_check(self) -> dict[str, Any]:
        """Run the real test suite on a clean copy of the template."""
        work = Path(tempfile.mkdtemp(prefix="aeion-baseline-", dir=str(self.runs_dir.parent)))
        try:
            shutil.copytree(self.template_dir, work / "repo", ignore=IGNORE)
            tests = self.run_tests(work / "repo")
            return {
                "ok": tests.available,
                "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "template": str(self.template_dir),
                "tests": tests.to_dict(),
                "summary": tests.summary(),
            }
        finally:
            shutil.rmtree(work, ignore_errors=True)

    # --------------------------------------------------------------- ops/croa
    def run_ops_integrity(self) -> dict[str, Any]:
        script = settings.ops_dir / "tools" / "check_security_audit_upstream.py"
        if not script.is_file():
            return {"available": False, "error": f"OPS checker not found: {script}"}
        try:
            result = _run(
                [sys.executable, str(script), "--integrity-only"],
                cwd=settings.ops_dir,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return {"available": False, "error": str(error)}
        payload: dict[str, Any] = {}
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = {"raw": result.stdout[-4000:]}
        payload["available"] = True
        payload["exit_code"] = result.returncode
        payload["stderr"] = result.stderr[-2000:]
        return payload

    def run_croa_audit(self, system: str = "ParcelDesk POC") -> dict[str, Any]:
        script = settings.croa_dir / "tools" / "croa" / "audit.py"
        if not script.is_file():
            return {"available": False, "error": f"CROA CLI not found: {script}"}
        out_dir = settings.poc_dir / "runtime" / "croa" / "control"
        out_dir.mkdir(parents=True, exist_ok=True)
        packet = out_dir / "audit.json"
        report_json = out_dir / "report.json"
        report_md = out_dir / "report.md"

        init = _run([sys.executable, str(script), "init", "--system", system], cwd=settings.croa_dir)
        if init.returncode != 0:
            return {"available": True, "ok": False, "error": init.stderr.strip() or "CROA init failed"}
        packet.write_text(init.stdout)
        validate = _run([sys.executable, str(script), "validate", str(packet)], cwd=settings.croa_dir)
        report = _run(
            [sys.executable, str(script), "report", str(packet), "--format", "json"],
            cwd=settings.croa_dir,
        )
        markdown = _run(
            [sys.executable, str(script), "report", str(packet), "--format", "markdown"],
            cwd=settings.croa_dir,
        )
        if report.stdout:
            report_json.write_text(report.stdout)
        if markdown.stdout:
            report_md.write_text(markdown.stdout)

        parsed: dict[str, Any] = {}
        try:
            parsed = json.loads(report.stdout) if report.stdout else {}
        except json.JSONDecodeError:
            parsed = {}

        return {
            "available": True,
            "ok": validate.returncode == 0,
            "packet_valid": validate.returncode == 0,
            "packet": str(packet),
            "report_json": str(report_json),
            "report_md": str(report_md),
            "outcome": parsed.get("outcome", "unknown"),
            "conformance_claim": parsed.get("conformance_claim", False),
            "conformance_level": parsed.get("conformance_level"),
            "report_exit_code": report.returncode,
            "checks_total": len(parsed.get("checks", [])),
            "checks_unknown": sum(
                1 for check in parsed.get("checks", []) if check.get("status") == "unknown"
            ),
            "digest": _sha256(packet.read_bytes()),
            "note": "CROA validates packet structure and staged file digests only; it never awards conformance.",
        }


sandbox = SandboxManager()
