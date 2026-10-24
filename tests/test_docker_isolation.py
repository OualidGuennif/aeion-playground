"""Real Docker boundaries with public canaries, never provider credentials."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from app import container_executor
from app.sandbox import SandboxManager
from test_product_loop import bootstrap, start_product_task

pytestmark = pytest.mark.skipif(os.environ.get("AEION_DOCKER_TESTS") != "1", reason="Requires explicitly enabled Docker qualification")


def checkout(tmp_path, text):
    root = tmp_path / "checkout"
    (root / "tests").mkdir(parents=True)
    (root / "app").mkdir()
    (root / "app/source.txt").write_text("public source")
    (root / "pytest.ini").write_text("[pytest]\npythonpath = .\ntestpaths = tests\n")
    (root / "tests/test_boundary.py").write_text(text)
    return root


def test_real_worker_refuses_host_source_root_network_and_privilege_access(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "test-only-public-sentinel")
    outside = tmp_path / "public-host-canary"
    outside.write_text("public host value")
    root = checkout(tmp_path, f'''
import errno,os,resource,socket
from pathlib import Path
import pytest

def test_nonroot_and_no_privileges():
    assert os.getuid()==65532
    status=Path('/proc/self/status').read_text()
    assert 'CapEff:\\t0000000000000000' in status
    assert 'NoNewPrivs:\\t1' in status
    with pytest.raises(PermissionError): os.setuid(0)

@pytest.mark.parametrize('name',['/work/app/source.txt','/opt/aeion/run_tests.py','/etc/hostname'])
def test_writes_refused(name):
    with pytest.raises(OSError): Path(name).write_text('unexpected')

def test_no_host_mount_or_credentials():
    assert not Path({str(outside)!r}).exists()
    assert not Path('/work/.git').exists()
    assert not Path('/work/policy').exists()
    assert not Path('/var/run/docker.sock').exists()
    assert not Path('/run/secrets').exists()
    assert 'OPENCODE_API_KEY' not in os.environ
    assert Path('/work/app/source.txt').read_text()=='public source'

def test_network_has_no_route():
    with socket.socket() as connection:
        connection.settimeout(1)
        with pytest.raises(OSError) as failure: connection.connect(('192.0.2.1',443))
        assert failure.value.errno == errno.ENETUNREACH

def test_actual_resource_limits():
    group=Path('/sys/fs/cgroup')
    assert int((group/'memory.max').read_text())==268435456
    assert int((group/'pids.max').read_text())==64
    quota,period=map(int,(group/'cpu.max').read_text().split()); assert quota==period
    assert resource.getrlimit(resource.RLIMIT_FSIZE)==(2097152,2097152)
    assert resource.getrlimit(resource.RLIMIT_NOFILE)==(128,128)
    info=os.statvfs('/tmp'); assert info.f_blocks*info.f_frsize<=67108864
    scratch=Path('/tmp/public-scratch');scratch.write_text('ok');assert scratch.read_text()=='ok'
''')
    (root / ".git").mkdir()
    (root / ".git/config").write_text("public Git canary")
    result = SandboxManager(tmp_path / "runs").run_tests(root)
    assert result.available and not result.error and result.passed == result.total == 7, result.to_dict()
    assert result.isolation["backend"] == "docker-pytest/v1"
    assert (root / "app/source.txt").read_text() == "public source"
    assert outside.read_text() == "public host value"
    leftover = subprocess.run(["docker", "ps", "-aq", "--filter", "name="+result.isolation["container"]], capture_output=True, text=True, check=True)
    assert not leftover.stdout.strip()


def test_absent_qualified_image_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(container_executor, "IMAGE_RECORD", tmp_path / "missing.json")
    root = checkout(tmp_path, "def test_never(): assert True\n")
    result = SandboxManager(tmp_path / "runs").run_tests(root)
    assert not result.available and "Qualified Docker worker image missing" in result.error
    assert result.total == 0


def test_snapshot_refuses_symlink_before_container_execution(tmp_path):
    root = checkout(tmp_path, "def test_never(): assert True\n")
    public = tmp_path / "outside.py"
    public.write_text("public canary")
    (root / "tests/test_link.py").symlink_to(public)
    result = SandboxManager(tmp_path / "runs").run_tests(root)
    assert not result.available and "refuses symlinks" in result.error


def test_deadline_kills_container_and_rejects_result(tmp_path):
    root = checkout(tmp_path, "import time\ndef test_timeout(): time.sleep(30)\n")
    result = SandboxManager(tmp_path / "runs").run_tests(root, timeout=.8)
    assert result.error and result.total == 0
    name = result.isolation["container"]
    assert not subprocess.run(["docker", "ps", "-aq", "--filter", "name="+name], capture_output=True, text=True, check=True).stdout.strip()


def test_real_product_loop_uses_distinct_containers_and_frozen_image(runtime):
    bootstrap(runtime)
    mission = start_product_task(runtime)
    assert mission["status"] == "completed" and mission["human_approval"] is None
    worker = json.loads(mission["tests_json"])
    receipt = json.loads(runtime.store.get_kv(f"delivery:{mission['id']}"))
    assert worker["passed"] == receipt["tests"]["passed"] == 49
    assert worker["isolation"]["container"] != receipt["test_isolation"]["container"]
    assert worker["executor"] == receipt["test_executor"]
