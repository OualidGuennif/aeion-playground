import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("publication_check", Path(__file__).parents[1] / "scripts/check_publication.py")
publication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publication)


@pytest.mark.parametrize("mode,name", [
    ("100644", ".env"), ("100644", "nested/.env.production"),
    ("100644", "runtime/ledger.db"), ("100644", "runtime/agent-runs/run.prompt.txt"),
    ("100644", "config/model-routing.json"), ("100644", "nested/auth.json"),
    ("100644", "secrets/provider.txt"), ("120000", "ordinary-link"),
])
def test_public_index_refuses_sensitive_or_linked_state(mode, name):
    assert publication.violation(mode, name)


@pytest.mark.parametrize("name", [".env.example", "config/model-routing.example.json", "app/main.py",
                                  "app/static/vendor/three/three.module.min.js"])
def test_public_index_permits_source_and_safe_examples(name):
    assert not publication.violation("100644", name)


@pytest.mark.parametrize("name", ["vendor/private/repo.py", "nested/vendor/private.py", "app/static/other/vendor/file.js"])
def test_public_index_still_refuses_private_vendor_clones(name):
    assert publication.violation("100644", name)
