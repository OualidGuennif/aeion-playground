"""Stable owner label for containers launched by one host executor agent.

`docker run --rm` removes a worker after it exits, but it cannot remove a worker
whose agent was killed: the container keeps running and its Docker client is gone.
Every test and role container therefore carries a random owner label, persisted in
the agent's state directory, so the next agent process on that directory can remove
what the previous one left behind before it serves new work.

The owner is a local identifier, not a credential. It never enters a worker, a
prompt, a model call or a provider request.
"""
from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

ENV = "AEION_CONTAINER_OWNER"
LABEL = "aeion.playground.owner"
OWNER_FILE = "container-owner"
_PATTERN = re.compile(r"[0-9a-f]{32}")


def owner_from_env() -> str | None:
    """Return the current agent owner, or None outside an agent process."""
    value = os.environ.get(ENV, "")
    return value if _PATTERN.fullmatch(value) else None


def owner_label_args() -> list[str]:
    """Docker flags that bind a container to the current agent, if any."""
    owner = owner_from_env()
    return ["--label", f"{LABEL}={owner}"] if owner else []


def load_or_create_owner(root: Path) -> str:
    """Read the agent owner from its state directory, creating it once."""
    path = root / OWNER_FILE
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        value = secrets.token_hex(16)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return load_or_create_owner(root)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value + "\n")
        return value
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        value = stream.read(65).strip()
    if not _PATTERN.fullmatch(value):
        raise OSError("Executor owner file is invalid; operator inspection required")
    return value
