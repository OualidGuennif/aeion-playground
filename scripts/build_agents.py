#!/usr/bin/env python3
"""Build the pinned OpenCode role runtime; no provider credentials are used."""
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
subprocess.run(["docker", "build", "-f", "infra/opencode/Dockerfile", "-t", "aeion-playground-opencode:1", "."], cwd=root, check=True)
identity = subprocess.run(["docker", "image", "inspect", "aeion-playground-opencode:1", "--format", "{{.Id}}"], check=True, capture_output=True, text=True).stdout.strip()
subprocess.run(["docker", "build", "-f", "infra/gateway/Dockerfile", "-t", "aeion-playground-gateway:1", "."], cwd=root, check=True)
gateway = subprocess.run(["docker", "image", "inspect", "aeion-playground-gateway:1", "--format", "{{.Id}}"], check=True, capture_output=True, text=True).stdout.strip()
target = root / "runtime/agent-image.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps({"schema": "aeion.agent-image/v1", "image_id": identity, "gateway_image_id": gateway}, indent=2) + "\n")
print("OpenCode role image candidate recorded:", identity)
