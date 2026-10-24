"""Strict, generated OpenCode configuration for sandboxed missions.

The configuration is real: OpenCode reads it from
`runtime/opencode/config/opencode/opencode.json`. It is regenerated from the
workspace `.env` on every run so the selected models and the permission wall
cannot drift silently.

OpenCode permission patterns are simple wildcards (`*`, `?`), not shell
globstars, and `edit` is matched against the path the tool receives (for example
`app/pricing.py`). The allow rules therefore use `app/*` and `tests/*`; the old
`**/app/**` form silently matched only the catch-all deny. `external_directory`
is denied, so a pattern cannot be used to escape the mission checkout.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import settings

FORGE_AGENT = "aeion-forge"
REVIEW_AGENT = "aeion-review"
PATCH_AGENT = "aeion-patch"
PATCH_REVIEW_AGENT = "aeion-patch-review"

# Read-only inspection commands the agent may run. Everything else is denied.
_READ_COMMANDS = {
    "ls": "allow",
    "ls *": "allow",
    "pwd": "allow",
    "cat app/*": "allow",
    "cat app/**": "allow",
    "cat tests/*": "allow",
    "cat tests/**": "allow",
    "cat AGENTS.md": "allow",
    "cat README.md": "allow",
    "cat policy/policy.yaml": "allow",
    "cat pytest.ini": "allow",
    "head * app/*": "allow",
    "head * app/**": "allow",
    "head * tests/*": "allow",
    "head * tests/**": "allow",
    "tail * app/*": "allow",
    "tail * app/**": "allow",
    "tail * tests/*": "allow",
    "tail * tests/**": "allow",
    "grep * app/*": "allow",
    "grep * app/**": "allow",
    "grep * tests/*": "allow",
    "grep * tests/**": "allow",
    "rg * app/*": "allow",
    "rg * app/**": "allow",
    "rg * tests/*": "allow",
    "rg * tests/**": "allow",
}

_GIT_READ = {
    "git status": "allow",
    "git status *": "allow",
    "git diff": "allow",
    "git diff *": "allow",
    "git log": "allow",
    "git log *": "allow",
    "git show": "allow",
    "git show *": "allow",
}

_FORGE_BASH = {
    "*": "deny",
    **_GIT_READ,
    "python -m pytest": "allow",
    "python -m pytest *": "allow",
    "python3 -m pytest": "allow",
    "python3 -m pytest *": "allow",
    "pytest": "allow",
    "pytest *": "allow",
    "python -m py_compile app/*": "allow",
    "python3 -m py_compile app/*": "allow",
    **_READ_COMMANDS,
}

_REVIEW_BASH = {
    "*": "deny",
    **_GIT_READ,
    **_READ_COMMANDS,
}

# OpenCode permission patterns use simple wildcard matching (`*` and `?`), not
# shell-style globstars. `**/app/**` therefore does not match the relative path
# `app/pricing.py`; the agent saw `action.pattern=* -> deny`. These relative
# patterns are the ones OpenCode actually matches.
_EDIT_FORGE = {
    "*": "deny",
    "app/*": "allow",
    "app/**": "allow",
    "tests/*": "allow",
    "tests/**": "allow",
}

_READ_RULES = {
    "*": "allow",
    "*.env": "deny",
    "*.env.*": "deny",
    "*.env.example": "allow",
    "**/.env": "deny",
    "**/.env.*": "deny",
    "**/.env.example": "allow",
    "**/secrets/**": "deny",
    "secrets/*": "deny",
    "secrets/**": "deny",
}

_BASE_PERMISSION: dict[str, Any] = {
    "*": "deny",
    "read": _READ_RULES,
    "edit": _EDIT_FORGE,
    "glob": "allow",
    "grep": "allow",
    "list": "allow",
    "bash": _FORGE_BASH,
    "todowrite": "allow",
    "external_directory": "deny",
    "webfetch": "deny",
    "websearch": "deny",
    "task": "deny",
    "skill": "deny",
    "question": "deny",
    "doom_loop": "deny",
    "lsp": "deny",
}

def build_config() -> dict[str, Any]:
    """Build the effective OpenCode configuration from the workspace .env."""
    model = settings.opencode_model
    review_model = settings.opencode_review_model
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": model,
        "small_model": model,
        "autoupdate": False,
        "share": "disabled",
        "permission": _BASE_PERMISSION,
        "agent": {
            **{
                name: {
                    "description": "Æion text-only proposal or review; the worker owns files and tests.",
                    # steps=1 injects OpenCode's final-summary instruction on the
                    # first completion. Tools stay denied; the adapter accepts
                    # exactly one actual step regardless of this ceiling.
                    "mode": "primary", "model": selected, "steps": 2,
                    "permission": {"*": "deny"},
                }
                for name, selected in ((PATCH_AGENT, model), (PATCH_REVIEW_AGENT, review_model))
            },
            FORGE_AGENT: {
                "description": "Æion bounded patch author. Edits app/** and tests/** only.",
                "mode": "primary",
                "model": model,
                "steps": 24,
                "permission": {
                    "edit": _EDIT_FORGE,
                    "bash": _FORGE_BASH,
                    "read": _READ_RULES,
                    "glob": "allow",
                    "grep": "allow",
                    "list": "allow",
                    "external_directory": "deny",
                    "webfetch": "deny",
                    "websearch": "deny",
                    "task": "deny",
                    "skill": "deny",
                    "question": "deny",
                },
            },
            REVIEW_AGENT: {
                "description": "Æion independent reviewer. Read-only; returns a JSON verdict.",
                "mode": "primary",
                "model": review_model,
                "steps": 12,
                "permission": {
                    "edit": "deny",
                    "bash": _REVIEW_BASH,
                    "read": _READ_RULES,
                    "glob": "allow",
                    "grep": "allow",
                    "list": "allow",
                    "external_directory": "deny",
                    "webfetch": "deny",
                    "websearch": "deny",
                    "task": "deny",
                    "skill": "deny",
                    "question": "deny",
                },
            },
        },
    }


def config_path() -> Path:
    return settings.opencode_config_dir / "opencode" / "opencode.json"


def ensure_config() -> Path:
    """Write the generated config atomically and return its path."""
    target = config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(build_config(), indent=2, sort_keys=False) + "\n"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(payload)
    temporary.replace(target)
    return target


def config_digest() -> str:
    return hashlib.sha256(config_path().read_bytes()).hexdigest() if config_path().is_file() else ""
