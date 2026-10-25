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


def linked_ticket(runtime):
    ticket = runtime.parceldesk.create_ticket(
        title="Repair started-kilogram rounding",
        outcome="A 1,001 g parcel is quoted at 8.00 EUR, not 5.00 EUR.",
        area="pricing", risk="high",
        acceptance="The started-kilogram boundary cases pass and existing behaviour is unchanged.",
    )
    mission = runtime.engine.launch_ticket(
        request_id=f"ticket-{ticket['id']}", title=ticket["title"], outcome=ticket["outcome"],
        area=ticket["area"], risk=ticket["risk"], acceptance=ticket["acceptance"],
    )
    runtime.parceldesk.link_ticket(ticket["id"], mission["id"])
    runtime.engine.ticket_store = runtime.parceldesk
    return ticket, mission


def test_a_blocked_mission_closes_its_linked_ticket(runtime):
    ticket, mission = linked_ticket(runtime)
    assert runtime.parceldesk.summary()["open_tickets"] == 1
    runtime.engine._block(mission["id"], "TEST_FAILURE", "Two started-kilogram cases failed.")
    closed = runtime.parceldesk.ticket(ticket["id"])
    assert closed["status"] == "blocked"
    assert "Two started-kilogram cases failed." in closed["error"]
    assert runtime.parceldesk.summary()["open_tickets"] == 0


def test_an_approved_mission_closes_the_ticket_and_publishes_a_release(runtime):
    ticket, mission = linked_ticket(runtime)
    scenario = json.loads(runtime.store.mission(mission["id"])["contract_json"])["scenario"]
    runtime.monkeypatch.setattr(runtime.module, "agent_runner", SimpleNamespace(run=ticket_patch_runner))
    runtime.engine._execute_mission(mission["id"], scenario)
    assert runtime.store.mission(mission["id"])["status"] == "awaiting_human"
    assert runtime.parceldesk.ticket(ticket["id"])["status"] == "running"
    assert runtime.engine.approve(mission["id"])
    assert runtime.store.mission(mission["id"])["status"] == "completed"
    assert runtime.parceldesk.ticket(ticket["id"])["status"] == "completed"
    assert runtime.parceldesk.summary()["open_tickets"] == 0
    assert json.loads(runtime.store.get_kv("product_head") or "{}")["mission"] == mission["id"]


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


def test_a_product_ticket_targets_the_quarkus_product(runtime):
    captured = {}

    def capture(scenario, **kwargs):
        captured.update({"scenario": scenario, **kwargs})
        return {"id": "PD-901", "status": "queued", "fixture": "TCK"}

    runtime.monkeypatch.setattr(runtime.engine, "_admit_scenario", capture)
    mission = runtime.engine.launch_ticket(
        request_id="ticket-product", title="Expose refund metadata",
        outcome="A support agent sees the refund reason and amount in the order list.",
        area="support", risk="low",
        acceptance="The order list exposes refund metadata for a refunded order and a new Maven test pins it.",
        constraints="Edit only product/backend DTO and test files.",
        product="product",
    )
    assert mission["id"] == "PD-901"
    scenario = captured["scenario"]
    assert scenario["test_profile"] == "product"
    assert scenario["source"] == "parceldesk-product"
    assert scenario["allowed_paths"] == ["product/**"]
    assert "product/README.md" in scenario["context_files"]
    assert captured["request"]["product"] == "product"


def test_a_product_ticket_is_persisted_with_its_product(desk):
    ticket = desk.create_ticket(
        title="Show the refund note", outcome="A support agent sees the refund note.",
        area="support", risk="low", acceptance="The order list shows the refund note.",
        product="product",
    )
    assert desk.ticket(ticket["id"])["product"] == "product"
    with pytest.raises(ValueError, match="product"):
        desk.create_ticket(
            title="Bad product", outcome="This should be refused.", area="support", risk="low",
            acceptance="The ticket is refused.", product="other",
        )


def test_product_and_legacy_missions_use_separate_release_heads():
    from app.real_engine import _release_head_key
    assert _release_head_key({"test_profile": "product"}) == "product_head:product"
    assert _release_head_key({}) == "product_head"
    assert _release_head_key({"test_profile": "legacy"}) == "product_head"


def test_a_warehouse_ticket_gets_the_frontend_context(runtime):
    captured = {}

    def capture(scenario, **kwargs):
        captured.update({"scenario": scenario, **kwargs})
        return {"id": "PD-902", "status": "queued", "fixture": "TCK"}

    runtime.monkeypatch.setattr(runtime.engine, "_admit_scenario", capture)
    runtime.engine.launch_ticket(
        request_id="ticket-product-frontend", title="Show the refund note",
        outcome="The warehouse refund desk shows the refund amount and reason on one line.",
        area="warehouse", risk="low",
        acceptance="The refund note formatter is covered by a Deno test and every suite passes.",
        product="product",
    )
    files = captured["scenario"]["context_files"]
    assert "product/frontend/tests/format_test.ts" in files
    assert "product/frontend/static/warehouse.js" in files
    assert not any(path.endswith("OrderApiTest.java") for path in files)
