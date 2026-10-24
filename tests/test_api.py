import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(runtime):
    with TestClient(runtime.main.app, base_url="http://127.0.0.1") as client:
        yield client


@pytest.mark.parametrize("path", ["/", "/paperclip", "/parceldesk", "/observability", "/context", "/ops", "/frameworks"])
def test_real_pages_render_without_simulated_claims(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert "simulation" not in response.text.lower()
    assert "initial-snapshot" in response.text


def test_empty_snapshot_is_honest(client):
    data = client.get("/api/snapshot").json()
    assert data["meta"]["mode"] == "REAL"
    assert data["meta"]["execution"] == "native-host"
    assert not data["missions"]
    assert not data["evidence_packets"]
    assert not data["client"]["mission_id"]
    assert not data["paperclip"]["available"]
    assert {item["id"] for item in data["scenarios"]} == {"PD00", "PD01", "PD02", "PD03", "PD04"}
    assert data["kpis"]["cost_per_success"] is None


def test_launch_validates_payload_and_capacity(client):
    assert client.post("/api/missions", json={"fixture": []}).status_code == 422
    assert client.post("/api/missions", json={"fixture": "PD99"}).status_code == 422
    assert client.post("/api/missions", json={"fixture": "PD00"}).status_code == 200
    assert client.post("/api/missions", json={"fixture": "PD01"}).status_code == 409


def test_reset_is_disabled_and_unknown_evidence_is_404(client):
    assert client.post("/api/reset").status_code == 409
    assert client.get("/api/evidence/missing").status_code == 404
    assert client.get("/api/evidence/missing/verify").status_code == 404
    assert not client.post("/api/missions/missing/approve").json()["ok"]


@pytest.mark.parametrize("grams,cents", [(1, 500), (1000, 500), (1001, 800), (1999, 800), (2001, 1100), (30000, 9200)])
def test_quote_contract(client, grams, cents):
    assert client.get("/api/quote", params={"grams": grams}).json()["cents"] == cents


def test_quote_reproduces_baseline_and_validates_range(client):
    assert client.get("/api/quote?grams=1200&buggy=true").json()["cents"] == 500
    assert not client.get("/api/quote?grams=0").json()["ok"]
    assert client.get("/api/quote?grams=-1").status_code == 422
    assert client.get("/api/quote?grams=30001").status_code == 422


def test_config_does_not_expose_credentials(client):
    from app.config import settings
    payload = client.get("/api/config").text
    for credential in [settings.opencode_api_key, settings.anthropic_api_key, settings.openai_api_key, settings.paperclip_api_token]:
        if credential and credential in payload:
            pytest.fail("Credential exposed by /api/config")


def test_evidence_api_returns_the_canonical_packet(client, runtime):
    mission = runtime.engine.launch_mission("PD00")
    runtime.engine._execute_mission(mission["id"], runtime.module.SCENARIOS["PD00"])
    packet_id = "CROA-" + mission["id"]
    body = client.get("/api/evidence/" + packet_id).json()
    assert body["schema"] == "aeion.mission.evidence/v1"
    assert body["mission"]["id"] == mission["id"]
    assert client.get("/api/evidence/" + packet_id + "/verify").json()["valid"]
