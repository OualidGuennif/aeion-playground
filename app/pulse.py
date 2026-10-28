"""Pure helpers for the operator-run Factory pulse.

The pulse is host-side operator tooling. It admits bounded low-risk product
tickets on a cadence, but it cannot approve a checkpoint, seal a packet or
change the Gate. Keeping the validation and budget rules here makes them
testable without a running cockpit.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

AREAS = {"pricing", "orders", "refunds", "warehouse", "support", "accessibility"}
RISKS = {"low", "medium", "high"}
PRODUCTS = {"legacy", "product"}
TICKET_REQUIRED = {"id", "title", "outcome", "area", "risk", "acceptance", "product"}
TICKET_OPTIONAL = {"constraints", "target_file"}
TICKET_FIELDS = TICKET_REQUIRED - {"id"} | TICKET_OPTIONAL - {"target_file"}
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
TARGET_PATTERN = re.compile(r"product/[A-Za-z0-9_./-]+")


class PulseConfigError(ValueError):
    """The pulse backlog is malformed; no mission should be admitted."""


def load_tickets(path: Path) -> list[dict[str, Any]]:
    """Load and validate the operator-editable ticket backlog."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise PulseConfigError(f"Cannot read pulse backlog: {error}") from error
    if isinstance(data, dict):
        tickets = data.get("tickets")
    else:
        tickets = data
    if not isinstance(tickets, list) or not tickets:
        raise PulseConfigError("The pulse backlog must contain a non-empty tickets list.")
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(tickets):
        if not isinstance(raw, dict):
            raise PulseConfigError(f"Ticket {index} is not an object.")
        keys = set(raw)
        unknown = keys - (TICKET_REQUIRED | TICKET_OPTIONAL)
        missing = TICKET_REQUIRED - keys
        if unknown:
            raise PulseConfigError(f"Ticket {index} has unknown fields: {sorted(unknown)}")
        if missing:
            raise PulseConfigError(f"Ticket {index} is missing fields: {sorted(missing)}")
        ticket = {key: raw[key] for key in TICKET_REQUIRED | TICKET_OPTIONAL if key in raw}
        ticket_id = ticket["id"]
        if not isinstance(ticket_id, str) or not ID_PATTERN.fullmatch(ticket_id):
            raise PulseConfigError(f"Ticket {index} has an invalid id.")
        if ticket_id in seen:
            raise PulseConfigError(f"Duplicate ticket id: {ticket_id}")
        seen.add(ticket_id)
        for key, low, high in (("title", 4, 120), ("outcome", 10, 500), ("acceptance", 10, 2_000)):
            value = ticket[key]
            if not isinstance(value, str) or not low <= len(value.strip()) <= high:
                raise PulseConfigError(f"Ticket {ticket_id} has an invalid {key}.")
        constraints = ticket.get("constraints", "")
        if not isinstance(constraints, str) or len(constraints) > 1_000:
            raise PulseConfigError(f"Ticket {ticket_id} has invalid constraints.")
        if ticket["area"] not in AREAS:
            raise PulseConfigError(f"Ticket {ticket_id} has an unknown area.")
        if ticket["risk"] not in RISKS:
            raise PulseConfigError(f"Ticket {ticket_id} has an unknown risk.")
        if ticket["product"] not in PRODUCTS:
            raise PulseConfigError(f"Ticket {ticket_id} has an unknown product.")
        target = ticket.get("target_file")
        if target is not None:
            if not isinstance(target, str) or not TARGET_PATTERN.fullmatch(target) or ".." in target:
                raise PulseConfigError(f"Ticket {ticket_id} has an invalid target_file.")
        result.append(ticket)
    return result


def ticket_payload(ticket: dict[str, Any]) -> dict[str, Any]:
    """Return only the fields accepted by POST /api/parceldesk/tickets."""
    return {key: ticket[key] for key in TICKET_FIELDS if key in ticket}


def budget_stop(kpis: dict[str, Any], max_exposure_usd: float, reservation_floor: float = 2.0) -> str | None:
    """Return a stop reason when another mission must not be admitted."""
    if not isinstance(kpis, dict):
        return "the ledger snapshot has no KPI data"
    try:
        total = float(kpis.get("budget_total") or 0)
        remaining = float(kpis.get("budget_remaining") or 0)
        unsettled = int(kpis.get("budget_unsettled_attempts") or 0)
    except (TypeError, ValueError):
        return "the ledger budget fields are not numeric"
    if unsettled > 0:
        return f"{unsettled} unsettled inference attempt(s); reconcile before new calls"
    exposure = total - remaining
    if exposure >= max_exposure_usd:
        return f"today's exposure ${exposure:.6f} reached the daily pulse limit ${max_exposure_usd:.2f}"
    if remaining < reservation_floor:
        return f"today's remaining ${remaining:.6f} is below the ${reservation_floor:.2f} mission reservation floor"
    return None


def is_daily_budget_stop(reason: str | None) -> bool:
    """True when the pulse can resume by itself after the UTC-day reset."""
    return bool(reason) and ("daily pulse limit" in reason or "mission reservation floor" in reason)


def next_utc_midnight(now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (current.astimezone(timezone.utc) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def next_ticket(tickets: list[dict[str, Any]], used: set[str], cursor: int) -> tuple[int, dict[str, Any]] | None:
    """Return the first unused ticket at or after the cursor; no wrapping."""
    for index in range(max(0, cursor), len(tickets)):
        if tickets[index]["id"] not in used:
            return index, tickets[index]
    return None


def classify(mission: dict[str, Any], delivery: dict[str, Any] | None = None,
             delivery_error: str | None = None) -> str:
    """Map a mission record to the pulse's terminal vocabulary."""
    status = str(mission.get("status") or "")
    if status == "awaiting_human":
        return "waiting_human"
    if status == "completed":
        return "completed"
    if status in {"blocked", "failed", "denied"}:
        return "blocked"
    if status in {"queued", "running"}:
        return "active"
    return "unknown"


def delivery_summary(delivery: dict[str, Any] | None, delivery_error: str | None) -> dict[str, Any]:
    if delivery:
        return {
            "commit": delivery.get("commit"),
            "packet_digest": delivery.get("packet_digest"),
            "tests": delivery.get("tests"),
        }
    if delivery_error:
        return {"error": delivery_error}
    return {}
