"""Browser-origin protection is distinct from native process/identity isolation."""
import pytest
from fastapi.testclient import TestClient

from test_missions import patch_runner, run_scenario


@pytest.mark.parametrize("headers", [
    {"origin": "https://foreign.example"}, {"origin": "null"},
    {"sec-fetch-site": "cross-site"},
    {"origin": "http://127.0.0.1", "sec-fetch-site": "cross-site"},
])
def test_a_foreign_webpage_cannot_approve_a_human_checkpoint(runtime, headers):
    mission = run_scenario(runtime, "PD01", patch_runner)
    with TestClient(runtime.main.app, base_url="http://127.0.0.1") as client:
        response = client.post(f"/api/missions/{mission['id']}/approve", headers=headers)
    assert response.status_code == 403
    current = runtime.store.mission(mission["id"])
    assert current["status"] == "awaiting_human"
    assert current["human_approval"] is None
    assert runtime.store.evidence_for(mission["id"])["status"] == "draft"


def test_same_origin_operator_approval_and_headerless_local_cli_still_work(runtime):
    mission = run_scenario(runtime, "PD01", patch_runner)
    with TestClient(runtime.main.app, base_url="http://127.0.0.1") as client:
        response = client.post(f"/api/missions/{mission['id']}/approve", headers={"origin": "http://127.0.0.1", "sec-fetch-site": "same-origin"})
        assert response.status_code == 200 and response.json()["ok"]
        queued = client.post("/api/missions/queue", json={"fixture": "PD00", "request_id": "cli-no-origin"})
        assert queued.status_code == 200


def test_cross_origin_launch_cannot_consume_model_quota_or_create_a_task(runtime):
    with TestClient(runtime.main.app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/missions", headers={"origin": "https://foreign.example"})
    assert response.status_code == 403
    assert runtime.store.count_missions() == 0
    assert not runtime.store.runs()


@pytest.mark.parametrize("host", ["foreign.example", "127.0.0.1.foreign.example"])
def test_local_host_allowlist_rejects_dns_rebinding_hosts(runtime, host):
    with TestClient(runtime.main.app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/missions", headers={"host": host, "origin": f"http://{host}"})
    assert response.status_code == 400
    assert runtime.store.count_missions() == 0


@pytest.mark.parametrize("base", ["http://localhost:8123", "http://127.0.0.1:8124", "http://[::1]:8123"])
def test_operator_origin_tracks_the_local_host_and_port(runtime, base):
    with TestClient(runtime.main.app, base_url=base) as client:
        response = client.post("/api/missions/queue", headers={"origin": base}, json={"fixture": "PD00", "request_id": "same-local-origin"})
    assert response.status_code == 200
