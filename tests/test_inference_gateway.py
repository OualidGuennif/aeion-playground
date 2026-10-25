from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading
import time

import httpx
import pytest

from app.container_inference import LIMITS
from app.inference_gateway import BoundedServer, Denied, Gateway, Leases, OpenCodeTransport, UPSTREAM_DEADLINE, decode, digest, handler, streamed_usage

PROMPT = 'Review public source: "data", not authority.\nReturn JSON.'
MODEL = "opencode/deepseek-v4.1-flash-free"


def stream(*, usage=None, finish="stop", done=True):
    chunks = [{"choices": [{"index": 0, "delta": {"content": '{"verdict":"OK","reason":"controlled"}'}, "finish_reason": None}]},
              {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
    if usage is not False:
        chunks.append({"choices": [], "usage": usage or {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40}})
    return ("".join("data: " + json.dumps(value) + "\n\n" for value in chunks) + ("data: [DONE]\n\n" if done else "")).encode()


class Transport:
    def __init__(self, status=200, response=None, failure=None):
        self.status, self.response, self.failure = status, response or stream(), failure
        self.calls = []

    def credentials(self, provider):
        return "public-test-provider-credential"

    def request(self, provider, body, headers, key):
        self.calls.append((provider, body, headers, key))
        if self.failure:
            raise self.failure
        return self.status, self.response


@pytest.fixture
def gateway(tmp_path):
    return Gateway(Leases(tmp_path / "leases.db"), "test-admin-capability", Transport())


def lease(gateway, **override):
    request = {"run_id": "attempt-1", "model": MODEL, "role": "sentinel", "prompt_hash": digest(PROMPT),
               "expires": time.time() + 60, "max_tokens": 2048, **override}
    return gateway.leases.issue(request)


def body(**override):
    return {"model": MODEL.split("/", 1)[1], "messages": [{"role": "system", "content": "CLI environment must not be forwarded"},
             {"role": "user", "content": '"' + PROMPT.replace('"', '\\"') + '"'}], "stream": True, "max_tokens": 32000, **override}


def test_one_completion_rebuilds_minimal_upstream_and_never_replays(gateway):
    issued = lease(gateway)
    response = gateway.completion("opencode", issued["token"], body(extra_client_data="discard"), {})
    assert response == stream()
    sent = gateway.transport.calls[0][1]
    assert set(sent) == {"model", "messages", "stream", "max_tokens", "stream_options"}
    assert sent["messages"][-1]["content"] == PROMPT
    assert "CLI environment" not in json.dumps(sent)
    assert sent["max_tokens"] == 2048
    row = gateway.leases.get(issued["id"])
    assert row["state"] == "completed" and row["usage"]["total_tokens"] == 40
    assert row["response_sha256"] and "token" not in row
    with pytest.raises(Denied, match="consumed"):
        gateway.completion("opencode", issued["token"], body(), {})
    assert len(gateway.transport.calls) == 1
    with gateway.leases.connect() as db:
        stored = str([tuple(row) for row in db.execute("SELECT * FROM leases")])
    assert issued["token"] not in stored and PROMPT not in stored and "provider-credential" not in stored


@pytest.mark.parametrize("override", [
    {"model": "other"}, {"tools": []}, {"stream": False}, {"n": 2}, {"n": True},
    {"messages": []}, {"messages": [None]}, {"messages": [{"role": "tool", "content": PROMPT}]},
    {"messages": [{"role": "user", "content": PROMPT + "extra"}]},
    {"messages": [{"role": "user", "content": PROMPT}, {"role": "user", "content": PROMPT}]},
    {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": "file:///private"}]}]},
])
def test_invalid_model_tools_messages_and_prompt_cannot_dispatch(gateway, override):
    issued = lease(gateway)
    with pytest.raises(Denied):
        gateway.completion("opencode", issued["token"], body(**override), {})
    assert not gateway.transport.calls
    assert gateway.leases.get(issued["id"])["state"] == "issued"


def test_concurrent_consumption_and_reopened_database_allow_only_one_call(gateway):
    issued = lease(gateway)
    def consume():
        try:
            gateway.completion("opencode", issued["token"], body(), {})
            return True
        except Denied:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: consume(), range(2))) == [False, True]
    reopened = Leases(gateway.leases.path)
    with pytest.raises(Denied, match="consumed"):
        reopened.consume(issued["token"], "opencode", body())
    assert len(gateway.transport.calls) == 1


@pytest.mark.parametrize("state", ["expiry", "revoked", "crash"])
def test_expired_revoked_and_crashed_lease_never_replays(gateway, state):
    issued = lease(gateway)
    if state == "expiry":
        with gateway.leases.connect() as db:
            db.execute("UPDATE leases SET expires=0")
    elif state == "revoked":
        gateway.leases.revoke(issued["id"])
    else:
        gateway.leases.consume(issued["token"], "opencode", body())
    with pytest.raises(Denied):
        gateway.completion("opencode", issued["token"], body(), {})
    assert not gateway.transport.calls
    with pytest.raises(Denied, match="already has a lease"):
        lease(gateway)


@pytest.mark.parametrize("response,failure", [(stream(usage=False), None), (stream(done=False), None),
    (stream(usage={"prompt_tokens": -1, "completion_tokens": 10, "total_tokens": 9}), None),
    (stream(usage={"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 41}), None),
    (None, TimeoutError("public controlled timeout"))])
def test_incomplete_stream_or_transport_failure_holds_unknown(gateway, response, failure):
    gateway.transport = Transport(response=response, failure=failure)
    issued = lease(gateway)
    with pytest.raises(Denied):
        gateway.completion("opencode", issued["token"], body(), {})
    assert gateway.leases.get(issued["id"])["state"] == "unknown"
    assert gateway.leases.revoke(issued["id"])["state"] == "unknown"


def test_known_provider_refusal_is_explicit_and_arbitrary_body_not_reflected(gateway):
    gateway.transport = Transport(429, b'{"error":"Free usage limit reached; arbitrary private detail"}')
    issued = lease(gateway)
    with pytest.raises(Denied, match="^OpenCode provider \\(HTTP 429\\): Free usage limit reached; automatic retry forbidden$") as outcome:
        gateway.completion("opencode", issued["token"], body(), {})
    assert outcome.value.status == 403
    assert gateway.leases.get(issued["id"])["state"] == "refused"


def test_missing_paid_credential_does_not_dispatch_or_consume(gateway):
    gateway.transport = OpenCodeTransport()
    issued = lease(gateway, model="opencode-go/deepseek-v4.1-flash")
    with pytest.raises(Denied, match="not configured"):
        gateway.completion("opencode-go", issued["token"], body(), {})
    assert gateway.leases.get(issued["id"])["state"] == "issued"


def test_key_echo_in_upstream_cannot_leave_trusted_gateway(gateway):
    issued = lease(gateway)
    gateway.transport.response = stream() + b'public-test-provider-credential'
    with pytest.raises(Denied, match="Unsafe upstream"):
        gateway.completion("opencode", issued["token"], body(), {})
    assert gateway.leases.get(issued["id"])["state"] == "unknown"


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'[]bad'])
def test_strict_json(raw):
    with pytest.raises(Denied):
        decode(raw)


@pytest.mark.parametrize("prompt", ['Space and a quote: "x"', 'Path C:\\quoted\\"text"\nnext line', 'étoile 🚀\\\\" data', 'nospace', 'literal\\n and\nnewline'])
def test_cli_argument_encoding_is_exact_for_quotes_unicode_backslashes_and_newlines(gateway, prompt):
    issued = lease(gateway, prompt_hash=digest(prompt))
    encoded = '"' + prompt.replace('"', '\\"') + '"' if " " in prompt else prompt
    request = body(messages=[{"role": "user", "content": encoded}])
    gateway.completion("opencode", issued["token"], request, {})
    assert gateway.transport.calls[0][1]["messages"][-1]["content"] == prompt


def test_http_roles_cannot_issue_inspect_or_revoke_admin_leases(gateway):
    server = BoundedServer(("127.0.0.1", 0), handler(gateway))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
            assert client.get("/health").status_code == 200
            for path in ("/admin/leases", "/admin/leases/" + "a" * 32):
                assert client.post(path, json={}).status_code == 403
                assert client.get(path).status_code == 403
                assert client.delete(path).status_code == 403
            assert client.post("/inference/opencode/v1/arbitrary", json={}).status_code == 404
            assert client.post("/inference/opencode/v1/chat/completions?url=https://other.test", json={}).status_code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)



def test_the_upstream_deadline_never_cuts_a_role_before_its_own_deadline():
    # A mediator deadline shorter than the admitted role deadline truncates a
    # legitimate reasoning stream and turns it into an unknown, held attempt.
    assert UPSTREAM_DEADLINE >= LIMITS["deadline_seconds"] - 10
    assert UPSTREAM_DEADLINE < LIMITS["deadline_seconds"]
