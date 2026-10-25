"""Opt-in Docker qualification: the polyglot worker really runs both product suites.

The product profile must run the Quarkus/Maven backend tests and the Deno frontend
tests inside the disposable worker, not fall back to the Python contract. The test
uses the checked-in sandbox template and the qualified image; no model is called.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from types import SimpleNamespace

from app.sandbox import SandboxManager

pytestmark = pytest.mark.skipif(
    os.environ.get("AEION_DOCKER_TESTS") != "1",
    reason="Requires explicitly enabled Docker qualification",
)
ROOT = Path(__file__).parents[1]


def test_product_profile_runs_maven_and_deno_suites(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sandbox.settings", SimpleNamespace(test_backend="docker"))
    manager = SandboxManager(tmp_path / "runs")
    result = manager.run_tests(ROOT / "sandbox/parceldesk", profile="product", timeout=180)
    assert result.available and not result.error, result.error
    assert result.exit_code == 0
    assert result.total == 12 and result.passed == 12 and result.failed == 0
    assert result.errors == 0 and result.skipped == 0
    assert result.isolation["profile"] == "product"
    names = {case["classname"] for case in result.cases}
    assert any(name.startswith("com.aeion.parceldesk.") for name in names)
    assert any(name.endswith("format_test.ts") for name in names)
