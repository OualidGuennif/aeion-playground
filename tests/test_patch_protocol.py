"""Controlled model streams; real worker apply, pytest, Gate and delivery."""
import hashlib
import json

import pytest

from app.agent_runner import AgentRunner
from app.opencode_config import PATCH_AGENT, PATCH_REVIEW_AGENT, build_config
from app.patch_protocol import PATCH_PROTOCOL, parse_patch, source_context
from test_product_loop import bootstrap, coverage_runner, proposal, start_product_task


@pytest.mark.parametrize("response", [
    'prefix {"patch":"diff --git x","summary":"x"}',
    '{"patch":"diff --git x","summary":"x","extra":true}',
    '{"patch":"diff --git x","summary":"x","summary":"y"}',
    '{"patch":"diff --git x","summary":[]}',
    json.dumps({"patch": "diff --git " + "x" * 24_000, "summary": "large"}),
    json.dumps({"patch": "diff --git \0", "summary": "nul"}),
])
def test_patch_shape_is_strict_and_bounded(response):
    with pytest.raises(ValueError):
        parse_patch(response)


def test_context_is_fixed_bounded_and_hash_bound(tmp_path):
    for name in ("app/pricing.py", "tests/test_pricing.py"):
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("# source\n")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets/private.txt").write_text("not in the prompt")
    files = json.loads(source_context(tmp_path))
    assert {item["path"] for item in files} == {"app/pricing.py", "tests/test_pricing.py"}
    assert all(item["sha256"] == hashlib.sha256(item["text"].encode()).hexdigest() for item in files)
    assert "not in the prompt" not in source_context(tmp_path)
    path.write_text("x" * 16_001)
    with pytest.raises(ValueError, match="bound"):
        source_context(tmp_path)
    path.unlink()
    path.symlink_to(tmp_path / "secrets/private.txt")
    with pytest.raises(ValueError, match="symlink"):
        source_context(tmp_path)


def test_proposal_roles_reserve_the_cli_summary_step_without_allowing_tools():
    for name in (PATCH_AGENT, PATCH_REVIEW_AGENT):
        agent = build_config()["agent"][name]
        assert agent["steps"] == 2 and agent["permission"] == {"*": "deny"}


def stream(text, *, steps=1, tool=False):
    return [*({"type": "step_start", "sessionID": "fixture-session"} for _ in range(steps)),
            *([{"type": "tool_use", "part": {"type": "tool"}}] if tool else []),
            {"type": "text", "part": {"text": text}},
            {"type": "step_finish", "part": {"reason": "stop", "tokens": {"input": 123, "output": 45}}}]


@pytest.mark.parametrize("mode", ["tool", "multi-step", "invalid-json", "provider-failure"])
def test_adapter_preserves_usage_but_refuses_invalid_proposals(tmp_path, monkeypatch, mode):
    from app import agent_runner as module
    runner = AgentRunner()
    runner.log_dir = tmp_path
    answer = proposal("tests/test_new.py", "def test_ok(): assert True\n").result_text
    events = stream("not JSON" if mode == "invalid-json" else answer,
                    steps=2 if mode == "multi-step" else 1, tool=mode == "tool")
    if mode == "provider-failure":
        events.append({"type": "error", "error": {"message": "Free usage limit reached"}})
    monkeypatch.setattr(module, "_spawn", lambda *a, **kw: (0, "\n".join(map(json.dumps, events)), "", 1, None))
    outcome = runner._run_opencode(run_id="fixture", prompt="test", prompt_path=tmp_path/"prompt",
                                   cwd=tmp_path, review=False, ready={}, protocol=PATCH_PROTOCOL, source_context="[]")
    assert outcome.status == "failed" and outcome.tokens_in == 123 and outcome.tokens_out == 45
    assert outcome.to_dict()["raw"]["protocol"] == PATCH_PROTOCOL


def test_real_adapter_contract_drives_worker_apply_tests_seal_and_delivery(runtime, monkeypatch, tmp_path):
    from app import agent_runner as module
    initial = bootstrap(runtime)
    runner = AgentRunner()
    runner.log_dir = tmp_path / "model-streams"
    runner.log_dir.mkdir()
    monkeypatch.setattr(runner, "probe", lambda: {"ready": True})
    calls = []
    def controlled_spawn(argv, **kwargs):
        calls.append(argv)
        prompt = argv[2]
        assert "Context recalled" not in prompt and "Graphify AST context" not in prompt
        assert "do not claim tests ran" in prompt
        assert "app/pricing.py" in prompt and "tests/test_pricing.py" in prompt
        if argv[argv.index("--agent")+1] == PATCH_AGENT:
            text = coverage_runner(protocol=PATCH_PROTOCOL).result_text
        else:
            assert "49 tests" in prompt and "Exact tested diff" in prompt
            text = json.dumps({"verdict": "OK", "reason": "The controlled patch covers all 30 boundaries."})
        return 0, "\n".join(map(json.dumps, stream(text))), "", 1, None
    monkeypatch.setattr(module, "_spawn", controlled_spawn)
    monkeypatch.setattr(runtime.module, "agent_runner", runner)
    mission = runtime.engine.launch_mission("PD04", request_id="strict-protocol", defer=True)
    assert json.loads(mission["contract_json"])["scenario"]["protocol"] == PATCH_PROTOCOL
    assert runtime.engine.dispatch_once()
    completed = runtime.store.mission(mission["id"])
    assert completed["status"] == "completed" and completed["outcome"] == "autonomous"
    receipt = json.loads(runtime.store.get_kv(f"delivery:{mission['id']}"))
    assert receipt["tests"]["passed"] == 49 and receipt["parent_release"]["commit"] == initial["commit"]
    assert len(calls) == 2
    packet = runtime.store.evidence_for(mission["id"])["payload"]["payload"]
    assert packet["contract"]["patch_protocol"] == PATCH_PROTOCOL
    for run in runtime.store.runs(mission["id"]):
        metadata = json.loads(run["raw_json"])
        assert metadata["protocol"] == PATCH_PROTOCOL and metadata["steps"] == 1 and metadata["tool_calls"] == 0
        assert len(metadata["source_digest"]) == 64


def test_route_override_and_deadline_reach_cli_without_automatic_title_call(tmp_path, monkeypatch):
    from app import agent_runner as module
    runner = AgentRunner()
    runner.log_dir = tmp_path
    observed = []
    def spawn(argv, **kwargs):
        observed.append((argv, kwargs["timeout"]))
        return 0, "\n".join(map(json.dumps, stream(proposal("tests/test_new.py", "def test_ok(): assert True\n").result_text))), "", 37, None
    monkeypatch.setattr(module, "_spawn", spawn)
    reply = runner._run_opencode(run_id="explicit-title", prompt="test", prompt_path=tmp_path/"prompt",
                                 cwd=tmp_path, review=False, ready={}, protocol=PATCH_PROTOCOL,
                                 model_override="opencode-go/glm-5.3-flash", timeout_seconds=12.5)
    argv, timeout = observed[0]
    assert timeout == 12.5 and argv[argv.index("--model") + 1] == "opencode-go/glm-5.3-flash"
    assert argv[argv.index("--title") + 1] == "explicit-title"
    assert reply.model == "opencode-go/glm-5.3-flash" and reply.raw["duration_ms"] == 37


@pytest.mark.parametrize("mode", ["mutation", "bad-json", "bad-diff", "failed-with-valid-patch"])
def test_worker_does_not_test_or_deliver_a_rejected_proposal(runtime, mode, monkeypatch):
    bootstrap(runtime)
    def refused(**kwargs):
        assert not kwargs.get("review")
        answer = coverage_runner(**kwargs)
        if mode == "mutation":
            (kwargs["cwd"] / "tests/unrequested.py").write_text("# unexpected tool effect\n")
        elif mode == "bad-json":
            answer.result_text = "not JSON"
        elif mode == "bad-diff":
            answer.result_text = json.dumps({"patch": "diff --git invalid\n", "summary": "broken"})
        else:
            answer.status, answer.error = "failed", "provider unavailable"
        return answer
    monkeypatch.setattr(runtime.sandbox, "run_tests", lambda *a, **kw: pytest.fail("Rejected proposal must not run tests"))
    mission = start_product_task(runtime, refused)
    assert mission["status"] == "blocked"
    assert mission["gate_reason_code"] == {"mutation": "PROPOSAL_MUTATION", "bad-json": "PATCH_PROTOCOL", "bad-diff": "MODEL_PATCH_INVALID", "failed-with-valid-patch": "AGENT_BLOCKED"}[mode]
    assert runtime.store.get_kv(f"delivery:{mission['id']}") is None
    assert not (runtime.sandbox.runs_dir / mission["id"] / "tests/test_boundaries_extended.py").exists()


def test_previously_admitted_editor_contract_is_not_silently_upgraded(runtime, monkeypatch):
    from types import SimpleNamespace
    bootstrap(runtime)
    mission = runtime.engine.launch_mission("PD04", request_id="legacy-editor", defer=True)
    admission = json.loads(mission["contract_json"])
    admission["scenario"].pop("protocol")
    runtime.store.update_mission(mission["id"], contract_json=json.dumps(admission))
    def legacy(**kwargs):
        assert kwargs["protocol"] is None
        return coverage_runner(**kwargs)
    monkeypatch.setattr(runtime.module, "agent_runner", SimpleNamespace(run=legacy))
    assert runtime.engine.dispatch_once()
    assert runtime.store.mission(mission["id"])["status"] == "completed"


@pytest.mark.parametrize("runtime_name,protocol", [("claude-cli", PATCH_PROTOCOL), ("opencode-cli", "unknown/v2")])
def test_unsupported_protocol_never_probes_or_falls_back(tmp_path, monkeypatch, runtime_name, protocol):
    from types import SimpleNamespace
    from app import agent_runner as module
    runner = AgentRunner()
    monkeypatch.setattr(module, "settings", SimpleNamespace(agent_runtime=runtime_name))
    monkeypatch.setattr(runner, "probe", lambda: pytest.fail("Unsupported protocol must stop before probing"))
    outcome = runner.run(mission_id="fixture", agent_id="forge", role="author", title="test",
                         scenario="agent", risk="low", task="test", cwd=tmp_path, protocol=protocol)
    assert outcome.status == "blocked" and "no runtime fallback" in outcome.error


def test_product_source_context_is_bounded_to_product_sources(tmp_path):
    names = (
        "product/README.md",
        "product/backend/src/main/java/com/aeion/parceldesk/dto/OrderView.java",
        "product/frontend/routes/index.tsx",
    )
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("source\n")
    files = json.loads(source_context(tmp_path, names))
    assert [item["path"] for item in files] == list(names)
    with pytest.raises(ValueError, match="unsupported type"):
        source_context(tmp_path, ("product/secrets.txt",))
    with pytest.raises(ValueError, match="product source"):
        source_context(tmp_path, ("policy/policy.yaml",))
