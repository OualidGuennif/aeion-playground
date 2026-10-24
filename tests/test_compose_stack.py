"""Opt-in qualification of the actual stack, including a restart and restore.

Only throwaway databases named aeion_qual_* are created/dropped. Never use this
against an unrelated Compose project. No operator/provider credential is printed.
"""
import json
import os
from pathlib import Path
import subprocess
import uuid

import httpx
import pytest

pytestmark = pytest.mark.skipif(os.environ.get("AEION_STACK_TESTS") != "1", reason="Requires explicitly started local Compose stack")
ROOT = Path(__file__).parents[1]


def docker(*args, input=None):
    result = subprocess.run(["docker", *args], cwd=ROOT, input=input, capture_output=True, timeout=180)
    # Do not expose database contents or service logs in assertion failures.
    assert result.returncode == 0, f"Docker qualification command failed ({args[0]})"
    return result.stdout


def sql(database, statement):
    return docker("compose", "exec", "-T", "--user", "postgres", "postgres", "psql", "-U", "paperclip", "-d", database,
                  "-v", "ON_ERROR_STOP=1", "-Atc", statement).decode().strip()


def test_real_stack_health_authentication_and_restricted_mounts():
    with httpx.Client(base_url="http://127.0.0.1:3101", trust_env=False, timeout=5) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["deploymentMode"] == "authenticated"
        assert client.get("/api/companies").status_code in {401, 403}
        assert client.get("/").status_code == 200
        # Complete schema, invalid password: exercises the origin middleware
        # without creating an operator account or claiming ownership.
        invalid_signup = {"name": "Origin validation probe", "email": "origin-probe@example.invalid", "password": "x"}
        local = client.post("/api/auth/sign-up/email", headers={"Origin": "http://127.0.0.1:3101"}, json=invalid_signup)
        assert local.status_code == 400 and local.json()["code"] == "PASSWORD_TOO_SHORT"
        foreign = client.post("/api/auth/sign-up/email", headers={"Origin": "https://foreign.example"}, json=invalid_signup)
        assert foreign.status_code == 403 and foreign.json()["code"] == "INVALID_ORIGIN"
    for service in ("paperclip", "postgres"):
        identity = docker("compose", "ps", "-q", service).decode().strip()
        # Select fields explicitly; never inspect or print the secret environment.
        config = json.loads(docker("inspect", identity, "--format", "{{json .HostConfig}}"))
        assert config["ReadonlyRootfs"] and config["Privileged"] is False
        assert "ALL" in config["CapDrop"]
        assert config["Memory"] > 0 and config["PidsLimit"] > 0 and config["NanoCpus"] > 0
        assert "no-new-privileges:true" in config["SecurityOpt"]
        assert config["LogConfig"]["Config"] == {"max-file": "2", "max-size": "1m"}
        assert not any("docker.sock" in mount for mount in config.get("Binds") or [])
        if service == "paperclip":
            assert config["PortBindings"] == {"3101/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3101"}]}
        else:
            assert not config["PortBindings"]


def test_database_survives_restart_and_backup_restores():
    source = "aeion_qual_" + uuid.uuid4().hex[:12]
    restored = "aeion_qual_" + uuid.uuid4().hex[:12]
    try:
        sql("postgres", f"CREATE DATABASE {source}")
        sql(source, "CREATE TABLE checkpoint(value text NOT NULL); INSERT INTO checkpoint VALUES ('verified')")
        backup = docker("compose", "exec", "-T", "--user", "postgres", "postgres", "pg_dump", "-U", "paperclip", "-Fc", source)
        assert len(backup) > 100
        docker("compose", "restart", "postgres", "paperclip")
        docker("compose", "up", "-d", "--wait", "--wait-timeout", "120", "postgres", "paperclip")
        assert sql(source, "SELECT value FROM checkpoint") == "verified"
        sql("postgres", f"CREATE DATABASE {restored}")
        docker("compose", "exec", "-T", "--user", "postgres", "postgres", "pg_restore", "-U", "paperclip", "-d", restored,
               "--exit-on-error", input=backup)
        assert sql(restored, "SELECT value FROM checkpoint") == "verified"
    finally:
        for database in (source, restored):
            sql("postgres", f"DROP DATABASE IF EXISTS {database} WITH (FORCE)")


def test_cockpit_has_no_socket_or_host_home_and_serves_both_surfaces():
    identity = docker("compose", "ps", "-q", "cockpit").decode().strip()
    if not identity:
        pytest.skip("Cockpit service is not part of this stack qualification run")
    config = json.loads(docker("inspect", identity, "--format", "{{json .HostConfig}}"))
    assert config["ReadonlyRootfs"] and config["Privileged"] is False
    assert "ALL" in config["CapDrop"]
    assert config["Memory"] > 0 and config["PidsLimit"] > 0 and config["NanoCpus"] > 0
    assert "no-new-privileges:true" in config["SecurityOpt"]
    assert config["PortBindings"] == {"8124/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8124"}]}
    binds = config.get("Binds") or []
    sources = [mount.split(":", 1)[0].rstrip("/") for mount in binds]
    assert not any("docker.sock" in mount for mount in binds)
    assert any(source.endswith("runtime/cockpit") for source in sources)
    assert any(source.endswith("runtime/secrets/executor_agent") for source in sources)
    assert all(source.endswith(("runtime/cockpit", "runtime/secrets/executor_agent", "config")) for source in sources)
    with httpx.Client(base_url="http://127.0.0.1:8124", trust_env=False, timeout=10) as client:
        assert client.get("/").status_code == 200
        assert client.get("/parceldesk").status_code == 200
        assert client.get("/api/health").json()["ok"] is True
