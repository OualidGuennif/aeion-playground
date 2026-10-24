"""Runtime configuration for the real Æion ParcelDesk POC.

Everything is loaded from the workspace `.env` (POC_AEION/.env is a symlink to
the workspace root). No secret is ever written to logs or returned by the API.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .operator_settings import operator_value

POC_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = POC_DIR

# Credentials remain in the existing dotenv source; non-secret routing defaults
# come from the explicit owner policy when present.
for candidate in (POC_DIR / ".env",):
    if candidate.exists():
        load_dotenv(candidate, override=False)


def _path(name: str, default: str) -> Path:
    raw = os.environ.get(name, default).strip() or default
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = POC_DIR / path
    return path.resolve()


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "1" if default else "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    test_backend: str = field(default_factory=lambda: os.environ.get("AEION_TEST_BACKEND", "docker"))
    mode: str = field(default_factory=lambda: os.environ.get("AEION_MODE", "real").strip().lower())
    approval_mode: str = field(default_factory=lambda: os.environ.get("AEION_APPROVAL_MODE", "human"))
    max_active_missions: int = field(default_factory=lambda: _int("AEION_MAX_ACTIVE_MISSIONS", 1))
    budget_usd: float = field(default_factory=lambda: float(operator_value("budget_usd", os.environ.get("AEION_BUDGET_USD", "5.00") or 5.00)))

    workspace_root: Path = field(default_factory=lambda: WORKSPACE_ROOT)
    poc_dir: Path = field(default_factory=lambda: POC_DIR)
    db_path: Path = field(default_factory=lambda: _path("AEION_DB_PATH", "runtime/aeion.db"))
    audit_path: Path = field(default_factory=lambda: _path("AEION_AUDIT_PATH", "runtime/events.jsonl"))
    sandbox_dir: Path = field(default_factory=lambda: _path("AEION_SANDBOX_DIR", "runtime/sandbox/parceldesk"))
    graph_path: Path = field(
        default_factory=lambda: _path("AEION_GRAPH_PATH", "runtime/graphify/graphify-out/graph.json")
    )
    graph_output_dir: Path = field(default_factory=lambda: _path("GRAPHIFY_OUTPUT", "runtime/graphify"))
    paperclip_dir: Path = field(default_factory=lambda: _path("PAPERCLIP_DIR", "vendor/paperclip"))
    paperclip_base_url: str = field(
        default_factory=lambda: os.environ.get("PAPERCLIP_BASE_URL", "http://127.0.0.1:3101").rstrip("/")
    )
    paperclip_api_token: str = field(default_factory=lambda: os.environ.get("PAPERCLIP_API_TOKEN", ""))
    paperclip_company_id: str = field(default_factory=lambda: os.environ.get("PAPERCLIP_COMPANY_ID", ""))

    agent_runtime: str = field(default_factory=lambda: os.environ.get("AEION_AGENT_RUNTIME", "opencode-cli"))
    agent_model: str = field(default_factory=lambda: os.environ.get("AEION_AGENT_MODEL", ""))
    agent_timeout: int = field(default_factory=lambda: int(operator_value("agent_timeout_seconds", _int("AEION_AGENT_TIMEOUT_SECONDS", 900))))
    agent_max_turns: int = field(default_factory=lambda: _int("AEION_AGENT_MAX_TURNS", 12))
    anthropic_api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    claude_oauth_token: str = field(default_factory=lambda: os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", ""))
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    openai_base_url: str = field(default_factory=lambda: os.environ.get("OPENAI_BASE_URL", ""))

    opencode_bin: str = field(
        default_factory=lambda: os.environ.get("OPENCODE_BIN", "vendor/opencode/node_modules/.bin/opencode")
    )
    opencode_model: str = field(
        default_factory=lambda: operator_value("default_author", os.environ.get("OPENCODE_MODEL", "opencode/deepseek-v4.1-flash-free"))
    )
    opencode_review_model: str = field(
        default_factory=lambda: operator_value("default_reviewer", os.environ.get("OPENCODE_REVIEW_MODEL", "opencode/glm-5.3-flash-free"))
    )
    opencode_api_key: str = field(default_factory=lambda: os.environ.get("OPENCODE_API_KEY", ""))
    opencode_data_dir: Path = field(
        default_factory=lambda: _path("OPENCODE_DATA_DIR", "runtime/opencode/data")
    )
    opencode_config_dir: Path = field(
        default_factory=lambda: _path("OPENCODE_CONFIG_DIR", "runtime/opencode/config")
    )
    opencode_state_dir: Path = field(
        default_factory=lambda: _path("OPENCODE_STATE_DIR", "runtime/opencode/state")
    )
    opencode_cache_dir: Path = field(
        default_factory=lambda: _path("OPENCODE_CACHE_DIR", "runtime/opencode/cache")
    )

    enable_github: bool = field(default_factory=lambda: _bool("AEION_ENABLE_GITHUB", False))
    github_repo: str = field(default_factory=lambda: os.environ.get("AEION_GITHUB_REPO", ""))

    factory_dir: Path = field(default_factory=lambda: _path("AEION_FACTORY_DIR", "references/factory"))
    ops_dir: Path = field(default_factory=lambda: _path("AEION_OPS_DIR", "references/ops"))
    croa_dir: Path = field(default_factory=lambda: _path("AEION_CROA_DIR", "references/croa"))
    observability_dir: Path = field(
        default_factory=lambda: _path("AEION_OBSERVABILITY_DIR", "references/observability")
    )
    spaceship_dir: Path = field(default_factory=lambda: _path("AEION_SPACESHIP_DIR", "references/spaceship"))

    def ensure_dirs(self) -> None:
        for path in (
            self.db_path.parent,
            self.audit_path.parent,
            self.sandbox_dir.parent,
            self.graph_path.parent,
            self.graph_output_dir,
            self.opencode_data_dir,
            self.opencode_config_dir,
            self.opencode_state_dir,
            self.opencode_cache_dir,
            self.opencode_config_dir / "opencode",
        ):
            path.mkdir(parents=True, exist_ok=True)

    def opencode_bin_path(self) -> Path:
        path = Path(self.opencode_bin).expanduser()
        if not path.is_absolute():
            path = POC_DIR / path
        return path.resolve()

    def agent_readiness(self) -> dict[str, object]:
        """Return an honest readiness report for the configured agent runtime."""
        runtime = self.agent_runtime
        if runtime == "opencode-cli":
            binary = self.opencode_bin_path()
            if not binary.is_file():
                return {
                    "ready": False,
                    "runtime": runtime,
                    "reason": f"OpenCode binary not found: {binary}",
                }
            has_key = bool(self.opencode_api_key)
            free_model = self.opencode_model.startswith("opencode/") and self.opencode_model.endswith("-free")
            auth = (
                "anonymous free model · API key present (not required)"
                if free_model and has_key
                else "anonymous free model"
                if free_model
                else "api key from .env"
                if has_key
                else "no credential"
            )
            return {
                "ready": has_key or free_model,
                "runtime": runtime,
                "binary": str(binary),
                "model": self.opencode_model,
                "review_model": self.opencode_review_model,
                "auth": auth,
                "reason": None
                if (has_key or free_model)
                else "No OpenCode credential. Set OPENCODE_API_KEY in .env or run scripts/opencode_auth.sh.",
            }
        if runtime in {"anthropic-api", "openai-api"}:
            return {
                "ready": False,
                "runtime": runtime,
                "reason": (
                    f"Runtime {runtime!r} is not implemented in this POC. "
                    "Use opencode-cli or claude-cli; no simulated API call is made."
                ),
            }
        if runtime == "claude-cli":
            binary = shutil.which("claude")
            if not binary:
                return {
                    "ready": False,
                    "runtime": runtime,
                    "reason": "The `claude` CLI is not on PATH.",
                }
            # A CLI can be installed but logged out. `claude -p` is the only
            # reliable non-interactive probe; it is cheap and only run on demand.
            return {
                "ready": bool(self.claude_oauth_token or self.anthropic_api_key),
                "runtime": runtime,
                "reason": None
                if (self.claude_oauth_token or self.anthropic_api_key)
                else "Claude CLI OAuth is expired. Run `claude auth login` or set ANTHROPIC_API_KEY.",
                "binary": binary,
            }
        return {"ready": False, "runtime": runtime, "reason": f"Unknown runtime {runtime!r}."}


settings = Settings()
settings.ensure_dirs()
