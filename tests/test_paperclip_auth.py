import json
import os
import stat

import httpx
import pytest

from app import paperclip_auth as auth
from app.paperclip import PaperclipClient

BASE = "http://127.0.0.1:3101"
PUBLIC_TOKEN = "controlled-public-board-token"


@pytest.mark.parametrize("target", ["https://external.test", "http://localhost:3101/path", "http://127.0.0.1:3101@external.test", "http://127.0.0.1:3101?redirect=external"])
def test_board_authorization_never_sends_credentials_outside_local_origin(target):
    with pytest.raises(ValueError):
        auth.local_origin(target)


def test_credential_is_owner_only_origin_bound_and_never_read_from_link(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    path = directory / "auth.json"
    auth.save_board_token(BASE, PUBLIC_TOKEN, "controlled-user", path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert auth.load_board_token(BASE, path) == PUBLIC_TOKEN
    assert auth.load_board_token("http://127.0.0.1:9999", path) == ""
    link = directory / "link.json"
    link.symlink_to(path)
    with pytest.raises(OSError):
        auth.load_board_token(BASE, link)
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        auth.load_board_token(BASE, path)


@pytest.mark.parametrize("status", ["approved", "cancelled"])
def test_only_browser_approved_board_challenge_can_persist_a_key(tmp_path, monkeypatch, status):
    requests, announced = [], []
    def route(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/api/cli-auth/challenges":
            data = json.loads(request.content)
            assert data["requestedAccess"] == "board" and data["clientName"] == "AEion Factory"
            return httpx.Response(201, json={"approvalPath": "/cli-auth/controlled?token=public-pairing-code",
                "pollPath": "/cli-auth/challenges/controlled", "token": "public-pairing-code",
                "boardApiToken": PUBLIC_TOKEN, "expiresAt": "2099-01-01T00:00:00Z"})
        if request.url.path == "/api/cli-auth/challenges/controlled":
            return httpx.Response(200, json={"status": status})
        assert request.url.path == "/api/cli-auth/me"
        assert request.headers["Authorization"] == "Bearer " + PUBLIC_TOKEN
        return httpx.Response(200, json={"userId": "controlled-user"})
    original_client = httpx.Client
    monkeypatch.setattr(auth.httpx, "Client", lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(route)))
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    path = directory / "auth.json"
    if status == "approved":
        assert auth.connect(BASE, announce=announced.append, path=path)["connected"]
        assert auth.load_board_token(BASE, path) == PUBLIC_TOKEN
    else:
        with pytest.raises(RuntimeError, match="cancelled"):
            auth.connect(BASE, announce=announced.append, path=path)
        assert not path.exists()
    assert len(announced) == 1 and PUBLIC_TOKEN not in announced[0]
    assert not any("approve" in route for _, route in requests)


def test_plain_container_token_is_private_owner_only_and_exported_without_printing(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    board = directory / "auth.json"
    auth.save_board_token(BASE, PUBLIC_TOKEN, "controlled-user", board)
    target = directory / "paperclip_api_token"
    auth.export_plain_token(BASE, target, board)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert auth.load_api_token_file(target) == PUBLIC_TOKEN
    inode = target.stat().st_ino
    auth.export_plain_token(BASE, target, board)
    assert target.stat().st_ino == inode
    assert {item.name for item in directory.iterdir()} == {"auth.json", "paperclip_api_token"}
    target.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        auth.load_api_token_file(target)
    link = directory / "link-token"
    link.symlink_to(target)
    with pytest.raises(OSError):
        auth.load_api_token_file(link)


def test_controller_does_not_send_authorization_through_proxy_or_follow_redirects(monkeypatch):
    from app import paperclip as module
    observed = []
    def request(method, url, **kwargs):
        observed.append(kwargs)
        return httpx.Response(302, json={"message": "controlled redirect"})
    monkeypatch.setattr(module.httpx, "request", request)
    outcome = PaperclipClient()._request("GET", "/api/companies")
    assert not outcome["ok"] and outcome["status"] == 302
    assert observed[0]["trust_env"] is False and observed[0]["follow_redirects"] is False
