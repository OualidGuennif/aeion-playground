"""Controlled transport only: never imports credentials or makes an upstream call."""
import importlib.util
import json
import os

spec = importlib.util.spec_from_file_location("mediator", "/opt/aeion/inference_gateway.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ControlledTransport:
    calls = 0

    def credentials(self, provider):
        return "public-controlled-credential"

    def request(self, provider, body, headers, key):
        self.calls += 1
        if "controlled rate refusal" in body["messages"][-1]["content"]:
            return 429, b'{"error":{"message":"Free usage limit reached"}}'
        if body["messages"][0]["content"].startswith("Propose"):
            patch = "diff --git a/tests/test_transport.py b/tests/test_transport.py\nnew file mode 100644\n--- /dev/null\n+++ b/tests/test_transport.py\n@@ -0,0 +1,2 @@\n+def test_transport():\n+    assert True\n"
            text = json.dumps({"patch": patch, "summary": "Controlled transport fixture; no model inference"})
        else:
            text = json.dumps({"verdict": "OK", "reason": "Controlled transport fixture; no model inference"})
        chunks = [
            {"choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40}}]
        return 200, ("".join("data: " + json.dumps({"id": "chatcmpl-controlled", "object": "chat.completion.chunk", "created": 1,
                    "model": body["model"], **chunk}) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()


transport = ControlledTransport()
gateway = module.Gateway(module.Leases("/tmp/leases.db"), os.environ["AEION_TEST_ADMIN"], transport)
module.BoundedServer(("0.0.0.0", 8130), module.handler(gateway)).serve_forever()
