#!/usr/bin/env python3
"""Build the pinned worker image and record its immutable local image ID."""
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
subprocess.run(["docker", "build", "-f", "infra/worker/Dockerfile", "-t", "aeion-playground-worker:1", "."], cwd=root, check=True)
identity = subprocess.run(["docker", "image", "inspect", "aeion-playground-worker:1", "--format", "{{.Id}}"], check=True, capture_output=True, text=True).stdout.strip()
target = root / "runtime/executor-image.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps({"schema": "aeion.worker-image/v1", "image_id": identity}, indent=2) + "\n")
print("Qualified image candidate recorded:", identity)
