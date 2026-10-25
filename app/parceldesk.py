"""ParcelDesk demo domain: orders, refunds and improvement tickets.

This is the user-facing commerce surface, distinct from the small pricing slice the
Factory patches. It stores demo orders in a local SQLite file, applies a deterministic
refund rule, and records improvement tickets before they become missions. No customer
data is real; the seed is synthetic.

The refund rule is intentionally ordinary: a delivered order can be refunded within
30 days, shipping is not refunded, an already-refunded order is refused, and a
high-value refund needs a written reason. The UI uses the same rule as the API.
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import settings

REFUND_WINDOW_DAYS = 30
SHIPPING_CENTS = 500
HIGH_VALUE_CENTS = 20_000
AREAS = ("pricing", "orders", "refunds", "warehouse", "support", "accessibility")
RISKS = ("low", "medium", "high")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def refund_quote(order: dict[str, Any], *, now: datetime | None = None, reason: str = "") -> dict[str, Any]:
    """Return the refund amount or raise ValueError with a support-readable reason."""
    if order["status"] == "refunded":
        raise ValueError("This order has already been refunded.")
    if order["status"] != "delivered":
        raise ValueError("Only delivered orders can be refunded.")
    delivered_at = order.get("delivered_at")
    if not delivered_at:
        raise ValueError("This order has no delivery date.")
    try:
        delivered = datetime.fromisoformat(str(delivered_at))
    except ValueError:
        raise ValueError("The delivery date is invalid.") from None
    if delivered.tzinfo is None:
        delivered = delivered.replace(tzinfo=timezone.utc)
    age = (now or _now()) - delivered
    if age < timedelta(0) or age.days > REFUND_WINDOW_DAYS:
        raise ValueError(f"The refund window is {REFUND_WINDOW_DAYS} days after delivery.")
    amount = int(order["total_cents"]) - int(order["shipping_cents"])
    if amount <= 0:
        raise ValueError("This order has nothing left to refund.")
    if int(order["total_cents"]) >= HIGH_VALUE_CENTS and len(reason.strip()) < 10:
        raise ValueError("A reason of at least 10 characters is required for a high-value refund.")
    return {"amount_cents": amount, "shipping_cents": int(order["shipping_cents"]),
            "window_days": REFUND_WINDOW_DAYS, "high_value": int(order["total_cents"]) >= HIGH_VALUE_CENTS}


SEED = (
    # id, customer, city, item, grams, total_cents, status, days_ago
    ("PD-1042", "Maya Chen", "Lyon", "Walnut desk organizer", 1_800, 6_900, "delivered", 3),
    ("PD-1041", "Noah Petit", "Nantes", "Linen desk mat", 900, 4_200, "delivered", 8),
    ("PD-1040", "Inès Roux", "Bordeaux", "Oak monitor stand", 2_400, 11_500, "in_transit", 0),
    ("PD-1039", "Liam Martin", "Lille", "Studio lamp", 3_100, 18_900, "packing", 0),
    ("PD-1038", "Ava Bernard", "Paris", "Modular shelf kit", 6_400, 24_500, "delivered", 12),
    ("PD-1037", "Ethan Dubois", "Toulouse", "Ceramic planter set", 2_200, 7_800, "delivered", 41),
    ("PD-1036", "Sofia Moreau", "Strasbourg", "Felt storage boxes", 1_500, 5_600, "refunded", 18),
    ("PD-1035", "Lucas Girard", "Montpellier", "Travel cable pouch", 400, 2_900, "delivered", 2),
    ("PD-1034", "Emma Laurent", "Rennes", "Bamboo drawer dividers", 1_100, 4_800, "in_transit", 0),
    ("PD-1033", "Adam Simon", "Nice", "Packing tape dispenser", 300, 1_900, "packing", 0),
    ("PD-1032", "Chloé Michel", "Grenoble", "Recycled shipping labels", 200, 1_400, "delivered", 21),
    ("PD-1031", "Hugo Lefèvre", "Dijon", "Heavy-duty archive box", 8_500, 13_700, "delivered", 6),
)


class ParcelDeskStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or settings.poc_dir / "runtime" / "parceldesk.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY, customer TEXT NOT NULL, city TEXT NOT NULL,
                    item TEXT NOT NULL, grams INTEGER NOT NULL, total_cents INTEGER NOT NULL,
                    shipping_cents INTEGER NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, delivered_at TEXT
                );
                CREATE TABLE IF NOT EXISTS refunds (
                    id TEXT PRIMARY KEY, order_id TEXT NOT NULL UNIQUE REFERENCES orders(id),
                    amount_cents INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tickets (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, outcome TEXT NOT NULL,
                    area TEXT NOT NULL, risk TEXT NOT NULL, acceptance TEXT NOT NULL,
                    constraints TEXT NOT NULL DEFAULT '', mission_id TEXT,
                    status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL,
                    product TEXT NOT NULL DEFAULT 'legacy'
                );
                """
            )
            columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(tickets)")}
            if "product" not in columns:
                self._conn.execute("ALTER TABLE tickets ADD COLUMN product TEXT NOT NULL DEFAULT 'legacy'")
        self._seed()

    def _seed(self) -> None:
        with self._lock, self._conn:
            if self._conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]:
                return
            now = _now()
            for order_id, customer, city, item, grams, total, status, days in SEED:
                delivered = _iso(now - timedelta(days=days)) if status in {"delivered", "refunded"} else None
                self._conn.execute(
                    "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (order_id, customer, city, item, grams, total, SHIPPING_CENTS, status,
                     _iso(now - timedelta(days=days + 2)), delivered),
                )
            self._conn.execute(
                "INSERT INTO refunds VALUES (?, ?, ?, ?, ?)",
                ("RF-9001", "PD-1036", 5_100, "Parcel arrived with a crushed corner; customer supplied photos.",
                 _iso(now - timedelta(days=16))),
            )

    def close(self) -> None:
        self._conn.close()

    def _order(self, order_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return dict(row) if row else None

    def orders(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT o.*, r.id AS refund_id, r.amount_cents AS refund_amount, r.reason AS refund_reason "
                "FROM orders o LEFT JOIN refunds r ON r.order_id = o.id ORDER BY o.created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def summary(self) -> dict[str, Any]:
        with self._lock:
            orders = [dict(row) for row in self._conn.execute("SELECT * FROM orders").fetchall()]
            refunded = self._conn.execute("SELECT COALESCE(SUM(amount_cents), 0), COUNT(*) FROM refunds").fetchone()
            tickets = self._conn.execute(
                "SELECT COUNT(*) FROM tickets WHERE status IN ('queued', 'running')"
            ).fetchone()[0]
        counts = {status: sum(1 for order in orders if order["status"] == status)
                  for status in ("packing", "in_transit", "delivered", "refunded")}
        return {
            "orders": len(orders),
            "counts": counts,
            "revenue_cents": sum(order["total_cents"] for order in orders if order["status"] != "refunded"),
            "refunded_cents": int(refunded[0]),
            "refunds": int(refunded[1]),
            "open_tickets": int(tickets),
            "refund_window_days": REFUND_WINDOW_DAYS,
        }

    def refund(self, order_id: str, reason: str, *, now: datetime | None = None) -> dict[str, Any]:
        reason = " ".join(str(reason or "").split())
        if len(reason) > 500 or "\0" in reason:
            raise ValueError("A refund reason must be at most 500 characters.")
        with self._lock, self._conn:
            order = self._order(order_id)
            if not order:
                raise ValueError("Unknown order.")
            quote = refund_quote(order, now=now, reason=reason)
            refund_id = "RF-" + uuid.uuid4().hex[:8].upper()
            created = _iso(now or _now())
            self._conn.execute(
                "INSERT INTO refunds VALUES (?, ?, ?, ?, ?)",
                (refund_id, order_id, quote["amount_cents"], reason or "No reason provided.", created),
            )
            self._conn.execute("UPDATE orders SET status = 'refunded' WHERE id = ?", (order_id,))
        return {"id": refund_id, "order_id": order_id, "amount_cents": quote["amount_cents"],
                "reason": reason or "No reason provided.", "created_at": created, **quote}

    def create_ticket(self, *, title: str, outcome: str, area: str, risk: str,
                      acceptance: str, constraints: str = "", product: str = "legacy") -> dict[str, Any]:
        title, outcome = " ".join(title.split()), " ".join(outcome.split())
        acceptance, constraints = acceptance.strip(), constraints.strip()
        if not 4 <= len(title) <= 120:
            raise ValueError("A ticket title requires 4–120 characters.")
        if not 10 <= len(outcome) <= 500:
            raise ValueError("An observable outcome requires 10–500 characters.")
        if area not in AREAS:
            raise ValueError("Unknown ticket area.")
        if risk not in RISKS:
            raise ValueError("Risk must be low, medium or high.")
        if product not in {"legacy", "product"}:
            raise ValueError("Unknown ticket product.")
        if not 10 <= len(acceptance) <= 2_000:
            raise ValueError("Acceptance checks require 10–2,000 characters.")
        if len(constraints) > 1_000 or any("\0" in value for value in (title, outcome, acceptance, constraints)):
            raise ValueError("Ticket text is invalid or too long.")
        ticket_id = "TCK-" + uuid.uuid4().hex[:8].upper()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO tickets (id, title, outcome, area, risk, acceptance, constraints, "
                "mission_id, status, error, created_at, product) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ticket_id, title, outcome, area, risk, acceptance, constraints, None, "queued", None,
                 _iso(_now()), product),
            )
        return self.ticket(ticket_id)

    def ticket(self, ticket_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not row:
            raise ValueError("Unknown ticket.")
        return dict(row)

    def tickets(self, limit: int = 30) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tickets ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def link_ticket(self, ticket_id: str, mission_id: str, status: str = "running") -> dict[str, Any]:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tickets SET mission_id = ?, status = ?, error = NULL WHERE id = ?",
                (mission_id, status, ticket_id),
            )
        return self.ticket(ticket_id)

    def fail_ticket(self, ticket_id: str, error: str) -> dict[str, Any]:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tickets SET status = 'failed', error = ? WHERE id = ?",
                (str(error)[:500], ticket_id),
            )
        return self.ticket(ticket_id)

    def sync_mission(self, mission_id: str, status: str, error: str | None = None) -> int:
        """Close the linked ticket when its mission reaches a terminal state.

        The warehouse board reads the mission for the live step, but the open-ticket
        count comes from this table. Leaving every ticket at ``running`` made the
        count grow forever after real, terminal missions.
        """
        if status not in {"completed", "blocked"}:
            return 0
        note = str(error)[:500] if error else None
        with self._lock, self._conn:
            return self._conn.execute(
                "UPDATE tickets SET status = ?, error = ? WHERE mission_id = ? AND status IN ('queued', 'running')",
                (status, note, mission_id),
            ).rowcount


parceldesk_store = ParcelDeskStore()
