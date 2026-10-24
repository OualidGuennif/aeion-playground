"""Real bounded pipes/process cleanup and untrusted JUnit regression cases."""
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from app import sandbox as module
from app.policy_engine import PolicyEngine
from app.sandbox import DiffResult, SandboxManager, TestResult as SuiteResult, _OutputLimitExceeded, _run
from test_missions import patch_runner, run_scenario
from test_native_isolation import checkout


def test_bounded_capture_drains_both_pipes_and_preserves_exit_code(tmp_path):
    script = "import os,sys; os.write(1,b'a'*40000); os.write(2,b'b'*40000); sys.exit(7)"
    result = _run([sys.executable, "-c", script], cwd=tmp_path, timeout=3, output_limit=80000)
    assert result.returncode == 7 and result.stdout == "a" * 40000 and result.stderr == "b" * 40000


@pytest.mark.parametrize("fd", [1, 2])
def test_output_overflow_stops_process_and_retains_only_bounded_diagnostics(tmp_path, fd):
    script = f"import os,time; os.write({fd},b'x'*100000); time.sleep(30)"
    started = time.monotonic()
    with pytest.raises(_OutputLimitExceeded) as caught:
        _run([sys.executable, "-c", script], cwd=tmp_path, timeout=5, output_limit=4096)
    assert len(caught.value.stdout.encode()) + len(caught.value.stderr.encode()) == 4096
    assert time.monotonic() - started < 3


def test_timeout_after_closed_pipes_retains_diagnostics_and_reaps(tmp_path):
    script = "import os,time; os.write(1,b'started'); os.close(1); os.close(2); time.sleep(30)"
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _run([sys.executable, "-c", script], cwd=tmp_path, timeout=.3, output_limit=4096)
    assert caught.value.stdout == "started"


def test_limit_kills_descendants_when_their_parent_already_exited(tmp_path):
    child = "import os,time; os.write(1,b'x'*100000); time.sleep(30)"
    script = (
        "import os,subprocess,sys; from pathlib import Path; "
        "Path('group.pid').write_text(str(os.getpid())); "
        f"subprocess.Popen([sys.executable,'-c',{child!r}]); "
        "os._exit(0)"
    )
    try:
        with pytest.raises(_OutputLimitExceeded):
            _run([sys.executable, "-c", script], cwd=tmp_path, timeout=3, output_limit=4096)
        group = int((tmp_path / "group.pid").read_text())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if not group_members(group):
                break
            time.sleep(.02)
        else:
            pytest.fail("A descendant survived process-group cleanup")
    finally:
        if (tmp_path / "group.pid").exists():
            for pid in group_members(int((tmp_path / "group.pid").read_text())):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def group_members(group):
    # macOS can return EPERM for killpg(..., 0) after the group disappeared.
    result = subprocess.run(["/bin/ps", "-axo", "pid=,pgid=,stat="], capture_output=True, text=True, timeout=3, check=True)
    return [int(parts[0]) for line in result.stdout.splitlines()
            if len(parts := line.split()) == 3 and parts[1] == str(group)]


def test_native_pytest_output_limit_is_explicit_and_ignores_a_forged_report(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "MAX_TEST_OUTPUT_BYTES", 4096)
    root = checkout(tmp_path, '''from pathlib import Path
import sys
def test_noise():
    Path(sys.argv[sys.argv.index('--junitxml')+1]).write_text('<testsuite><testcase name="fake-pass"/></testsuite>')
    print('x'*100000,flush=True)
''')
    result = SandboxManager(tmp_path / "runs").run_tests(root)
    assert result.error and "output exceeded 4096" in result.error
    assert result.total == 0 and result.exit_code is None
    assert result.limits["output_bytes"] == 4096
    assert "--capture=no" in result.executor["arguments"]


def test_engine_skips_review_and_sealing_when_capture_overflows(runtime, monkeypatch):
    monkeypatch.setattr(module, "MAX_TEST_OUTPUT_BYTES", 4096)
    def noisy_author(**kwargs):
        assert not kwargs.get("review"), "An aborted suite must not trigger paid review"
        answer = patch_runner(**kwargs)
        (kwargs["cwd"] / "tests/test_noise.py").write_text("def test_noise():\n    print('x'*100000,flush=True)\n")
        return answer
    mission = run_scenario(runtime, "PD01", noisy_author)
    assert mission["status"] == "blocked" and mission["gate_reason_code"] == "TEST_EXECUTION_ERROR"
    assert runtime.store.evidence_for(mission["id"])["status"] == "draft"


def parse(tmp_path, xml):
    report = tmp_path / "report.xml"
    report.write_bytes(xml.encode() if isinstance(xml, str) else xml)
    return SandboxManager._parse_junit(report)


def test_junit_counts_all_suites_including_failures_after_the_first(tmp_path):
    result = parse(tmp_path, '<testsuites><testsuite time=".1"><testcase name="one"/></testsuite>'
                            '<testsuite time=".2"><testcase name="two"><failure/></testcase></testsuite></testsuites>')
    assert (result["total"], result["passed"], result["failed"]) == (2, 1, 1)
    assert result["duration"] == pytest.approx(.3)


@pytest.mark.parametrize("xml", [
    '<wrong><testcase name="one"/></wrong>',
    '<testsuite><testcase name="one"/><testcase name="one"/></testsuite>',
    '<testsuite><testcase/></testsuite>',
    '<testsuite time="NaN"><testcase name="one"/></testsuite>',
    '<testsuite><testcase name="one" time="-1"/></testsuite>',
    '<testsuite><testcase name="one" time="Infinity"/></testsuite>',
    '<testsuite><testcase name="one" time="bad"/></testsuite>',
    '<!DOCTYPE testsuite [<!ENTITY fake "expanded">]><testsuite><testcase name="&fake;"/></testsuite>',
    b'\xff<testsuite/>',
    '<testsuite>',
])
def test_invalid_reports_cannot_supply_partial_passing_results(tmp_path, xml):
    result = parse(tmp_path, xml)
    assert result["total"] == result["passed"] == 0 and result["error"]


def test_report_bytes_and_case_counts_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "MAX_JUNIT_BYTES", 256)
    assert "byte limit" in parse(tmp_path, '<testsuite>' + ' ' * 256 + '</testsuite>')["error"]
    monkeypatch.setattr(module, "MAX_JUNIT_CASES", 2)
    assert "too many cases" in parse(tmp_path, '<testsuite><testcase name="a"/><testcase name="b"/><testcase name="c"/></testsuite>')["error"]


def test_a_fifo_report_is_refused_without_blocking(tmp_path):
    fifo = tmp_path / "report.xml"
    os.mkfifo(fifo)
    script = "from pathlib import Path; from app.sandbox import SandboxManager; import sys; r=SandboxManager._parse_junit(Path(sys.argv[1])); assert r['total']==0 and r['error']; print(r['error'])"
    result = subprocess.run([sys.executable, "-c", script, str(fifo)], capture_output=True, text=True, timeout=3)
    assert result.returncode == 0 and "regular file" in result.stdout


def test_gate_refuses_an_execution_error_even_if_counts_claim_success():
    result = PolicyEngine({}).evaluate(
        diff=DiffResult(available=True, files=[{"path": "tests/test_new.py"}], patch="patch", digest="digest"),
        tests=SuiteResult(available=True, exit_code=0, total=19, passed=19, error="Invalid JUnit report"),
        risk="low", cost_usd=0, review_verdict="OK", review_independent=True,
    )
    assert result.decision == "deny" and result.reason_code == "TEST_EXECUTION_ERROR"
