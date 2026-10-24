"""ParcelDesk warehouse page, vendored Three.js and the ticket form surface."""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).parents[1]


def test_warehouse_page_serves_the_three_surfaces(runtime):
    client = TestClient(runtime.main.app, base_url="http://127.0.0.1")
    page = client.get("/warehouse")
    assert page.status_code == 200
    assert "Today at ParcelDesk" in page.text
    assert "/static/js/warehouse.js" in page.text
    assert "wh-canvas" in page.text and "ticket-form" in page.text and "refund-form" in page.text
    storefront = client.get("/parceldesk")
    assert storefront.status_code == 200 and "/warehouse" in storefront.text
    script = client.get("/static/js/warehouse.js")
    assert script.status_code == 200 and "three.module.min.js" in script.text
    assert client.get("/static/css/warehouse.css").status_code == 200


def test_vendored_three_files_match_the_recorded_hashes():
    vendor = ROOT / "app/static/vendor/three"
    readme = (vendor / "README.md").read_text()
    expected = {
        "three.module.min.js": "e2b5ee6bccd38fd6d8a2428546b83c5f2426d84b152ef82be8055556e3b40eb6",
        "three.core.min.js": "61ba0df005b05991361d040d8ff670e1aadfd0ce7aeebd1fdb0725957a8957de",
    }
    for name, digest in expected.items():
        assert digest in readme
        assert hashlib.sha256((vendor / name).read_bytes()).hexdigest() == digest
    assert (vendor / "LICENSE").read_text().startswith("The MIT License")
