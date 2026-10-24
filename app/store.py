"""Persistent mission ledger.

SQLite is the canonical local ledger. Every mission, step, agent run, gate
decision, event and evidence packet is stored here. The JSONL audit trail and
the OTel-shaped event file are projections of the same facts.
"""

from __future__ import annotations

import fcntl
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import settings

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

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
            self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS missions_request_id ON missions(request_id)")
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
        self._execute(
            """INSERT INTO events (ts, type, actor, mission, fixture, message, severity, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                timestamp,
                type,
                actor,
                mission,
                fixture,
                message,
                severity,
                json.dumps(payload, ensure_ascii=False) if payload else None,
            ),
        )
        return {
            "ts": timestamp,
            "type": type,
            "actor": actor,
            "mission": mission,
            "fixture": fixture,
            "message": message,
            "severity": severity,
            "payload": payload or {},
        }

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
    def add_run(self, run: dict[str, Any]) -> None:
        self._execute(
            """INSERT OR REPLACE INTO agent_runs
               (run_id, mission_id, agent, runtime, model, status, started_at, ended_at,
                cost_usd, tokens_in, tokens_out, exit_code, error, stdout_path, stderr_path,
                result_text, verdict, reason, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run["run_id"],
                run["mission_id"],
                run["agent"],
                run["runtime"],
                run.get("model"),
                run["status"],
                run.get("started_at"),
                run.get("ended_at"),
                float(run.get("cost_usd") or 0),
                int(run.get("tokens_in") or 0),
                int(run.get("tokens_out") or 0),
                run.get("exit_code"),
                run.get("error"),
                run.get("stdout_path"),
                run.get("stderr_path"),
                run.get("result_text"),
                run.get("verdict"),
                run.get("reason"),
                json.dumps(run.get("raw") or {}, ensure_ascii=False),
            ),
        )

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
        """Advance the local product only from its expected accepted parent."""
        key = f"delivery:{receipt['mission']}"
        serialized = json.dumps(receipt, sort_keys=True, ensure_ascii=False)
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            previous = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
            head = self._conn.execute("SELECT value FROM kv WHERE key = 'product_head'").fetchone()
            if previous:
                if json.loads(previous["value"]) != receipt:
                    raise ValueError("Different delivery already recorded")
                if head is None and not receipt.get("parent_release"):
                    self._conn.execute("INSERT INTO kv (key, value) VALUES ('product_head', ?)", (serialized,))
                return False  # Replaying an old receipt never rolls the product back.
            parent = (receipt.get("parent_release") or {}).get("commit")
            if (json.loads(head["value"])["commit"] if head else None) != parent:
                raise ValueError("STALE_BASE: the local product advanced since task admission")
            self._conn.execute("INSERT INTO kv (key, value) VALUES (?, ?)", (key, serialized))
            self._conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES ('product_head', ?)", (serialized,))
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
