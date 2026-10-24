import pytest

from app.paperclip import PaperclipClient


@pytest.mark.parametrize("status", ["blocked", "in_review", "done"])
def test_workboard_status_uses_installed_blocker_contract(monkeypatch, status):
    client = PaperclipClient()
    requests = []
    def request(method, path, **kwargs):
        requests.append(kwargs["json"])
        return {"ok": True, "status": 200}
    monkeypatch.setattr(client, "_request", request)
    assert client.update_issue("issue", status=status, comment="Gate PROTECTED_PATH") ["ok"]
    payload = requests[0]
    assert payload["status"] == status
    if status == "blocked":
        assert payload["unblockDescriptor"]["owner"] == "board"
        assert "new mission" in payload["unblockDescriptor"]["action"]
    else:
        assert "unblockDescriptor" not in payload
