import dataclasses
import pytest

from app import paperclip as module
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


def test_issue_url_uses_the_browser_public_origin(monkeypatch):
    client = PaperclipClient()
    monkeypatch.setattr(client, "ensure_company", lambda: {
        "available": True,
        "company": {"id": "controlled", "name": "ParcelDesk", "issuePrefix": "AEI"},
    })
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: {
        "ok": True, "status": 201, "data": {"id": "controlled-issue", "identifier": "AEI-9", "status": "todo"},
    })
    monkeypatch.setattr(module, "settings", dataclasses.replace(module.settings, paperclip_public_url="http://127.0.0.1:3101"))
    issue = client.create_issue({"id": "PD-999", "title": "controlled", "priority": "low"})
    assert issue["url"] == "http://127.0.0.1:3101/AEI/issues/AEI-9"
