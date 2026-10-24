import json
import os
import socket

import pytest

from app.container_inference import GatewayClient
from app.inference_gateway import OpenCodeTransport, UPSTREAM_PATHS


@pytest.mark.parametrize("url", ["https://external.example", "http://192.0.2.1:8130", "http://localhost:8130/path",
                                "http://user:pass@127.0.0.1:8130", "http://127.0.0.1:8130?url=https://external.example"])
def test_admin_capability_cannot_be_sent_to_nonloopback_or_routed_url(url):
    with pytest.raises(OSError, match="loopback"):
        GatewayClient(url)


@pytest.mark.parametrize("kind", ["public", "symlink", "fifo"])
def test_admin_capability_requires_private_regular_file(tmp_path, kind):
    target = tmp_path / "capability"
    if kind == "symlink":
        other = tmp_path / "public-canary"
        other.write_text("a" * 64)
        target.symlink_to(other)
    elif kind == "fifo":
        os.mkfifo(target)
    else:
        target.write_text("a" * 64)
        target.chmod(0o644)
    with pytest.raises(OSError):
        GatewayClient(secret_path=target)


def test_transport_has_fixed_https_destination_and_drops_client_authority_headers(monkeypatch):
    from app import inference_gateway as module
    observed = {}
    class Response:
        status = 302
        def read1(self, size):
            return b""
    class Connection:
        sock = None
        def __init__(self, host, timeout):
            observed.update(host=host, timeout=timeout)
        def connect(self):
            class Socket:
                def settimeout(self, value): pass
                def shutdown(self, how): pass
            self.sock = Socket()
        def request(self, method, path, body, headers):
            observed.update(method=method, path=path, body=json.loads(body), headers=headers)
        def getresponse(self):
            return Response()
        def close(self):
            observed["closed"] = True
    monkeypatch.setattr(module.http.client, "HTTPSConnection", Connection)
    status, content = OpenCodeTransport().request("opencode-go", {"model": "controlled"},
        {"Authorization": "Bearer must-not-forward", "Host": "other.test", "x-secret": "must-not-forward",
         "x-opencode-session": "controlled-session", "x-opencode-client": "cli"}, "public-controlled-provider-key")
    assert status == 302 and content == b""  # The transport never follows Location.
    assert observed["host"] == "opencode.ai" and observed["path"] == UPSTREAM_PATHS["opencode-go"]
    assert observed["headers"]["Authorization"] == "Bearer public-controlled-provider-key"
    assert observed["headers"]["x-opencode-session"] == "controlled-session"
    assert "Host" not in observed["headers"] and "x-secret" not in observed["headers"]
    assert observed["closed"]


def test_total_upstream_deadline_interrupts_a_stalled_response(monkeypatch):
    import threading
    import time
    from app import inference_gateway as module
    interrupted = threading.Event()
    class Socket:
        def settimeout(self, value): pass
        def shutdown(self, how): interrupted.set()
    class Response:
        status = 200
        def read1(self, size):
            assert interrupted.wait(timeout=1), "Total deadline did not interrupt the read"
            return b""
    class Connection:
        sock = Socket()
        def __init__(self, *args, **kwargs): pass
        def connect(self): pass
        def request(self, *args): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(module.http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(module, "UPSTREAM_DEADLINE", .05)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        OpenCodeTransport().request("opencode", {}, {}, "public")
    assert interrupted.is_set() and time.monotonic() - started < .5
