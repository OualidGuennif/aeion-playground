import json

import pytest

from app.agent_runner import AgentRunner, _event_stats
from app.policy_engine import PolicyEngine
from app.sandbox import DiffResult, TestResult as SuiteResult


def test_readiness_checks_mixed_go_author_and_zen_reviewer_catalog(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import subprocess
    from app import agent_runner as module
    binary = tmp_path / "opencode"
    binary.touch()
    monkeypatch.setattr(module, "settings", SimpleNamespace(
        agent_runtime="opencode-cli", opencode_bin_path=lambda: binary,
        opencode_model="opencode-go/deepseek-v4.1-flash",
        opencode_review_model="opencode/glm-5.3-flash-free", opencode_api_key="test-only-key",
    ))
    monkeypatch.setattr(module, "ensure_config", lambda: None)
    runner = AgentRunner.__new__(AgentRunner)
    monkeypatch.setattr(runner, "_opencode_env", lambda: {})
    calls = []
    def cli(argv, **kwargs):
        calls.append(argv)
        text = "1.19.4" if "--version" in argv else "opencode-go/deepseek-v4.1-flash\nopencode/glm-5.3-flash-free\n"
        return subprocess.CompletedProcess(argv, 0, text, "")
    monkeypatch.setattr(module.subprocess, "run", cli)
    outcome = runner._probe_opencode()
    assert outcome["ready"] and [str(binary), "models"] in calls


@pytest.mark.parametrize("exit_code,passed,skipped", [(2, 19, 0), (None, 19, 0), (0, 18, 1), (0, 18, 0)])
def test_gate_rejects_incomplete_or_skipped_suite(exit_code, passed, skipped):
    gate = PolicyEngine({}).evaluate(
        diff=DiffResult(available=True, files=[{"path": "app/pricing.py"}], patch="fix", digest="digest"),
        tests=SuiteResult(available=True, exit_code=exit_code, total=19, passed=passed, skipped=skipped),
        risk="low", cost_usd=0, review_verdict="OK", review_independent=True,
    )
    assert gate.decision == "deny"
    assert gate.reason_code == "TESTS_INCOMPLETE"


@pytest.mark.parametrize("text", ["This is not OK", "No changes required, looks OK", '[]', '{"verdict":"OK"}', '{"verdict":["OK"],"reason":"bad shape"}'])
def test_review_does_not_infer_ok_from_prose(text):
    assert AgentRunner._parse_verdict(text)[0] == "UNPARSED"


def test_review_accepts_explicit_final_json():
    assert AgentRunner._parse_verdict('Read-only review.\n{"verdict":"OK","reason":"Bounded fix."}') == ("OK", "Bounded fix.")


def test_review_accepts_braces_inside_the_reason():
    text = 'Read-only review.\n{"verdict":"OK","reason":"Keeps {data.summary.openPulls} and href=\\"/pulls\\" unchanged."}'
    assert AgentRunner._parse_verdict(text) == (
        "OK", 'Keeps {data.summary.openPulls} and href="/pulls" unchanged.'
    )


def test_review_still_rejects_truncated_json_with_braces():
    text = '{"verdict":"OK","reason":"Keeps {data.summary.openPulls} but the JSON is cut'
    assert AgentRunner._parse_verdict(text)[0] == "UNPARSED"


@pytest.mark.parametrize("path", ["README.md", "pytest.ini", "junit.xml", "other/code.py"])
def test_successful_review_cannot_widen_the_task_edit_scope(path):
    gate = PolicyEngine({}).evaluate(
        diff=DiffResult(available=True, files=[{"path": path}], patch="change", digest="digest"),
        tests=SuiteResult(available=True, exit_code=0, total=19, passed=19),
        risk="low", cost_usd=0, review_verdict="OK", review_independent=True,
    )
    assert gate.reason_code == "OUT_OF_SCOPE"
    assert gate.decision == "deny"


def test_provider_error_event_fails_even_with_zero_cli_exit(tmp_path, monkeypatch):
    from app import agent_runner as module
    runner = AgentRunner()
    runner.log_dir = tmp_path
    events = [{"type": "error", "error": {"name": "APIError", "data": {"message": "HTTP 402 insufficient funds"}}}]
    monkeypatch.setattr(module, "_spawn", lambda *a, **kw: (0, "\n".join(map(json.dumps, events)), "", 1, None))
    outcome = runner._run_opencode(run_id="quota-test", prompt="test", prompt_path=tmp_path/"prompt", cwd=tmp_path, review=False, ready={})
    assert outcome.status == "failed"
    assert "402" in outcome.error


def test_truncated_session_is_not_completed(tmp_path, monkeypatch):
    from app import agent_runner as module
    runner = AgentRunner()
    runner.log_dir = tmp_path
    events = [{"type": "step_start", "sessionID": "session"}, {"type": "step_finish", "part": {"reason": "length"}}]
    monkeypatch.setattr(module, "_spawn", lambda *a, **kw: (0, "\n".join(map(json.dumps, events)), "", 1, None))
    assert runner._run_opencode(run_id="truncated", prompt="test", prompt_path=tmp_path/"prompt", cwd=tmp_path, review=False, ready={}).status == "failed"


def test_provider_quota_stops_a_hung_cli_early(tmp_path):
    import os
    import sys
    import time
    from app.agent_runner import _spawn
    started = time.monotonic()
    code, out, err, elapsed, failure = _spawn(
        [sys.executable, "-c", "import sys,time; print('Free usage limit reached',file=sys.stderr,flush=True); time.sleep(30)"],
        cwd=tmp_path, env=dict(os.environ), timeout=20, failure_phrases=("Free usage limit reached",),
    )
    assert "Free usage limit reached" in failure
    assert "Free usage limit reached" in err
    assert code != 0
    assert time.monotonic() - started < 5


def test_shutdown_cancels_real_agent_process_and_retains_its_output(tmp_path):
    import os
    import sys
    import time
    from concurrent.futures import ThreadPoolExecutor
    from app.agent_runner import _spawn
    runner = AgentRunner()
    ready = tmp_path / "ready"
    code = "from pathlib import Path; import time; print('started',flush=True); Path('ready').touch(); time.sleep(30)"
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(_spawn, [sys.executable, "-c", code], cwd=tmp_path, env=dict(os.environ), timeout=5, cancel_event=runner._cancelled)
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        runner.cancel_all()
        exit_code, stdout, _, _, error = pending.result(timeout=3)
    assert exit_code != 0
    assert "started" in stdout
    assert "cancelled" in error
    runner.resume()
    assert not runner._cancelled.is_set()


def test_timeout_terminates_descendants_after_the_cli_parent_has_exited(tmp_path):
    import os
    import signal
    import subprocess
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from app.agent_runner import _spawn
    marker = tmp_path / "group.pid"
    code = (
        "import os,sys,subprocess; from pathlib import Path; "
        "Path('group.pid').write_text(str(os.getpgrp())); "
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']); "
        "print('parent exited',flush=True)"
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(_spawn, [sys.executable, "-c", code], cwd=tmp_path, env=dict(os.environ), timeout=1)
        try:
            _, stdout, _, _, error = pending.result(timeout=4)
        finally:
            if marker.exists():
                try:
                    os.killpg(int(marker.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    # macOS can transfer a dead orphan/zombie to launchd before
                    # its process group disappears. Refuse to ignore a live member.
                    rows = subprocess.run(["ps", "-ax", "-o", "pgid=,stat="], capture_output=True, text=True, check=True).stdout.splitlines()
                    states = [row.split()[1] for row in rows if row.split() and row.split()[0] == marker.read_text()]
                    assert all(state.startswith("Z") for state in states)
    assert "parent exited" in stdout
    assert "timed out" in error


def test_agent_combined_output_is_bounded_and_flood_process_is_killed(tmp_path):
    import os
    import sys
    from app.agent_runner import _spawn
    code = "import os;\nwhile True:\n os.write(1,b'x'*1024);os.write(2,b'y'*1024)"
    exit_code, stdout, stderr, _, error = _spawn([sys.executable, "-c", code], cwd=tmp_path,
        env=dict(os.environ), timeout=5, output_limit=4096)
    assert exit_code != 0 and "output exceeded" in error
    assert len(stdout.encode()) + len(stderr.encode()) == 4096


def test_product_paths_are_admitted_when_the_task_names_them():
    diff = DiffResult(available=True, files=[{"path": "product/backend/src/main/java/com/aeion/parceldesk/dto/OrderView.java"}],
                      patch="fix", digest="digest")
    acceptance = SuiteResult(available=True, exit_code=0, total=12, passed=12, executor={"executor": "test"})
    decision = PolicyEngine({}).evaluate(
        diff=diff, tests=acceptance, risk="low", cost_usd=0.01,
        review_verdict="OK", review_independent=True,
        allowed_paths=["product/**"], acceptance=acceptance,
    )
    assert decision.decision == "allow", decision.reason_code


def test_the_review_prompt_uses_the_admitted_paths_not_a_fixed_legacy_scope():
    from app.agent_runner import REVIEW_TASK
    prompt = REVIEW_TASK.format(task="Allowed task paths: ['product/**'].", diff="diff --git a/product/x b/product/x")
    assert "admitted allowed paths" in prompt
    assert "product/**" in prompt
    assert "started-kilogram" not in prompt
    assert "machine-generated" in prompt and "authoritative" in prompt


def test_the_mission_prompt_does_not_hardcode_the_legacy_scope_or_test_command():
    from app.agent_runner import PROMPT_HEADER
    prompt = PROMPT_HEADER.format(
        role="an independent reviewer", mission_id="PD-999", title="Product change",
        scenario="agent", risk="low", task="Allowed task paths: ['product/**'].",
        memory="none", graph="none",
    )
    assert "Edit only app/**" not in prompt
    assert "python -m pytest" not in prompt
    assert "allowed paths named in the task" in prompt
    assert "If you are a reviewer" in prompt
