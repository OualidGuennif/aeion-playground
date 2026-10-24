"""Owner-controlled, non-secret routing settings. Invalid configuration fails closed."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

ROUTING_PATH = Path(__file__).resolve().parent.parent / "config/model-routing.json"
EXAMPLE_PATH = ROUTING_PATH.with_name("model-routing.example.json")
KNOWN_MODELS = {
    "opencode/deepseek-v4.1-flash-free", "opencode/glm-5.3-flash-free",
    "opencode-go/deepseek-v4.1-flash", "opencode-go/glm-5.3-flash",
}


def load_routing_policy() -> dict | None:
    path = ROUTING_PATH if ROUTING_PATH.exists() else EXAMPLE_PATH
    if path.is_symlink():
        raise ValueError("Routing settings must not be a symlink")
    data = json.loads(path.read_text())
    keys = {"schema", "budget_usd", "authorization_expires_at", "default_author", "default_reviewer",
            "agent_timeout_seconds", "cooldown_seconds", "history_window", "roles"}
    if not isinstance(data, dict) or set(data) != keys or data["schema"] != "aeion.model-routing/v1":
        raise ValueError("Invalid routing policy schema")
    budget = data["budget_usd"]
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget) or not 0 < budget <= 100:
        raise ValueError("Routing budget must be positive and within the authorized 100 USD")
    for key, low, high in (("agent_timeout_seconds", 1, 900), ("cooldown_seconds", 60, 86400), ("history_window", 3, 100)):
        if type(data[key]) is not int or not low <= data[key] <= high:
            raise ValueError(f"Invalid routing bound: {key}")
    if not isinstance(data["authorization_expires_at"], str):
        raise ValueError("Routing authorization expiry must be an ISO timestamp")
    deadline = datetime.fromisoformat(data["authorization_expires_at"].replace("Z", "+00:00"))
    if deadline.tzinfo is None:
        raise ValueError("Routing authorization expiry needs a timezone")
    if any(not isinstance(data[key], str) or data[key] not in KNOWN_MODELS
           for key in ("default_author", "default_reviewer")):
        raise ValueError("Unknown default model")
    if not isinstance(data["roles"], dict) or set(data["roles"]) != {"forge", "sentinel"}:
        raise ValueError("Both routing roles are required")
    for role in data["roles"].values():
        if (not isinstance(role, dict) or set(role) != {"preferred_family", "models"}
                or not isinstance(role["preferred_family"], str) or role["preferred_family"] not in {"deepseek", "glm"}
                or not isinstance(role["models"], list) or not role["models"]
                or any(not isinstance(model, str) for model in role["models"])
                or len(role["models"]) != len(set(role["models"]))
                or any(model not in KNOWN_MODELS for model in role["models"])):
            raise ValueError("Invalid authorized role pool")
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return {**data, "digest": "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()}


def operator_value(key: str, default):
    policy = load_routing_policy()
    return policy[key] if policy is not None else default
