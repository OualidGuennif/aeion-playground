"""Real role/CLI/network qualification with a controlled local model response.

No provider call, provider credential, operator lease or human approval is used.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

import httpx
import pytest

from app import agent_runner, container_inference as container
from app.patch_protocol import PATCH_PROTOCOL

pytestmark = pytest.mark.skipif(os.environ.get("AEION_INFERENCE_TESTS") != "1", reason="Requires explicitly enabled Docker inference qualification")
ROOT = Path(__file__).parents[1]


def docker(*args):
    result = subprocess.run(["docker", *args], env=container.docker_env(), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Controlled Docker qualification failed: {args[:2]}"
    return result.stdout.strip()


@pytest.fixture
def qualified_gateway(tmp_path, monkeypatch):
    record = json.loads((ROOT / "runtime/agent-image.json").read_text())
    prefix = "aeion-inference-qual-" + uuid.uuid4().hex[:10]
    network, control, gateway_name = prefix + "-roles", prefix + "-control", prefix + "-gateway"
    secret = tmp_path / "test-admin"
    secret.write_text("a" * 64)
    secret.chmod(0o600)
    original_client = container.GatewayClient
    try:
        docker("network", "create", "--internal", "--opt", "com.docker.network.bridge.gateway_mode_ipv4=isolated", network)
        docker("network", "create", control)
        docker("create", "--name", gateway_name, "--network", control, "--publish", "127.0.0.1::8130", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "256m", "--pids-limit", "64",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777", "--env", "AEION_TEST_ADMIN=" + "a" * 64,
            "--mount", "type=bind,src=" + str(ROOT / "tests/fixtures/controlled_gateway.py") + ",dst=/fixture.py,readonly",
            "--entrypoint", "python", record["gateway_image_id"], "-I", "/fixture.py")
        docker("network", "connect", "--alias", "inference-gateway", network, gateway_name)
        docker("start", gateway_name)
        port = docker("port", gateway_name, "8130/tcp").split(":")[-1]
        url = "http://127.0.0.1:" + port
        for _ in range(50):
            try:
                with httpx.Client(trust_env=False, timeout=1) as client:
                    if client.get(url + "/health").status_code == 200:
                        break
            except httpx.HTTPError:
                pass
            time.sleep(.1)
        else:
            pytest.fail("Controlled inference fixture did not start")
        monkeypatch.setattr(container, "NETWORK", network)
        monkeypatch.setattr(container, "GATEWAY_CONTAINER", gateway_name)
        monkeypatch.setattr(container, "GatewayClient", lambda: original_client(url, secret))
        monkeypatch.setattr(agent_runner, "settings", replace(agent_runner.settings, inference_backend="docker"))
        yield original_client(url, secret)
    finally:
        subprocess.run(["docker", "rm", "--force", gateway_name], capture_output=True)
        subprocess.run(["docker", "network", "rm", network, control], capture_output=True)


def test_real_cli_uses_distinct_author_reviewer_containers_and_mediated_usage(qualified_gateway, tmp_path):
    runner = agent_runner.AgentRunner()
    runner.log_dir = tmp_path
    results = []
    for review, model in [(False, "opencode/deepseek-v4.1-flash-free"), (True, "opencode-go/glm-5.3-flash")]:
        result = runner.run(mission_id="controlled", agent_id="sentinel" if review else "forge", role="reviewer" if review else "author",
            title="Controlled CLI transport qualification", scenario="controlled", risk="low", task="Return the controlled JSON fixture.",
            cwd=tmp_path, review=review, protocol=PATCH_PROTOCOL, source_context="[]", model_override=model, timeout_seconds=40)
        assert result.status == "completed", result.error
        assert result.tokens_in == 30 and result.tokens_out == 10
        assert result.num_turns == 1 and result.raw["tool_calls"] == 0
        inference = result.to_dict()["raw"]["inference"]
        assert inference["state"] == "completed" and inference["usage"]["total_tokens"] == 40
        assert not docker("ps", "-aq", "--filter", "name=^/" + inference["container"] + "$")
        results.append(result)
    assert results[0].raw["inference"]["container"] != results[1].raw["inference"]["container"]
    assert results[0].raw["inference"]["lease_id"] != results[1].raw["inference"]["lease_id"]
    assert results[1].verdict == "OK"


def node_execution(execution, script):
    argv, env, metadata, _ = execution
    index = argv.index(metadata["executor"]["image_id"])
    return argv[:index] + ["--entrypoint", "node", argv[index], "-e", script], env, metadata


def test_actual_role_refuses_root_host_credentials_external_dns_network_and_admin(qualified_gateway, tmp_path):
    outside = tmp_path / "host-canary"
    outside.write_text("public host canary")
    script = r'''
const assert=require('assert'),fs=require('fs'),net=require('net'),dns=require('dns').promises;
const refused=(host,port)=>new Promise((resolve,reject)=>{
 const connection=net.connect({host,port});connection.setTimeout(1200);
 connection.on('connect',()=>{connection.destroy();reject(Error('network escaped'))});
 connection.on('error',()=>resolve());connection.on('timeout',()=>{connection.destroy();resolve()});
});
(async()=>{
 assert.equal(process.getuid(),65532);
 const status=fs.readFileSync('/proc/self/status','utf8');assert(status.includes('CapEff:\t0000000000000000'.replace('\\t','\t')));
 assert(status.includes('NoNewPrivs:\t1'.replace('\\t','\t')));
 assert.throws(()=>process.setuid(0));
 for(const path of ['/etc/hostname','/work/unexpected'])assert.throws(()=>fs.writeFileSync(path,'unexpected'));
 for(const path of ['/work/.git','/run/secrets','/run/provider-auth','/var/run/docker.sock',HOST_CANARY])assert(!fs.existsSync(path));
 assert(!('OPENCODE_API_KEY' in process.env));assert(!('AEION_TEST_ADMIN' in process.env));
 assert.equal(fs.readFileSync('/sys/fs/cgroup/memory.max','utf8').trim(),'1073741824');
 assert.equal(fs.readFileSync('/sys/fs/cgroup/pids.max','utf8').trim(),'128');
 const [quota,period]=fs.readFileSync('/sys/fs/cgroup/cpu.max','utf8').trim().split(' ');assert.equal(quota,period);
 assert.equal((await fetch('http://inference-gateway:8130/health')).status,200);
 assert.equal((await fetch('http://inference-gateway:8130/admin/leases')).status,403);
 await refused('192.0.2.1',443);
 await assert.rejects(dns.lookup('example.com'));
 try {const {address}=await dns.lookup('host.docker.internal');await refused(address,HOST_PORT)}catch(error){assert(['EAI_AGAIN','ENOTFOUND','ECONNREFUSED'].includes(error.code))}
 fs.writeFileSync('/tmp/scratch','bounded');assert.equal(fs.readFileSync('/tmp/scratch','utf8'),'bounded');
 console.log('physical boundary verified');
})().catch(error=>{console.error(error.message);process.exit(1)});
'''
    script = script.replace("HOST_CANARY", json.dumps(str(outside))).replace("HOST_PORT", str(httpx.URL(qualified_gateway.url).port))
    with container.isolated_opencode(prompt="controlled physical probe", model="opencode/deepseek-v4.1-flash-free", role="forge",
                                    run_id="probe-" + uuid.uuid4().hex, timeout=30) as execution:
        argv, env, metadata = node_execution(execution, script)
        code, stdout, stderr, _, error = agent_runner._spawn(argv, cwd=tmp_path, env=env, timeout=25)
    assert code == 0 and not error, (stdout, stderr, error)
    assert "physical boundary verified" in stdout
    assert metadata["state"] == "revoked" and outside.read_text() == "public host canary"


def test_deadline_removes_actual_role_container_and_revokes_unused_lease(qualified_gateway, tmp_path):
    with container.isolated_opencode(prompt="controlled timeout", model="opencode/deepseek-v4.1-flash-free", role="forge",
                                    run_id="timeout-" + uuid.uuid4().hex, timeout=5) as execution:
        argv, env, metadata = node_execution(execution, "setInterval(()=>{},1000)")
        code, _, _, _, error = agent_runner._spawn(argv, cwd=tmp_path, env=env, timeout=1)
    assert code != 0 and "timed out" in error
    assert not docker("ps", "-aq", "--filter", "name=^/" + metadata["container"] + "$")
    assert metadata["state"] == "revoked"


def test_real_cli_nonzero_exit_retains_mediated_quota_refusal(qualified_gateway, tmp_path):
    runner = agent_runner.AgentRunner()
    runner.log_dir = tmp_path
    result = runner.run(mission_id="controlled", agent_id="forge", role="author", title="Controlled refusal qualification",
        scenario="controlled", risk="low", task="controlled rate refusal", cwd=tmp_path, protocol=PATCH_PROTOCOL,
        source_context="[]", model_override="opencode/deepseek-v4.1-flash-free", timeout_seconds=30)
    assert result.status == "failed" and "Free usage limit reached" in result.error
    assert result.raw["inference"]["state"] == "refused"
