"""Persistent mission ledger.

SQLite is the canonical local ledger. Every mission, step, agent run, gate
decision, event and evidence packet is stored here. The JSONL audit trail and
the metadata-only OTLP stream are projections of the same facts.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import random
import sqlite3
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from .config import settings
from .event_protocol import metadata as event_metadata
from .operator_settings import effective_budget

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS missions (
    id TEXT PRIMARY KEY,
    scenario TEXT NOT NULL,
    fixture TEXT NOT NULL,
    title TEXT NOT NULL,
    subtitle TEXT NOT NULL,
    risk TEXT NOT NULL,
    priority TEXT NOT NULL,
    profile TEXT NOT NULL,
    status TEXT NOT NULL,
    outcome TEXT,
    progress REAL NOT NULL DEFAULT 0,
    current_step TEXT,
    current_agent TEXT,
    gate_decision TEXT,
    gate_reason TEXT,
    gate_reason_code TEXT,
    human_approval TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    sandbox_path TEXT,
    base_commit TEXT,
    diff_digest TEXT,
    diff_path TEXT,
    tests_json TEXT,
    diff_json TEXT,
    gate_json TEXT,
    paperclip_issue_id TEXT,
    paperclip_identifier TEXT,
    paperclip_url TEXT,
    blocked_reason TEXT,
    request_id TEXT,
    contract_json TEXT
);

CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    agent TEXT NOT NULL,
    label TEXT NOT NULL,
    detail TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    progress REAL NOT NULL DEFAULT 0,
    started_at TEXT,
    ended_at TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    evidence_path TEXT,
    FOREIGN KEY (mission_id) REFERENCES missions(id)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    actor TEXT NOT NULL,
    mission TEXT,
    fixture TEXT,
    message TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    payload TEXT
);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    runtime TEXT NOT NULL,
    model TEXT,
    status TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    exit_code INTEGER,
    error TEXT,
    stdout_path TEXT,
    stderr_path TEXT,
    result_text TEXT,
    verdict TEXT,
    reason TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS event_outbox (
    event_id INTEGER PRIMARY KEY REFERENCES events(id),
    metadata_json TEXT NOT NULL,
    audit_sent INTEGER NOT NULL DEFAULT 0,
    collector_sent INTEGER NOT NULL DEFAULT 0,
    owner TEXT,
    lease_until REAL NOT NULL DEFAULT 0,
    retry_at REAL NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS event_outbox_pending ON event_outbox(retry_at,lease_until,event_id)
    WHERE audit_sent=0 OR collector_sent=0;

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    mission TEXT NOT NULL,
    scope TEXT NOT NULL,
    digest TEXT NOT NULL,
    checks INTEGER NOT NULL DEFAULT 0,
    review TEXT NOT NULL,
    status TEXT NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT
);

CREATE TABLE IF NOT EXISTS inference_attempts (
    run_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES missions(id),
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    policy_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    reserved_usd REAL NOT NULL,
    held_usd REAL NOT NULL,
    reconciled_usd REAL NOT NULL DEFAULT 0,
    reconcile_note TEXT,
    lease_id TEXT UNIQUE,
    created_at TEXT NOT NULL,
    settled_at TEXT
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class InferenceBudgetError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.db_path
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Full event content stays in the private ledger. SQLite creates new WAL/
        # SHM files with the database mode; also restrict existing sidecars.
        for target in (self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            if target == self.path:
                flags |= os.O_CREAT
            try:
                descriptor = os.open(target, flags, 0o600)
            except FileNotFoundError:
                continue
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                    raise ValueError("Ledger files must be regular operator-owned files without hard links")
                os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 10000")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(missions)")}
            for name in ("request_id", "contract_json"):
                if name not in columns:
                    self._conn.execute(f"ALTER TABLE missions ADD COLUMN {name} TEXT")
            attempt_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(inference_attempts)")}
            if "reconciled_usd" not in attempt_columns:
                self._conn.execute("ALTER TABLE inference_attempts ADD COLUMN reconciled_usd REAL NOT NULL DEFAULT 0")
            if "reconcile_note" not in attempt_columns:
                self._conn.execute("ALTER TABLE inference_attempts ADD COLUMN reconcile_note TEXT")
            self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS missions_request_id ON missions(request_id)")
            self._conn.execute("INSERT OR IGNORE INTO kv(key,value) VALUES('observability:instance_id',?)", (uuid.uuid4().hex,))
            self.instance_id = self._conn.execute("SELECT value FROM kv WHERE key='observability:instance_id'").fetchone()[0]
            self._conn.commit()

    # ------------------------------------------------------------------ generic
    def _execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cursor

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    # ----------------------------------------------------------------- missions
    @staticmethod
    def _request(record: dict[str, Any]) -> dict[str, Any]:
        contract = json.loads(record.get("contract_json") or "{}")
        if not isinstance(contract, dict):
            raise ValueError("Stored request contract is malformed")
        if "request" in contract:
            if not isinstance(contract["request"], dict):
                raise ValueError("Stored request identity is malformed")
            return contract["request"]
        scenario = contract.get("scenario") or {}
        if not isinstance(scenario, dict):
            raise ValueError("Stored scenario is malformed")
        # Contracts admitted before explicit task requests used fixture + patch.
        return {"fixture": record["fixture"], "patch": scenario.get("patch")}

    def replay_request(self, request_id: str | None, request: dict[str, Any]) -> dict[str, Any] | None:
        if request_id is None:
            return None
        rows = self._query("SELECT * FROM missions WHERE request_id = ?", (request_id,))
        if not rows:
            return None
        existing = dict(rows[0])
        if self._request(existing) != request:
            raise ValueError("Request id already belongs to another task.")
        return existing

    def admit_mission(
        self, record: dict[str, Any], *, defer: bool, capacity: int, queue_limit: int,
    ) -> tuple[dict[str, Any], bool]:
        """Allocate identity, enforce bounds and deduplicate in one transaction."""
        columns = (
            "id",
            "scenario",
            "fixture",
            "title",
            "subtitle",
            "risk",
            "priority",
            "profile",
            "status",
            "outcome",
            "progress",
            "current_step",
            "current_agent",
            "created_at",
            "sandbox_path",
            "base_commit",
            "paperclip_issue_id",
            "paperclip_identifier",
            "paperclip_url",
            "blocked_reason",
            "request_id",
            "contract_json",
        )
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            if record.get("request_id"):
                existing = self.replay_request(record["request_id"], self._request(record))
                if existing:
                    return existing, False
            if defer:
                count = self._conn.execute("SELECT COUNT(*) FROM missions WHERE status = 'queued'").fetchone()[0]
                if count >= queue_limit:
                    raise RuntimeError("Persistent mission queue is full.")
            else:
                count = self._conn.execute(
                    "SELECT COUNT(*) FROM missions WHERE status IN ('queued', 'running', 'awaiting_human')",
                ).fetchone()[0]
                if count >= capacity:
                    raise RuntimeError("Mission capacity reached. Finish the active mission or its human checkpoint first.")
            number = self._conn.execute(
                "SELECT COALESCE(MAX(CAST(SUBSTR(id, 4) AS INTEGER)), 0) + 1 FROM missions WHERE id GLOB 'PD-[0-9]*'",
            ).fetchone()[0]
            record = {**record, "id": f"PD-{number:03d}"}
            self._conn.execute(
                f"INSERT INTO missions ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                [record.get(column) for column in columns],
            )
        return self.mission(record["id"]) or record, True

    def queued_missions(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._query(
            "SELECT * FROM missions WHERE status = 'queued' ORDER BY created_at, id",
        )]

    def running_missions(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._query("SELECT * FROM missions WHERE status = 'running'")]

    def claim_mission(self, mission_id: str, capacity: int) -> bool:
        """One worker can claim a queued mission, across threads and processes."""
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            occupied = self._conn.execute(
                "SELECT COUNT(*) FROM missions WHERE status IN ('running', 'awaiting_human')",
            ).fetchone()[0]
            if occupied >= capacity:
                return False
            result = self._conn.execute(
                "UPDATE missions SET status = 'running', started_at = COALESCE(started_at, ?) WHERE id = ? AND status = 'queued'",
                (utc_now(), mission_id),
            )
            return result.rowcount == 1

    def requeue_review(self, mission_id: str, intent: dict[str, Any]) -> bool:
        """Persist one bounded technical-review retry together with its queue transition."""
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            if self._conn.execute("SELECT 1 FROM kv WHERE key = ?", (f"review_retry:{mission_id}",)).fetchone():
                return False
            changed = self._conn.execute(
                "UPDATE missions SET status = 'queued', current_step = 'Review retry queued' "
                "WHERE id = ? AND status = 'awaiting_human' AND human_approval IS NULL AND diff_digest = ? AND contract_json = ?",
                (mission_id, intent["diff_digest"], intent["contract_json"]),
            )
            if changed.rowcount != 1:
                return False
            self._conn.execute("INSERT INTO kv (key, value) VALUES (?, ?)",
                               (f"review_retry:{mission_id}", json.dumps(intent, sort_keys=True)))
            return True

    @contextmanager
    def mission_lock(self, mission_id: str):
        """Kernel-released ownership lets restart recovery distinguish live workers."""
        if not mission_id.startswith("PD-") or not mission_id[3:].isdigit():
            raise ValueError("Invalid mission identity")
        directory = self.path.parent / (self.path.name + ".workers")
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / (mission_id + ".lock")).open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def update_mission(self, mission_id: str, **fields: Any) -> None:
        if not fields:
            return
        for key in ("tests_json", "diff_json", "gate_json"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = json.dumps(fields[key], ensure_ascii=False)
        assignments = ", ".join(f"{key} = ?" for key in fields)
        self._execute(f"UPDATE missions SET {assignments} WHERE id = ?", [*fields.values(), mission_id])

    def mission(self, mission_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM missions WHERE id = ?", (mission_id,))
        return dict(rows[0]) if rows else None

    def missions(self, limit: int = 40) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM missions ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in rows]

    def count_missions(self) -> int:
        rows = self._query("SELECT COUNT(*) AS count FROM missions")
        return int(rows[0]["count"]) if rows else 0

    # -------------------------------------------------------------------- steps
    def add_step(
        self,
        *,
        mission_id: str,
        idx: int,
        agent: str,
        label: str,
        detail: str,
        status: str = "pending",
        progress: float = 0.0,
    ) -> int:
        cursor = self._execute(
            """INSERT INTO steps (mission_id, idx, agent, label, detail, status, progress, started_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (mission_id, idx, agent, label, detail, status, progress, utc_now() if status == "running" else None),
        )
        return int(cursor.lastrowid)

    def update_step(self, step_id: int, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in fields)
        self._execute(f"UPDATE steps SET {assignments} WHERE id = ?", [*fields.values(), step_id])

    def steps(self, mission_id: str) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM steps WHERE mission_id = ? ORDER BY idx", (mission_id,))
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------- events
    def add_event(
        self,
        *,
        type: str,
        actor: str,
        message: str,
        mission: str | None = None,
        fixture: str | None = None,
        severity: str = "info",
        payload: dict[str, Any] | None = None,
        ts: str | None = None,
    ) -> dict[str, Any]:
        timestamp = ts or utc_now()
        event = {"ts": timestamp, "type": type, "actor": actor, "mission": mission, "fixture": fixture,
                 "message": message, "severity": severity, "payload": payload or {}}
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            cursor = self._conn.execute(
                """INSERT INTO events (ts, type, actor, mission, fixture, message, severity, payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (timestamp, type, actor, mission, fixture, message, severity,
                 json.dumps(payload, ensure_ascii=False) if payload else None),
            )
            event["id"] = int(cursor.lastrowid)
            record = event_metadata(self.instance_id, event)
            self._conn.execute("INSERT INTO event_outbox(event_id,metadata_json) VALUES(?,?)",
                               (event["id"], json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False)))
        event["event_id"] = record["event_id"]
        return event

    def claim_outbox(self, *, now: float | None = None) -> dict | None:
        now = time.time() if now is None else now
        owner = uuid.uuid4().hex
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute("""SELECT * FROM event_outbox WHERE (audit_sent=0 OR collector_sent=0)
                AND (last_error IS NULL OR last_error!='collector_rejected')
                AND retry_at<=? AND lease_until<=? ORDER BY event_id LIMIT 1""", (now, now)).fetchone()
            if not row:
                return None
            self._conn.execute("UPDATE event_outbox SET owner=?,lease_until=? WHERE event_id=?", (owner, now + 30, row["event_id"]))
            result = dict(row)
            event = dict(self._conn.execute("SELECT * FROM events WHERE id=?", (row["event_id"],)).fetchone())
        event["payload"] = json.loads(event["payload"]) if event["payload"] else {}
        result.update(owner=owner, lease_until=now + 30, event=event, metadata=json.loads(result["metadata_json"]))
        return result

    def acknowledge_outbox(self, event_id: int, owner: str, sink: str) -> bool:
        column = {"audit": "audit_sent", "collector": "collector_sent"}[sink]
        with self._lock, self._conn:
            changed = self._conn.execute(f"UPDATE event_outbox SET {column}=1 WHERE event_id=? AND owner=?", (event_id, owner))
            return changed.rowcount == 1

    def release_outbox(self, event_id: int, owner: str, error: str | None = None, *, now: float | None = None, retry_at: float | None = None) -> bool:
        now = time.time() if now is None else now
        # Diagnostics are codes, not exception messages or raw HTTP response bodies.
        if error not in {None, "audit_unavailable", "collector_unavailable", "collector_rejected", "persistence_unavailable"}:
            raise ValueError("Unknown outbox error code")
        with self._lock, self._conn:
            row = self._conn.execute("SELECT attempts FROM event_outbox WHERE event_id=? AND owner=?", (event_id, owner)).fetchone()
            if not row:
                return False
            delay = min(60, 2 ** min(6, row["attempts"])) * random.uniform(.5, 1.5) if error else 0
            due = max(now + delay, retry_at or 0)
            self._conn.execute("""UPDATE event_outbox SET owner=NULL,lease_until=0,retry_at=?,
                attempts=attempts+?,last_error=? WHERE event_id=? AND owner=?""",
                (due, int(error is not None), error, event_id, owner))
            return True

    def outbox_status(self) -> dict:
        with self._lock:
            row = self._conn.execute("""SELECT COUNT(*) AS total,
                COALESCE(SUM(audit_sent=0),0) AS audit_pending,COALESCE(SUM(collector_sent=0),0) AS collector_pending,
                COALESCE(SUM(last_error IS NOT NULL AND (audit_sent=0 OR collector_sent=0)),0) AS failed
                FROM event_outbox""").fetchone()
        return dict(row)

    def requeue_rejected_event(self, event_id: int) -> bool:
        """Explicit operator retry after correcting a permanent OTLP rejection."""
        with self._lock, self._conn:
            result = self._conn.execute("""UPDATE event_outbox SET last_error=NULL,retry_at=0,owner=NULL,lease_until=0
                WHERE event_id=? AND last_error='collector_rejected'""", (event_id,))
            return result.rowcount == 1

    def events(self, limit: int = 120) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        output = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload"]) if item.get("payload") else {}
            output.append(item)
        return output

    def count_events(self) -> int:
        rows = self._query("SELECT COUNT(*) AS count FROM events")
        return int(rows[0]["count"]) if rows else 0

    # --------------------------------------------------------------- agent runs
    @staticmethod
    def _budget_snapshot(db) -> dict:
        spent = sum((Decimal(str(row[0])) for row in db.execute("SELECT cost_usd FROM missions")), Decimal(0))
        carry = db.execute("SELECT value FROM kv WHERE key='budget:carried_usage'").fetchone()
        carried = Decimal(str(json.loads(carry[0])["cost_usd"])) if carry else Decimal(0)
        rows = db.execute("SELECT state,held_usd FROM inference_attempts WHERE state IN ('reserved','unknown','overrun')").fetchall()
        held = sum((Decimal(str(row[1])) for row in rows), Decimal(0))
        reconciled = db.execute("SELECT COALESCE(SUM(reconciled_usd),0) FROM inference_attempts").fetchone()[0]
        reconciled = Decimal(str(reconciled or 0))
        return {"reported_usd": float(spent), "carried_usd": float(carried), "held_usd": float(held),
                "reconciled_usd": float(reconciled), "unsettled_attempts": len(rows),
                "exposure_usd": float(spent + carried + held + reconciled)}

    def budget_snapshot(self) -> dict:
        with self._lock:
            return self._budget_snapshot(self._conn)

    def register_carried_usage(self, cost_usd: float, source_digest: str) -> bool:
        """One immutable migration observation; never rewrite mission/proof history."""
        if (isinstance(cost_usd, bool) or not isinstance(cost_usd, (int, float)) or not math.isfinite(cost_usd)
                or not 0 <= cost_usd <= 100 or not isinstance(source_digest, str) or len(source_digest) != 64
                or any(value not in "0123456789abcdef" for value in source_digest)):
            raise ValueError("A bounded observed cost and source digest are required")
        value = json.dumps({"cost_usd": cost_usd, "source_digest": source_digest}, sort_keys=True)
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            original = self._conn.execute("SELECT value FROM kv WHERE key='budget:carried_usage'").fetchone()
            if original:
                if original[0] != value:
                    raise ValueError("Carried usage is immutable")
                return False
            if self._conn.execute("SELECT 1 FROM inference_attempts LIMIT 1").fetchone():
                raise ValueError("Register carried usage before admitting inference attempts")
            self._conn.execute("INSERT INTO kv(key,value) VALUES('budget:carried_usage',?)", (value,))
            return True

    def reserve_attempt(self, *, run_id: str, mission_id: str, agent: str, model: str, mission_limit: float) -> dict:
        """Serialize dispatch and reserve the full remaining mission envelope before IO."""
        if (not isinstance(run_id, str) or not run_id.startswith("run-") or not 5 <= len(run_id) <= 80
                or agent not in {"forge", "sentinel"} or isinstance(mission_limit, bool)
                or not isinstance(mission_limit, (int, float)) or not math.isfinite(mission_limit) or not 0 < mission_limit <= 2):
            raise ValueError("Invalid inference reservation bounds")
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            mission = self._conn.execute("SELECT status,cost_usd,contract_json FROM missions WHERE id=?", (mission_id,)).fetchone()
            if not mission or mission["status"] != "running":
                raise InferenceBudgetError("MISSION_NOT_RUNNING", "Inference requires its running mission")
            contract = json.loads(mission["contract_json"] or "{}")
            policy = contract.get("routing") or {}
            executor = contract.get("runtime", {}).get("inference_executor", {})
            if (executor.get("backend") != "docker-opencode/v1" or model not in policy.get("roles", {}).get(agent, {}).get("models", [])):
                raise InferenceBudgetError("INFERENCE_NOT_ADMITTED", "Model/role/isolated executor is not admitted")
            deadline = datetime.fromisoformat(policy["authorization_expires_at"].replace("Z", "+00:00"))
            if deadline.tzinfo is None or deadline <= datetime.now(timezone.utc):
                raise InferenceBudgetError("AUTHORIZATION_EXPIRED", "No model dispatch after its authorization expiry")
            budget = effective_budget(policy["budget_usd"])
            if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget) or not 0 < budget <= 100:
                raise ValueError("Invalid admitted budget")
            if self._conn.execute("SELECT 1 FROM inference_attempts WHERE run_id=?", (run_id,)).fetchone():
                raise InferenceBudgetError("ATTEMPT_ALREADY_RESERVED", "An admitted attempt cannot be dispatched twice")
            snapshot = self._budget_snapshot(self._conn)
            if snapshot["unsettled_attempts"]:
                raise InferenceBudgetError("EXPOSURE_HELD", "Unsettled inference exposure blocks new calls; no automatic refund/retry")
            reserved = Decimal(str(mission_limit)) - Decimal(str(mission["cost_usd"]))
            if reserved <= 0:
                raise InferenceBudgetError("MISSION_BUDGET_EXHAUSTED", "The mission's reported budget is exhausted")
            if Decimal(str(snapshot["exposure_usd"])) + reserved > Decimal(str(budget)):
                raise InferenceBudgetError("BUDGET_RESERVATION_DENIED", "The full remaining mission envelope does not fit the reported local budget")
            self._conn.execute("""INSERT INTO inference_attempts
                (run_id,mission_id,agent,model,policy_digest,state,reserved_usd,held_usd,created_at)
                VALUES(?,?,?,?,?,'reserved',?,?,?)""",
                (run_id, mission_id, agent, model, policy["digest"], float(reserved), float(reserved), utc_now()))
        return self.attempt(run_id)

    def attempt(self, run_id: str) -> dict | None:
        rows = self._query("SELECT * FROM inference_attempts WHERE run_id=?", (run_id,))
        return dict(rows[0]) if rows else None

    def reconcile_unknown_attempt(self, run_id: str, note: str) -> dict:
        """Operator settlement: charge the full held reservation, never zero.

        This is only for a mediator outcome that lost its usage record. The
        provider may have billed; the conservative charge is the whole reserved
        envelope. The attempt keeps its original reservation, gains the charge
        and note, and no longer blocks new inference. This is not an invoice
        reconciliation: it can only overstate, never understate, the spend.
        """
        if not isinstance(run_id, str) or not run_id.startswith("run-") or len(run_id) > 80:
            raise ValueError("Invalid attempt identity")
        note = " ".join(str(note or "").split())
        if not 8 <= len(note) <= 500:
            raise ValueError("A reconciliation note of 8-500 characters is required")
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute("SELECT * FROM inference_attempts WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                raise ValueError("Unknown inference attempt")
            if row["state"] != "unknown" or float(row["held_usd"]) <= 0:
                raise ValueError("Only an unknown attempt with a held reservation can be reconciled")
            charge = float(row["held_usd"])
            self._conn.execute(
                "UPDATE inference_attempts SET state='reconciled', held_usd=0, reconciled_usd=?, "
                "reconcile_note=?, settled_at=? WHERE run_id=?",
                (charge, note, utc_now(), run_id),
            )
            return dict(self._conn.execute("SELECT * FROM inference_attempts WHERE run_id=?", (run_id,)).fetchone())

    def bind_attempt_lease(self, run_id: str, lease_id: str) -> None:
        if not isinstance(lease_id, str) or len(lease_id) != 32 or any(value not in "0123456789abcdef" for value in lease_id):
            raise ValueError("Invalid mediator lease identity")
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            changed = self._conn.execute("""UPDATE inference_attempts SET lease_id=?
                WHERE run_id=? AND state='reserved' AND (lease_id IS NULL OR lease_id=?)""", (lease_id, run_id, lease_id))
            if changed.rowcount != 1:
                raise ValueError("A lease requires its unique active reservation")

    def hold_attempt_unknown(self, run_id: str) -> None:
        self._execute("UPDATE inference_attempts SET state='unknown' WHERE run_id=? AND state='reserved'", (run_id,))

    def hold_mission_attempts_unknown(self, mission_id: str) -> None:
        self._execute("UPDATE inference_attempts SET state='unknown' WHERE mission_id=? AND state='reserved'", (mission_id,))

    @staticmethod
    def _attempt_outcome(run, attempt, contract) -> str:
        raw = run.get("raw") or {}
        zero = run.get("cost_usd", 0) == run.get("tokens_in", 0) == run.get("tokens_out", 0) == 0
        if not attempt["lease_id"] and raw.get("dispatch") == "not_started" and zero:
            return "not_dispatched"
        mediated = raw.get("inference") or {}
        if (mediated.get("lease_id") != attempt["lease_id"] or not attempt["lease_id"]
                or mediated.get("run_id") != run["run_id"]
                or mediated.get("executor") != contract.get("runtime", {}).get("inference_executor")):
            return "unknown"
        if mediated.get("state") == "revoked" and zero:
            return "not_dispatched"
        if mediated.get("state") == "refused" and zero and mediated.get("refusal") and mediated.get("response_sha256"):
            return "refused"
        usage = mediated.get("usage") or {}
        if (mediated.get("state") == "completed" and mediated.get("response_sha256")
                and raw.get("steps") == 1 and raw.get("tool_calls") == 0 and raw.get("session_id")
                and raw.get("finish_reason") in {"stop", "length"}
                and usage.get("prompt_tokens") == run.get("tokens_in", 0)
                and usage.get("completion_tokens") == run.get("tokens_out", 0)):
            return "settled"
        return "unknown"

    def add_run(self, run: dict[str, Any]) -> bool:
        """Record one immutable result and settle its known usage in one transaction."""
        if not isinstance(run.get("run_id"), str) or not 1 <= len(run["run_id"]) <= 128:
            raise ValueError("A bounded nonempty run identity is required")
        cost = run.get("cost_usd", 0)
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
            raise ValueError("Reported run cost must be finite and nonnegative")
        tokens = [run.get(key, 0) for key in ("tokens_in", "tokens_out")]
        if any(type(value) is not int or value < 0 for value in tokens):
            raise ValueError("Reported token counts must be nonnegative integers")
        columns = ("run_id", "mission_id", "agent", "runtime", "model", "status", "started_at", "ended_at",
                   "cost_usd", "tokens_in", "tokens_out", "exit_code", "error", "stdout_path", "stderr_path",
                   "result_text", "verdict", "reason", "raw_json")
        values = (
            run["run_id"], run["mission_id"], run["agent"], run["runtime"], run.get("model"), run["status"],
            run.get("started_at"), run.get("ended_at"), float(cost), *tokens,
            run.get("exit_code"), run.get("error"), run.get("stdout_path"), run.get("stderr_path"),
            run.get("result_text"), run.get("verdict"), run.get("reason"),
            json.dumps(run.get("raw") or {}, sort_keys=True, ensure_ascii=False, allow_nan=False),
        )
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            existing = self._conn.execute("SELECT * FROM agent_runs WHERE run_id = ?", (run["run_id"],)).fetchone()
            if existing:
                original = dict(existing)
                original["raw_json"] = json.dumps(json.loads(original["raw_json"] or "{}"), sort_keys=True, ensure_ascii=False, allow_nan=False)
                if tuple(original[column] for column in columns) != values:
                    raise ValueError("Run identity already belongs to a different immutable result")
                return False
            mission = self._conn.execute("SELECT status,contract_json FROM missions WHERE id = ?", (run["mission_id"],)).fetchone()
            if not mission or mission["status"] != "running":
                raise ValueError("A new agent result requires its running mission")
            contract = json.loads(mission["contract_json"] or "{}")
            attempt = self._conn.execute("SELECT * FROM inference_attempts WHERE run_id=?", (run["run_id"],)).fetchone()
            if attempt:
                if (attempt["state"] != "reserved" or attempt["mission_id"] != run["mission_id"]
                        or attempt["agent"] != run["agent"] or attempt["model"] != run.get("model")):
                    raise ValueError("Result does not match its active immutable attempt")
            elif contract.get("runtime", {}).get("inference_executor") and contract.get("scenario", {}).get("kind") == "agent":
                raise ValueError("Canonical inference result has no prior budget reservation")
            self._conn.execute(
                """INSERT INTO agent_runs
                   (run_id, mission_id, agent, runtime, model, status, started_at, ended_at,
                    cost_usd, tokens_in, tokens_out, exit_code, error, stdout_path, stderr_path,
                    result_text, verdict, reason, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                values,
            )
            self._conn.execute("""UPDATE missions SET cost_usd = cost_usd + ?, tokens_in = tokens_in + ?,
                               tokens_out = tokens_out + ? WHERE id = ?""", (float(cost), *tokens, run["mission_id"]))
            if attempt:
                outcome = self._attempt_outcome(run, attempt, contract)
                if float(cost) > attempt["reserved_usd"]:
                    outcome = "overrun"
                held = max(0.0, attempt["reserved_usd"] - float(cost)) if outcome == "unknown" else 0.0
                self._conn.execute("UPDATE inference_attempts SET state=?,held_usd=?,settled_at=? WHERE run_id=?",
                                   (outcome, held, utc_now(), run["run_id"]))
            return True

    def runs(self, mission_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if mission_id:
            rows = self._query(
                "SELECT * FROM agent_runs WHERE mission_id = ? ORDER BY started_at DESC, rowid DESC LIMIT ?",
                (mission_id, limit),
            )
        else:
            rows = self._query("SELECT * FROM agent_runs ORDER BY started_at DESC, rowid DESC LIMIT ?", (limit,))
        return [dict(row) for row in rows]

    # ----------------------------------------------------------------- evidence
    def add_evidence(self, packet: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            self._save_evidence(packet)

    def _save_evidence(self, packet: dict[str, Any]) -> None:
        existing = self._conn.execute("SELECT status, digest FROM evidence WHERE id = ?", (packet["id"],)).fetchone()
        if existing and existing["status"] == "sealed":
            if existing["digest"] != packet["digest"] or packet.get("status") != "sealed":
                raise ValueError("Sealed evidence is immutable")
            return
        self._conn.execute(
            """INSERT OR REPLACE INTO evidence
               (id, mission, scope, digest, checks, review, status, path, created_at, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                packet["id"],
                packet["mission"],
                packet["scope"],
                packet["digest"],
                int(packet.get("checks") or 0),
                packet.get("review") or "NOT RUN",
                packet.get("status") or "draft",
                packet["path"],
                packet["created_at"],
                json.dumps(packet, ensure_ascii=False),
            ),
        )

    def prepare_seal(self, packet: dict[str, Any]) -> None:
        """Persist the exact acceptance intent before publishing any sealed file."""
        mission_id = packet["mission"]
        serialized = json.dumps(packet, sort_keys=True, ensure_ascii=False)
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            mission = self._conn.execute("SELECT status FROM missions WHERE id = ?", (mission_id,)).fetchone()
            if not mission or mission["status"] != "running":
                raise ValueError("Only a running mission can prepare a seal")
            previous = self._conn.execute("SELECT value FROM kv WHERE key = ?", (f"seal:{mission_id}",)).fetchone()
            if previous and previous["value"] != serialized:
                raise ValueError("A different seal is already pending")
            self._conn.execute("INSERT OR IGNORE INTO kv (key, value) VALUES (?, ?)", (f"seal:{mission_id}", serialized))

    def pending_seal(self, mission_id: str) -> dict[str, Any] | None:
        value = self.get_kv(f"seal:{mission_id}")
        return json.loads(value) if value is not None else None

    def record_delivery(self, receipt: dict[str, Any]) -> bool:
        """Advance the release line that produced this receipt.

        The legacy Python slice and the Quarkus/Deno product are separate
        products with separate linear histories. A receipt without a product is
        the legacy slice; a product receipt advances ``product_head:product``.
        """
        key = f"delivery:{receipt['mission']}"
        product = receipt.get("product")
        if product is None:
            # Receipts written before the product field existed carry the
            # worker profile in their isolation metadata.
            profile = (receipt.get("test_isolation") or {}).get("profile")
            product = "product" if profile == "product" else "legacy"
        head_key = "product_head:product" if product == "product" else "product_head"
        serialized = json.dumps(receipt, sort_keys=True, ensure_ascii=False)
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            previous = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
            head = self._conn.execute("SELECT value FROM kv WHERE key = ?", (head_key,)).fetchone()
            if previous:
                if json.loads(previous["value"]) != receipt:
                    raise ValueError("Different delivery already recorded")
                if head is None and not receipt.get("parent_release"):
                    self._conn.execute("INSERT INTO kv (key, value) VALUES (?, ?)", (head_key, serialized))
                return False  # Replaying an old receipt never rolls the product back.
            parent = (receipt.get("parent_release") or {}).get("commit")
            if (json.loads(head["value"])["commit"] if head else None) != parent:
                raise ValueError("STALE_BASE: the local product advanced since task admission")
            self._conn.execute("INSERT INTO kv (key, value) VALUES (?, ?)", (key, serialized))
            self._conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (head_key, serialized))
            return True

    def complete_mission(self, packet: dict[str, Any]) -> bool:
        """Commit completion, the evidence index and the final step together."""
        mission_id = packet["mission"]
        body = packet["payload"]
        final = body["mission"]
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
            if row and row["status"] == "completed":
                existing = self._conn.execute("SELECT digest FROM evidence WHERE mission = ?", (mission_id,)).fetchone()
                if not existing or existing["digest"] != packet["digest"]:
                    raise ValueError("Completed mission has different evidence")
                return False
            pending = self._conn.execute("SELECT value FROM kv WHERE key = ?", (f"seal:{mission_id}",)).fetchone()
            if not row or row["status"] != "running" or not pending or json.loads(pending["value"]) != packet:
                raise ValueError("No matching acceptance intent")
            if (
                not body.get("sealed") or packet["status"] != "sealed" or final["id"] != mission_id
                or final["status"] != "completed" or final["human_approval"] != row["human_approval"]
                or body["diff"]["digest"] != row["diff_digest"]
                or body["gate"] != json.loads(row["gate_json"] or "{}")
                or (final["outcome"] == "assisted" and row["human_approval"] != "operator")
                or (final["outcome"] == "autonomous" and body["gate"].get("decision") != "allow")
                or final["outcome"] not in {"autonomous", "assisted"}
            ):
                raise ValueError("Acceptance facts changed after preparation")
            self._save_evidence(packet)
            self._conn.execute(
                """UPDATE missions SET status = 'completed', outcome = ?, ended_at = ?,
                   progress = 1, current_step = 'Sealed', current_agent = 'gate', blocked_reason = NULL
                   WHERE id = ?""", (final["outcome"], final["ended_at"], mission_id),
            )
            self._conn.execute(
                """UPDATE steps SET status = 'done', progress = 1, detail = ?, ended_at = ?
                   WHERE mission_id = ? AND idx = 6""",
                (f"Packet {packet['digest']} sealed.", final["ended_at"], mission_id),
            )
            self._conn.execute("DELETE FROM kv WHERE key = ?", (f"seal:{mission_id}",))
            return True

    def evidence(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM evidence ORDER BY created_at DESC LIMIT ?", (limit,))
        output = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload"]) if item.get("payload") else {}
            output.append(item)
        return output

    def evidence_for(self, mission_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM evidence WHERE mission = ? ORDER BY created_at DESC LIMIT 1", (mission_id,))
        if not rows:
            return None
        item = dict(rows[0])
        item["payload"] = json.loads(item["payload"]) if item.get("payload") else {}
        return item

    # --------------------------------------------------------------------- kv
    def set_kv(self, key: str, value: str) -> None:
        self._execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))

    def get_kv(self, key: str, default: str | None = None) -> str | None:
        rows = self._query("SELECT value FROM kv WHERE key = ?", (key,))
        return rows[0]["value"] if rows else default

    # ------------------------------------------------------------------ metrics
    def metrics(self) -> dict[str, Any]:
        rows = self._query(
            """SELECT status, outcome, COUNT(*) AS count, COALESCE(SUM(cost_usd), 0) AS cost,
                      COALESCE(SUM(tokens_in), 0) AS tokens_in, COALESCE(SUM(tokens_out), 0) AS tokens_out
                 FROM missions GROUP BY status, outcome"""
        )
        by_status: dict[str, int] = {}
        by_outcome: dict[str, int] = {}
        cost = 0.0
        tokens_in = 0
        tokens_out = 0
        for row in rows:
            by_status[row["status"]] = by_status.get(row["status"], 0) + int(row["count"])
            if row["outcome"]:
                by_outcome[row["outcome"]] = by_outcome.get(row["outcome"], 0) + int(row["count"])
            cost += float(row["cost"] or 0)
            tokens_in += int(row["tokens_in"] or 0)
            tokens_out += int(row["tokens_out"] or 0)

        durations = [
            row["seconds"]
            for row in self._query(
                """SELECT (julianday(ended_at) - julianday(started_at)) * 86400.0 AS seconds
                     FROM missions WHERE ended_at IS NOT NULL AND started_at IS NOT NULL"""
            )
            if row["seconds"] is not None and row["seconds"] >= 0
        ]
        durations.sort()
        p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else None
        average = sum(durations) / len(durations) if durations else None

        evidence_rows = self._query("SELECT COUNT(*) AS count FROM evidence WHERE status = 'sealed'")
        sealed = int(evidence_rows[0]["count"]) if evidence_rows else 0
        completed = by_status.get("completed", 0)
        return {
            "by_status": by_status,
            "by_outcome": by_outcome,
            "cost": round(cost, 6),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "p95_seconds": round(p95, 3) if p95 is not None else None,
            "avg_seconds": round(average, 3) if average is not None else None,
            "sealed": sealed,
            "completed": completed,
            "evidence_coverage": round(sealed / completed, 3) if completed else 0.0,
        }


store = Store()
