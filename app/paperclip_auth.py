"""Paperclip's official board CLI authorization protocol, without exposing a key.

Only a real browser session approves the challenge. This application never calls
the approval endpoint and never creates an operator or configures an LLM provider.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from urllib.parse import urlparse

import httpx

AUTH_STORE = Path(os.environ.get("PAPERCLIP_AUTH_STORE", str(Path(__file__).resolve().parents[1] / "runtime/secrets/paperclip-board-auth.json"))).expanduser()


def local_origin(value):
    value = value.rstrip("/")
    target = urlparse(value)
    if (target.scheme != "http" or target.hostname != "127.0.0.1" or not target.port or target.username or target.password
            or target.path or target.query or target.fragment):
        raise ValueError("Workboard authorization requires a loopback HTTP origin")
    return value


def load_board_token(base, path=AUTH_STORE):
    base = local_origin(base)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        return ""
    with os.fdopen(descriptor, "r") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("Workboard credential must be a private operator-owned file")
        raw = stream.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise ValueError("Workboard credential store exceeds its bound")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("credentials"), dict):
        raise ValueError("Invalid workboard credential store")
    item = data["credentials"].get(base)
    if item is None:
        return ""
    if not isinstance(item, dict) or item.get("apiBase") != base:
        raise ValueError("Workboard credential is bound to a different origin")
    token = item.get("token")
    if not isinstance(token, str) or not 16 <= len(token) <= 4096 or not all(33 <= ord(char) < 127 for char in token):
        raise ValueError("Invalid workboard credential")
    return token


def load_api_token_file(path):
    """Read a plain bearer token from an owner-only regular file.

    This is the container path: the token is mounted read-only at a fixed path,
    never placed in the environment or printed. Missing means unauthenticated;
    insecure ownership/mode or a symlink fails closed.
    """
    target = Path(path).expanduser()
    try:
        descriptor = os.open(target, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        return ""
    with os.fdopen(descriptor, "r") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("Workboard API token must be a private operator-owned file")
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError("Workboard API token exceeds its bound")
    token = raw.strip()
    if not 16 <= len(token) <= 4096 or not all(33 <= ord(char) < 127 for char in token):
        raise ValueError("Invalid workboard API token")
    return token


def save_plain_token(path, token):
    """Publish a derived plain token with the same private-file guarantees."""
    if not isinstance(token, str) or not 16 <= len(token) <= 4096 or not all(33 <= ord(char) < 127 for char in token):
        raise ValueError("Invalid workboard API token")
    target = Path(path)
    directory = target.parent
    if directory.is_symlink() or directory.parent.is_symlink() or target.is_symlink():
        raise ValueError("Workboard token paths cannot be symlinks")
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    if directory.stat().st_uid != os.getuid() or stat.S_IMODE(directory.stat().st_mode) & 0o077:
        raise ValueError("Workboard token directory must be private")
    # Write in place when the file exists: Docker bind-mounts a file inode, so an
    # atomic os.replace would leave a running cockpit reading the deleted inode.
    flags = os.O_WRONLY | os.O_NOFOLLOW
    if target.exists():
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("Existing workboard API token must be a private operator-owned file")
        flags |= os.O_TRUNC
    else:
        flags |= os.O_CREAT | os.O_EXCL
    descriptor = os.open(target, flags, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(token + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(target, 0o600)
    if load_api_token_file(target) != token:
        raise ValueError("Workboard token validation failed")


def export_plain_token(base, destination, path=AUTH_STORE):
    """Derive the container token from the browser-approved board store."""
    token = load_board_token(base, path)
    if not token:
        raise ValueError("No approved workboard credential to export")
    save_plain_token(destination, token)
    return Path(destination)


def save_board_token(base, token, user_id, path=AUTH_STORE):
    base = local_origin(base)
    directory = path.parent
    if directory.is_symlink() or directory.parent.is_symlink() or path.is_symlink():
        raise ValueError("Workboard credential paths cannot be symlinks")
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    if directory.stat().st_uid != os.getuid() or stat.S_IMODE(directory.stat().st_mode) & 0o077:
        raise ValueError("Workboard credential directory must be private")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data = {"version": 1, "credentials": {base: {"apiBase": base, "token": token, "createdAt": now,
            "updatedAt": now, "userId": user_id}}}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, prefix=".paperclip-auth-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        # The same validation used by the controller runs before publishing a key.
        load_board_token(base, temporary)
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def connect(base, *, announce, path=AUTH_STORE, timeout=300):
    """Mirror paperclipai auth login, storing only a browser-approved board key."""
    base = local_origin(base)
    with httpx.Client(base_url=base, timeout=8, trust_env=False, follow_redirects=False) as client:
        def request(method, route, **kwargs):
            try:
                response = client.request(method, route, **kwargs)
                if response.status_code != 200 and response.status_code != 201:
                    raise RuntimeError(f"Workboard authorization refused (HTTP {response.status_code})")
                return response.json()
            except (httpx.HTTPError, ValueError):
                raise RuntimeError("Workboard authorization unavailable; no credential was displayed") from None
        existing = load_board_token(base, path)
        if existing:
            me = request("GET", "/api/cli-auth/me", headers={"Authorization": "Bearer " + existing})
            if me.get("userId"):
                return {"connected": True, "api_base": base}
        challenge = request("POST", "/api/cli-auth/challenges", json={"command": "aeion-playground connect workboard",
            "clientName": "AEion Factory", "requestedAccess": "board", "requestedCompanyId": None})
        approval = challenge["approvalPath"]
        poll = challenge["pollPath"]
        if (not isinstance(approval, str) or not approval.startswith("/cli-auth/") or approval.startswith("//")
                or not isinstance(poll, str) or not re.fullmatch(r"/cli-auth/challenges/[A-Za-z0-9_-]{1,100}", poll)):
            raise RuntimeError("Invalid local workboard authorization route")
        expires = datetime.fromisoformat(challenge["expiresAt"].replace("Z", "+00:00")).timestamp()
        deadline = min(time.time() + min(timeout, 300), expires)
        interval = max(1, min(5, float(challenge.get("suggestedPollIntervalMs") or 1000) / 1000))
        # The approval URL contains a short-lived pairing code for the browser.
        # The caller opens it directly; never display the reusable board API key.
        announce(base + approval)
        while time.time() < deadline:
            status = request("GET", "/api" + poll, params={"token": challenge["token"]})
            if status.get("status") == "approved":
                me = request("GET", "/api/cli-auth/me", headers={"Authorization": "Bearer " + challenge["boardApiToken"]})
                if not isinstance(me.get("userId"), str) or not me["userId"]:
                    raise RuntimeError("Approved workboard session has no user identity")
                save_board_token(base, challenge["boardApiToken"], me["userId"], path)
                return {"connected": True, "api_base": base}
            if status.get("status") in {"cancelled", "expired"}:
                raise RuntimeError("Workboard authorization cancelled or expired")
            time.sleep(min(interval, max(0, deadline - time.time())))
        raise RuntimeError("Workboard authorization is still pending; rerun to request a fresh approval")
