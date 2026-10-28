"""The Factory pulse's validation and safety rules."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from app.pulse import (
    PulseConfigError,
    budget_stop,
    classify,
    is_daily_budget_stop,
    load_tickets,
    next_ticket,
    next_utc_midnight,
    ticket_payload,
)

ROOT = Path(__file__).resolve().parents[1]


def test_the_checked_in_backlog_is_valid_and_unique():
    tickets = load_tickets(ROOT / "config/pulse-tickets.json")
    assert len(tickets) >= 10
    assert len({ticket["id"] for ticket in tickets}) == len(tickets)
    assert all(ticket["risk"] == "low" for ticket in tickets)
    assert all(ticket["product"] == "product" for ticket in tickets)
    assert all(ticket["target_file"].startswith("product/") for ticket in tickets)


def test_a_bad_ticket_is_rejected_before_admission(tmp_path):
    path = tmp_path / "pulse.json"
    path.write_text(json.dumps({"tickets": [{
        "id": "bad", "title": "Short", "outcome": "too short",
        "area": "warehouse", "risk": "low", "acceptance": "too short",
        "product": "product", "unexpected": True,
    }]}))
    with pytest.raises(PulseConfigError, match="unknown fields"):
        load_tickets(path)


def test_budget_guard_refuses_unsettled_or_over_limit():
    assert "unsettled" in (budget_stop({
        "budget_total": 30, "budget_remaining": 19, "budget_unsettled_attempts": 1,
    }, 20.0) or "")
    assert "reached" in (budget_stop({
        "budget_total": 30, "budget_remaining": 9.5, "budget_unsettled_attempts": 0,
    }, 20.0) or "")
    assert "floor" in (budget_stop({
        "budget_total": 30, "budget_remaining": 1.5, "budget_unsettled_attempts": 0,
    }, 29.0) or "")
    assert budget_stop({
        "budget_total": 30, "budget_remaining": 19.7, "budget_unsettled_attempts": 0,
    }, 20.0) is None


def test_only_the_daily_budget_pause_resumes_automatically():
    assert is_daily_budget_stop("today's exposure $15.000000 reached the daily pulse limit $15.00")
    assert is_daily_budget_stop("today's remaining $1.50 is below the $2.00 mission reservation floor")
    assert not is_daily_budget_stop("1 unsettled inference attempt(s); reconcile before new calls")
    assert not is_daily_budget_stop(None)
    now = datetime(2026, 10, 28, 14, 30, tzinfo=timezone.utc)
    assert next_utc_midnight(now) == datetime(2026, 10, 29, 0, 0, tzinfo=timezone.utc)


def test_next_ticket_skips_used_and_does_not_wrap():
    tickets = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    assert next_ticket(tickets, set(), 0) == (0, tickets[0])
    assert next_ticket(tickets, {"a"}, 0) == (1, tickets[1])
    assert next_ticket(tickets, {"a", "b", "c"}, 0) is None
    assert next_ticket(tickets, set(), 2) == (2, tickets[2])
    assert next_ticket(tickets, {"c"}, 2) is None


def test_mission_classification_is_terminal_and_explicit():
    assert classify({"status": "completed"}) == "completed"
    assert classify({"status": "awaiting_human"}) == "waiting_human"
    assert classify({"status": "blocked"}) == "blocked"
    assert classify({"status": "running"}) == "active"
    assert classify({"status": "queued"}) == "active"
    assert classify({"status": "surprise"}) == "unknown"


def test_ticket_payload_contains_only_api_fields():
    ticket = {
        "id": "x", "title": "A title", "outcome": "An observable outcome",
        "area": "warehouse", "risk": "low", "acceptance": "A precise acceptance check",
        "product": "product", "target_file": "product/frontend/tests/x_test.ts",
        "constraints": "",
    }
    payload = ticket_payload(ticket)
    assert "id" not in payload and "target_file" not in payload
    assert payload["title"] == "A title" and payload["product"] == "product"
