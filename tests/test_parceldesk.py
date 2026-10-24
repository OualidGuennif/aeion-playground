"""ParcelDesk orders, refunds and the ticket-to-mission path."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.parceldesk import ParcelDeskStore, refund_quote
from test_missions import result


@pytest.fixture()
def desk(tmp_path):
    store = ParcelDeskStore(tmp_path / "parceldesk.db")
    try:
        yield store
    finally:
        store.close()


def test_refund_rule_window_amount_and_high_value_reason(desk):
    now = datetime.now(timezone.utc)
    delivered = next(order for order in desk.orders() if order["id"] == "PD-1042")
    quote = refund_quote(delivered, now=now, reason="")
    assert quote["amount_cents"] == delivered["total_cents"] - delivered["shipping_cents"]
    result = desk.refund("PD-1042", "Parcel arrived damaged; photos attached.", now=now)
    assert result["amount_cents"] == 6_400
    with pytest.raises(ValueError, match="already"):
        desk.refund("PD-1042", "again", now=now)
    old = next(order for order in desk.orders() if order["id"] == "PD-1037")
    with pytest.raises(ValueError, match="30 days"):
        refund_quote(old, now=now, reason="too late")
    high = next(order for order in desk.orders() if order["id"] == "PD-1038")
    with pytest.raises(ValueError, match="10 characters"):
        refund_quote(high, now=now, reason="")
    assert refund_quote(high, now=now, reason="Two shelves arrived broken.")["high_value"] is True
    packing = next(order for order in desk.orders() if order["status"] == "packing")
    with pytest.raises(ValueError, match="Only delivered"):
        refund_quote(packing, now=now, reason="not yet")


def test_api_lists_orders_and_records_a_refund(runtime):
    client = TestClient(runtime.main.app, base_url="http://127.0.0.1")
    summary = client.get("/api/parceldesk/summary")
    assert summary.status_code == 200 and summary.json()["ok"]
    assert summary.json()["orders"] == 12
    orders = client.get("/api/parceldesk/orders")
    assert orders.status_code == 200 and len(orders.json()["orders"]) == 12
    refused = client.post("/api/parceldesk/orders/PD-1037/refund", json={"reason": "too late"})
    assert refused.status_code == 422 and "30 days" in refused.json()["error"]
    accepted = client.post("/api/parceldesk/orders/PD-1042/refund",
                           json={"reason": "Parcel arrived damaged; photos attached."})
    assert accepted.status_code == 200
    body = accepted.json()
    assert body["ok"] and body["refund"]["amount_cents"] == 6_400
    assert body["summary"]["counts"]["refunded"] == 2
    assert any(order["id"] == "PD-1042" and order["status"] == "refunded" for order in body["orders"])


def test_ticket_builds_a_bounded_scenario_without_caller_authority(runtime):
    captured = {}
    real = runtime.engine._admit_scenario

    def capture(scenario, *, request_id, request, defer):
        captured.update({"scenario": scenario, "request_id": request_id, "request": request, "defer": defer})
        return {"id": "PD-900", "status": "queued", "fixture": "TCK"}

    runtime.monkeypatch.setattr(runtime.engine, "_admit_scenario", capture)
    mission = runtime.engine.launch_ticket(
        request_id="ticket-test", title="Show a refund reason",
        outcome="A support agent sees why an order was refunded.",
        area="refunds", risk="low", acceptance="The refund queue shows a reason for every row.",
        constraints="Do not change the pricing contract.",
    )
    assert mission["id"] == "PD-900" and captured["defer"] is False
    scenario = captured["scenario"]
    assert scenario["kind"] == "agent" and scenario["protocol"] == "patch/v1"
    assert scenario["allowed_paths"] == ["app/**", "tests/**"]
    assert scenario["delivery"] == "local" and scenario["fixture"] == "TCK"
    assert scenario["context_files"] == ["app/pricing.py", "tests/test_pricing.py"]
    assert "A support agent sees why an order was refunded." in scenario["task"]
    assert "The refund queue shows a reason for every row." in scenario["task"]
    assert "Do not change the pricing contract." in scenario["task"]
    assert "protected_paths" not in scenario and "model" not in scenario
    runtime.monkeypatch.setattr(runtime.engine, "_admit_scenario", real)


def test_ticket_endpoint_links_the_mission_and_rejects_invalid_input(runtime):
    client = TestClient(runtime.main.app, base_url="http://127.0.0.1")
    response = client.post("/api/parceldesk/tickets", json={
        "title": "Expose the refund reason in the support queue",
        "outcome": "A support agent sees why an order was refunded without opening the ledger.",
        "area": "refunds", "risk": "low",
        "acceptance": "The refund queue shows a reason for every row.",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] and body["ticket"]["mission_id"] == body["mission"]["id"]
    assert body["ticket"]["mission"]["id"] == body["mission"]["id"]
    assert body["ticket"]["mission"]["status"] == "queued"
    assert body["ticket"]["title"].startswith("Expose")
    bad = client.post("/api/parceldesk/tickets", json={
        "title": "x", "outcome": "short", "area": "nope", "risk": "extreme", "acceptance": "x",
    })
    assert bad.status_code == 422


def ticket_patch_runner(**kwargs):
    if kwargs.get("review"):
        return result("sentinel", verdict="OK", reason="Bounded rounding repair.")
    patch = """diff --git a/app/pricing.py b/app/pricing.py
--- a/app/pricing.py
+++ b/app/pricing.py
@@ -27,3 +27,3 @@
     # DEFECT: floor division ignores the started kilogram.
-    kilograms = grams // 1000
+    kilograms = (grams + 999) // 1000
     return FIRST_KG_CENTS + max(0, kilograms - 1) * NEXT_KG_CENTS
"""
    return result("forge", num_turns=1, result_text=json.dumps({"patch": patch, "summary": "Repair started-kilogram rounding."}))


def test_a_ticket_mission_runs_the_full_loop(runtime):
    mission = runtime.engine.launch_ticket(
        request_id="ticket-loop", title="Repair started-kilogram rounding",
        outcome="A 1,001 g parcel is quoted at 8.00 €, not 5.00 €.",
        area="pricing", risk="high",
        acceptance="The pricing contract tests pass for every started kilogram.",
    )
    scenario = json.loads(runtime.store.mission(mission["id"])["contract_json"])["scenario"]
    runtime.monkeypatch.setattr(runtime.module, "agent_runner", SimpleNamespace(run=ticket_patch_runner))
    runtime.engine._execute_mission(mission["id"], scenario)
    stored = runtime.store.mission(mission["id"])
    assert stored["status"] in {"awaiting_human", "completed"}
    assert stored["gate_reason_code"] in {"HUMAN_CHECKPOINT", "HUMAN_APPROVED"}
    tests = json.loads(stored["tests_json"])
    assert tests["passed"] == 19 and tests["acceptance"]["passed"] == 19
    assert stored["fixture"] == "TCK"
