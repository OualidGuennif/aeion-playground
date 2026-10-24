"""Trusted launcher for tool-denied, credential-free disposable OpenCode roles."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from urllib.parse import urlparse
import uuid

import httpx

from .container_executor import docker_env

ROOT = Path(__file__).resolve().parents[1]
IMAGE_RECORD = ROOT / "runtime/agent-image.json"
NETWORK = "aeion-playground-inference"
GATEWAY_CONTAINER = "aeion-playground-inference-gateway-1"
LIMITS = {"memory_bytes": 1024 * 1024 * 1024, "cpus": 1, "pids": 128,
          "scratch_bytes": 256 * 1024 * 1024, "file_bytes": 4 * 1024 * 1024,
          "open_files": 256, "deadline_seconds": 180, "output_bytes": 2 * 1024 * 1024}


class GatewayClient:
    def __init__(self, url="http://127.0.0.1:8130", secret_path=None):
        target = urlparse(url)
        if target.scheme != "http" or target.hostname != "127.0.0.1" or target.username or target.password or target.path or target.query or target.fragment:
            raise OSError("Gateway administration requires an explicit loopback HTTP origin")
        self.url = url
        path = secret_path or ROOT / "runtime/secrets/gateway_admin"
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "r") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise OSError("Gateway capability must be a private operator-owned regular file")
            self.token = stream.read(128).strip()
        if not re.fullmatch(r"[0-9a-f]{64}", self.token):
            raise OSError("Invalid gateway admin capability")

    def request(self, method, path, payload=None):
        try:
            with httpx.Client(base_url=self.url, timeout=5, trust_env=False, follow_redirects=False) as client:
                with client.stream(method, path, json=payload, headers={"Authorization": "Bearer " + self.token}) as response:
                    if response.status_code != 200:
                        raise OSError(f"Gateway operation refused (HTTP {response.status_code}); no inference retry")
                    content = bytearray()
                    for chunk in response.iter_bytes(chunk_size=4096):
                        content.extend(chunk)
                        if len(content) > 16384:
                            raise OSError("Unbounded gateway administration response")
                    return json.loads(content)
        except (httpx.HTTPError, ValueError):
            raise OSError("Gateway administration unavailable; outcome must be inspected before retry") from None


def _docker(*args):
    try:
        result = subprocess.run(["docker", *args], env=docker_env(), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        raise OSError("Docker inference backend unavailable; no native fallback") from None
    if result.returncode:
        raise OSError("Qualified Docker inference resource unavailable; no native fallback")
    return result.stdout.strip()


def executor_identity(client=None):
    if IMAGE_RECORD.is_symlink() or not IMAGE_RECORD.is_file():
        raise OSError("Qualified OpenCode image missing; run scripts/build_agents.py")
    try:
        record = json.loads(IMAGE_RECORD.read_text())
        if record["schema"] != "aeion.agent-image/v1":
            raise ValueError()
        for key in ("image_id", "gateway_image_id"):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", record[key]) or _docker("image", "inspect", record[key], "--format", "{{.Id}}") != record[key]:
                raise ValueError()
        if _docker("inspect", GATEWAY_CONTAINER, "--format", "{{.Image}}") != record["gateway_image_id"]:
            raise OSError("Running inference gateway image differs from the qualified image")
        network = json.loads(_docker("network", "inspect", NETWORK))[0]
        if not network["Internal"] or network["EnableIPv6"] or network["Options"].get("com.docker.network.bridge.gateway_mode_ipv4") != "isolated":
            raise OSError("Inference network must have no host gateway or external route")
    except (ValueError, KeyError, TypeError, IndexError):
        raise OSError("Invalid qualified inference image/network identity") from None
    health = (client or GatewayClient()).request("GET", "/health")
    gateway_digest = hashlib.sha256((ROOT / "app/inference_gateway.py").read_bytes()).hexdigest()
    if health.get("schema") != "aeion.inference-gateway/v1" or health.get("implementation_digest") != gateway_digest:
        raise OSError("Gateway protocol/implementation drift; rebuild and readmit the mission")
    return {"backend": "docker-opencode/v1", "image_id": record["image_id"], "gateway_image_id": record["gateway_image_id"],
            "implementation_digest": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "gateway_implementation_digest": gateway_digest, "network_id": network["Id"], "network": NETWORK,
            "catalog_digest": hashlib.sha256((ROOT / "infra/opencode/catalog.json").read_bytes()).hexdigest(),
            "user": "65532:65532", "rootfs": "read-only", "capabilities": [], "mounts": [],
            "no_new_privileges": True, "logging": "none", "dns": "internal names only; external forwarding to dead loopback resolver", "limits": LIMITS.copy()}


def probe():
    try:
        client = GatewayClient()
        identity = executor_identity(client)
        health = client.request("GET", "/health")
        return {"ready": True, "runtime": "opencode-cli", "version": "1.19.4", "executor": identity,
                "providers_configured": health["providers_configured"], "auth": "credential mediator; roles receive single-use capabilities", "reason": None}
    except OSError as error:
        return {"ready": False, "runtime": "opencode-cli", "reason": str(error)}


@contextmanager
def isolated_opencode(*, prompt, model, role, run_id, timeout):
    if role not in {"forge", "sentinel"} or len(prompt.encode()) > 512 * 1024:
        raise OSError("Invalid bounded inference role/prompt")
    client = GatewayClient()
    identity = executor_identity(client)
    timeout = min(timeout, LIMITS["deadline_seconds"])
    lease = client.request("POST", "/admin/leases", {"run_id": run_id, "role": role, "model": model,
        "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest(), "expires": time.time() + timeout, "max_tokens": 8192})
    metadata = {"executor": identity, "lease_id": lease["id"], "state": "issued"}
    token = lease["token"]
    provider = model.split("/", 1)[0]
    agent = "aeion-patch-review" if role == "sentinel" else "aeion-patch"
    config = {"model": model, "small_model": model, "autoupdate": False, "share": "disabled", "permission": {"*": "deny"},
              "provider": {provider: {"options": {"baseURL": f"http://inference-gateway:8130/inference/{provider}/v1", "apiKey": token}}},
              "agent": {agent: {"mode": "primary", "model": model, "steps": 2, "permission": {"*": "deny"}}}}
    name = "aeion-" + role + "-" + uuid.uuid4().hex
    argv = ["docker", "run", "--rm", "--name", name, "--label", "aeion.playground.role=" + role,
        "--log-driver", "none", "--network", NETWORK, "--dns", "127.0.0.1", "--dns-option", "timeout:1", "--dns-option", "attempts:1", "--read-only", "--user", identity["user"],
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", str(LIMITS["memory_bytes"]),
        "--memory-swap", str(LIMITS["memory_bytes"]), "--cpus", str(LIMITS["cpus"]), "--pids-limit", str(LIMITS["pids"]),
        "--ulimit", f"fsize={LIMITS['file_bytes']}:{LIMITS['file_bytes']}",
        "--ulimit", f"nofile={LIMITS['open_files']}:{LIMITS['open_files']}",
        "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={LIMITS['scratch_bytes']},mode=1777",
        "--env", "OPENCODE_CONFIG_CONTENT", identity["image_id"], "run", prompt, "--model", model, "--agent", agent,
        "--title", run_id, "--auto", "--pure", "--format", "json", "--print-logs", "--log-level", "ERROR"]
    metadata["container"] = name
    try:
        yield argv, {**docker_env(), "OPENCODE_CONFIG_CONTENT": json.dumps(config)}, metadata, token
    finally:
        cleanup = None
        try:
            _docker("rm", "--force", name)
        except OSError:
            # rm is expected to report a missing --rm container after normal exit.
            try:
                if _docker("ps", "-aq", "--filter", "name=^/" + name + "$"):
                    cleanup = "Inference container cleanup failed; operator inspection required"
            except OSError:
                cleanup = "Inference container cleanup outcome unknown"
        # Atomic revoke only affects an unused lease. Consumed/unknown stays held.
        try:
            metadata.update(client.request("DELETE", "/admin/leases/" + lease["id"]))
        except OSError:
            metadata["state"] = "unknown"
        if cleanup:
            raise OSError(cleanup)
