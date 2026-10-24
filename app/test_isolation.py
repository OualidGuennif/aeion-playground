"""Mandatory, host-specific Seatbelt boundary for canonical ParcelDesk pytest.

This is not a VM. sandbox-exec and Apple's dyld support are deprecated/private
interfaces; unsupported hosts fail closed instead of running unconfined tests.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
DYLD_SUPPORT = Path("/System/Library/Sandbox/Profiles/dyld-support.sb")
PROFILE = '''(version 1)
(deny default)
(import "dyld-support.sb")
(allow process-exec (literal (param "PYTHON")) (literal (param "PYTHON_APP")))
; pytest's JUnit writer calls os.uname(); no general sysctl access is needed.
(allow sysctl-read (sysctl-name "kern.ostype") (sysctl-name "kern.hostname")
    (sysctl-name "kern.osrelease") (sysctl-name "kern.version") (sysctl-name "hw.machine"))
(allow file-read-metadata)
(allow file-read* (subpath "/System") (subpath "/usr/lib")
    (subpath (param "PYTHON_BASE")) (subpath (param "VENV"))
    (subpath (param "ROOT")) (subpath (param "SCRATCH"))
    (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random"))
(allow file-map-executable (subpath "/System") (subpath "/usr/lib")
    (subpath (param "PYTHON_BASE")) (subpath (param "VENV")))
(allow file-write* (subpath (param "SCRATCH")) (literal (param "JUNIT")) (literal "/dev/null"))
(deny file-read* file-write* (subpath (string-append (param "ROOT") "/.git")))
'''
ISOLATION_CODE_DIGEST = "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def executor_identity() -> dict:
    """Stable host identity; checkout and temporary paths are recorded separately."""
    if sys.platform != "darwin" or not SANDBOX_EXEC.is_file() or not DYLD_SUPPORT.is_file():
        raise OSError("Canonical pytest requires macOS sandbox-exec; no unconfined fallback")
    venv, base = Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()
    interpreter = Path(sys.executable).resolve()
    framework_app = base / "Resources/Python.app/Contents/MacOS/Python"
    if venv == base or not (venv / "pyvenv.cfg").is_file() or not framework_app.is_file():
        raise OSError("Canonical pytest requires the verified framework Python and project venv")
    try:
        pytest_version = version("pytest")
    except PackageNotFoundError as error:
        raise OSError("Canonical pytest requires an installed pytest distribution") from error
    def identity(path):
        return {"path": str(path), "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}
    return {
        "backend": "macos-seatbelt/v1",
        "profile_digest": "sha256:" + hashlib.sha256(PROFILE.encode()).hexdigest(),
        "dyld_support_digest": "sha256:" + hashlib.sha256(DYLD_SUPPORT.read_bytes()).hexdigest(),
        "isolation_code_digest": ISOLATION_CODE_DIGEST,
        "sandbox_exec": identity(SANDBOX_EXEC),
        "python": {"version": sys.version, "executable": identity(interpreter),
                   "framework": identity(base / "Python"), "application": identity(framework_app),
                   "venv": identity(venv / "pyvenv.cfg")},
        "pytest_version": pytest_version,
    }


@contextmanager
def isolated_pytest(root: Path, junit: Path, command: list[str], env: dict[str, str]):
    executor = executor_identity()
    venv, base = Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()
    interpreter = Path(sys.executable).resolve()
    framework_app = base / "Resources/Python.app/Contents/MacOS/Python"
    with tempfile.TemporaryDirectory(prefix="aeion-pytest-") as directory:
        control = Path(directory).resolve()
        scratch = control / "work"
        scratch.mkdir()
        profile = control / "profile.sb"
        profile.write_text(PROFILE)
        parameters = {
            "PYTHON": str(interpreter), "PYTHON_APP": str(framework_app),
            "PYTHON_BASE": str(base), "VENV": str(venv), "ROOT": str(root.resolve()),
            "SCRATCH": str(scratch), "JUNIT": str(junit.resolve()),
        }
        argv = [str(SANDBOX_EXEC), "-f", str(profile)]
        for key, value in parameters.items():
            argv.extend(["-D", f"{key}={value}"])
        restricted_env = {**env, "TMPDIR": str(scratch), "PYTHONDONTWRITEBYTECODE": "1"}
        metadata = {
            "backend": executor["backend"],
            "profile_digest": executor["profile_digest"],
            "dyld_support_digest": executor["dyld_support_digest"],
            "executor": executor,
            "parameters_digest": "sha256:" + hashlib.sha256(
                json.dumps(parameters, sort_keys=True).encode()
            ).hexdigest(),
            "required": True,
            "checkout": "read-only", "network": "denied", "child_processes": "denied",
            "writes": "temporary scratch, JUnit report and /dev/null only",
        }
        yield [*argv, *command], restricted_env, metadata
