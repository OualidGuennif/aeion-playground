"""Actual host Seatbelt tests, with public canaries only and no model calls."""
import hashlib
import os
import socket
import subprocess
from pathlib import Path

import pytest

from app.sandbox import SandboxManager
from app.test_isolation import PROFILE
from test_missions import patch_runner, run_scenario

pytestmark = pytest.mark.skipif(os.environ.get("AEION_TEST_BACKEND") != "macos", reason="macOS-specific boundary qualification")


def checkout(tmp_path, code):
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "tests").mkdir()
    (root / "pytest.ini").write_text("[pytest]\npythonpath = .\ntestpaths = tests\n")
    (root / "tests/test_native.py").write_text(code)
    return root


def test_actual_native_boundary_allows_pytest_and_refuses_escape(tmp_path, monkeypatch):
    outside = tmp_path / "public-canary.txt"
    outside.write_text("public test canary")
    monkeypatch.setenv("OPENCODE_API_KEY", "test-only-sentinel")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        port = listener.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass  # Proves the denial below is not just an unavailable endpoint.
        root = checkout(tmp_path, f'''
from pathlib import Path
import os,socket,subprocess,sys,ctypes,errno
import pytest
OUTSIDE=Path({str(outside)!r})
def test_read_source():
    assert Path('source.txt').read_text() == 'checkout source'
@pytest.mark.parametrize('action', ['outside_read','outside_write','source_write','git_read','git_write','network','child','python_child','fork'])
def test_denied_operations(action):
    actions={{
        'outside_read': lambda: OUTSIDE.read_text(),
        'outside_write': lambda: OUTSIDE.write_text('unexpected'),
        'source_write': lambda: Path('source.txt').write_text('unexpected'),
        'git_read': lambda: Path('.git/config').read_text(),
        'git_write': lambda: Path('.git/config').write_text('unexpected'),
        'network': lambda: socket.create_connection(('127.0.0.1',{port}),timeout=2),
        'child': lambda: subprocess.run(['/usr/bin/true'],check=True),
        'python_child': lambda: subprocess.run([sys.executable,'-c','print(1)'],check=True),
        'fork': os.fork,
    }}
    with pytest.raises(PermissionError): actions[action]()
def test_scratch(tmp_path):
    path=tmp_path/'allowed.txt'; path.write_text('ok'); assert path.read_text()=='ok'
    path.unlink(); path.symlink_to(OUTSIDE)
    with pytest.raises(PermissionError): path.read_text()
    with pytest.raises(PermissionError): path.write_text('unexpected')
def test_no_credentials():
    assert 'HOME' not in os.environ and 'OPENCODE_API_KEY' not in os.environ
def test_stdin_closed():
    assert os.read(0,1) == b''
def test_non_allowlisted_sysctl_denied():
    libc=ctypes.CDLL(None,use_errno=True)
    value=ctypes.c_int(); size=ctypes.c_size_t(ctypes.sizeof(value))
    status=libc.sysctlbyname(b'hw.ncpu',ctypes.byref(value),ctypes.byref(size),None,0)
    assert status == -1 and ctypes.get_errno() == errno.EPERM
''')
        (root / "source.txt").write_text("checkout source")
        (root / ".git").mkdir()
        (root / ".git/config").write_text("public git canary")
        result = SandboxManager(tmp_path / "runs").run_tests(root)
    assert result.available and result.exit_code == 0, result.to_dict()
    assert result.total == result.passed == 14
    assert result.command[0] == "/usr/bin/sandbox-exec"
    assert result.isolation["required"] is True
    assert result.isolation["profile_digest"] == "sha256:" + hashlib.sha256(PROFILE.encode()).hexdigest()
    assert len(result.isolation["dyld_support_digest"]) == 71
    assert outside.read_text() == "public test canary"
    assert (root / "source.txt").read_text() == "checkout source"
    assert (root / ".git/config").read_text() == "public git canary"
    assert not Path(result.command[2]).parent.exists()  # Scratch and profile are cleaned.


def test_missing_backend_fails_closed_before_any_test_process(tmp_path, monkeypatch):
    from app import sandbox as module, test_isolation
    monkeypatch.setattr(test_isolation, "SANDBOX_EXEC", tmp_path / "missing")
    monkeypatch.setattr(module, "_run", lambda *a, **kw: pytest.fail("No unconfined fallback"))
    root = checkout(tmp_path, "def test_never(): assert True\n")
    result = SandboxManager(tmp_path / "runs").run_tests(root)
    assert not result.available and "no unconfined fallback" in result.error


def test_rejected_sandbox_apply_never_retries_unconfined(tmp_path, monkeypatch):
    from app import sandbox as module
    calls = []
    def rejected(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 71, "", "sandbox-exec: sandbox_apply: Operation not permitted")
    monkeypatch.setattr(module, "_run", rejected)
    root = checkout(tmp_path, "def test_never(): assert True\n")
    result = SandboxManager(tmp_path / "runs").run_tests(root)
    assert len(calls) == 1 and calls[0][0] == "/usr/bin/sandbox-exec"
    assert not result.available and "not run unconfined" in result.error


def test_real_timeout_cleans_the_restricted_process_and_scratch(tmp_path):
    root = checkout(tmp_path, "import time\ndef test_timeout(): time.sleep(30)\n")
    result = SandboxManager(tmp_path / "runs").run_tests(root, timeout=.5)
    assert result.exit_code is None and "timed out" in result.summary()
    assert not Path(result.command[2]).parent.exists()


def test_report_symlink_is_not_followed_by_the_worker(tmp_path):
    outside = tmp_path / "public-report.xml"
    outside.write_text('<testsuite tests="99" failures="0"/>')
    report = tmp_path / "junit.xml"
    report.symlink_to(outside)
    assert SandboxManager._parse_junit(report)["total"] == 0


def test_existing_diff_guard_still_rejects_a_change_by_an_external_actor(runtime, monkeypatch):
    original = runtime.sandbox.run_tests
    def concurrent_change(path, **kwargs):
        result = original(path, **kwargs)
        source = path / "app/pricing.py"
        source.write_text(source.read_text() + "\n# simulated external concurrent change\n")
        return result
    monkeypatch.setattr(runtime.sandbox, "run_tests", concurrent_change)
    def no_review(**kwargs):
        assert not kwargs.get("review")
        return patch_runner(**kwargs)
    mission = run_scenario(runtime, "PD01", no_review)
    assert mission["status"] == "blocked" and mission["gate_reason_code"] == "TESTS_CHANGED_PATCH"
