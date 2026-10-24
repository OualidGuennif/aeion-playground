"""Persistent local memory for the real POC.

This is a real SQLite database with FTS5 full-text recall. It is deliberately
not a vector store: every item points at a real file and carries its SHA-256.
If the file changes, the item is marked stale on the next read. Recall is
advisory only; it can never grant authority.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import settings

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    file_hash TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    scope TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'verified',
    confidence REAL NOT NULL DEFAULT 1.0,
    authority TEXT NOT NULL DEFAULT 'advisory',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    uses INTEGER NOT NULL DEFAULT 0,
    last_used TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    title,
    body,
    content='memory_items',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory_items BEGIN
    INSERT INTO memory_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body);
END;

CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory_items BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, title, body) VALUES ('delete', old.rowid, old.title, old.body);
END;

CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memory_items BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, title, body) VALUES ('delete', old.rowid, old.title, old.body);
    INSERT INTO memory_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body);
END;

CREATE TABLE IF NOT EXISTS recall_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    query TEXT NOT NULL,
    item_ids TEXT NOT NULL,
    hit INTEGER NOT NULL,
    latency_ms REAL NOT NULL,
    mission_id TEXT
);
"""

DEFAULT_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("README.md", "plan", "product"),
    ("RUNBOOK.md", "runbook", "operations"),
    ("sandbox/parceldesk/README.md", "contract", "sandbox"),
    ("sandbox/parceldesk/AGENTS.md", "contract", "sandbox"),
    ("sandbox/parceldesk/policy/policy.yaml", "policy", "sandbox"),
    ("sandbox/parceldesk/app/pricing.py", "code", "pricing"),
    ("sandbox/parceldesk/tests/test_pricing.py", "test", "pricing"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(settings.workspace_root))
    except ValueError:
        return str(path)


def _clean(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _chunks(text: str, limit: int = 1800) -> Iterable[str]:
    """Split on paragraph boundaries, keeping every chunk under `limit`."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    buffer: list[str] = []
    size = 0
    for paragraph in paragraphs:
        if size and size + len(paragraph) + 2 > limit:
            yield "\n\n".join(buffer)
            buffer = []
            size = 0
        if len(paragraph) > limit:
            for start in range(0, len(paragraph), limit):
                yield paragraph[start : start + limit]
            continue
        buffer.append(paragraph)
        size += len(paragraph) + 2
    if buffer:
        yield "\n\n".join(buffer)


def _title_for(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()[:120]
        if line and not line.startswith(("---", "```", "|")):
            return line[:120]
    return fallback


class MemoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 10000")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(memory_items)")}
        if "file_hash" not in columns:
            self._conn.execute("ALTER TABLE memory_items ADD COLUMN file_hash TEXT NOT NULL DEFAULT ''")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------------------------------------------------------- writes
    def upsert(
        self,
        *,
        title: str,
        body: str,
        source_path: str,
        kind: str,
        scope: str,
        status: str = "verified",
        confidence: float = 1.0,
        authority: str = "advisory",
        item_id: str | None = None,
        file_hash: str | None = None,
    ) -> str:
        with self._lock:
            digest = _hash(body)
            identifier = item_id or _hash(source_path + "\0" + body)[:16]
            now = utc_now()
            row = self._conn.execute("SELECT id, source_hash FROM memory_items WHERE id = ?", (identifier,)).fetchone()
            if row:
                self._conn.execute(
                    """
                    UPDATE memory_items
                       SET title = ?, body = ?, source_path = ?, source_hash = ?, file_hash = ?, kind = ?,
                           scope = ?, status = ?, confidence = ?, authority = ?, updated_at = ?
                     WHERE id = ?
                    """,
                    (
                        title,
                        body,
                        source_path,
                        digest,
                        file_hash or "",
                        kind,
                        scope,
                        status,
                        confidence,
                        authority,
                        now,
                        identifier,
                    ),
                )
            else:
                self._conn.execute(
                    """
                    INSERT INTO memory_items
                        (id, title, body, source_path, source_hash, file_hash, kind, scope, status,
                         confidence, authority, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        title,
                        body,
                        source_path,
                        digest,
                        file_hash or "",
                        kind,
                        scope,
                        status,
                        confidence,
                        authority,
                        now,
                        now,
                    ),
                )
            self._conn.commit()
            return identifier

    def seed_sources(self, sources: Iterable[tuple[str, str, str]] | None = None) -> int:
        """Index real files. Missing files are skipped, never invented."""
        count = 0
        for rel_path, kind, scope in sources or DEFAULT_SOURCES:
            path = (settings.workspace_root / rel_path).resolve()
            if not path.is_file():
                continue
            text = _clean(path.read_text(errors="replace"))
            if not text:
                continue
            whole_hash = _hash(text)
            for index, chunk in enumerate(_chunks(text)):
                self.upsert(
                    title=_title_for(chunk, path.name),
                    body=chunk,
                    source_path=rel_path,
                    kind=kind,
                    scope=scope,
                    item_id=_hash(f"{rel_path}:{index}")[:16],
                    confidence=1.0,
                    authority="advisory",
                    file_hash=whole_hash,
                )
                count += 1
        return count

    def reset(self) -> None:
        """Drop indexed items and recall history before a controlled reseed.

        This is used once when the source list changes (for example when the
        simulation-era files are removed). It never touches the mission ledger.
        """
        with self._lock:
            self._conn.execute("DELETE FROM memory_items")
            self._conn.execute("DELETE FROM recall_log")
            self._conn.commit()

    def write_note(self, *, mission_id: str, title: str, body: str, source_path: str) -> str:
        return self.upsert(
            title=title,
            body=body,
            source_path=source_path,
            kind="agent-note",
            scope=mission_id,
            status="verified",
            confidence=0.75,
            authority="advisory",
        )

    # ----------------------------------------------------------------- reads
    def _refresh_staleness(self) -> None:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, source_path, file_hash, status FROM memory_items WHERE kind != 'agent-note'"
            ).fetchall()
            cache: dict[str, str | None] = {}
            changed = False
            for row in rows:
                rel_path = row["source_path"]
                if rel_path not in cache:
                    path = (settings.workspace_root / rel_path).resolve()
                    cache[rel_path] = (
                        _hash(_clean(path.read_text(errors="replace"))) if path.is_file() else None
                    )
                current = cache[rel_path]
                status = "verified" if current is not None and current == row["file_hash"] else "stale"
                if row["status"] != status:
                    self._conn.execute("UPDATE memory_items SET status = ? WHERE id = ?", (status, row["id"]))
                    changed = True
            # Always close the write transaction: leaving it open holds a RESERVED
            # lock and blocks the mission ledger on the same SQLite file.
            if changed or rows:
                self._conn.commit()

    def items(self, limit: int = 12) -> list[dict[str, Any]]:
        with self._lock:
            self._refresh_staleness()
            rows = self._conn.execute(
                """
                SELECT * FROM memory_items
                 ORDER BY CASE status WHEN 'verified' THEN 0 ELSE 1 END, uses DESC, updated_at DESC
                 LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [self._shape(row) for row in rows]

    def _shape(self, row: sqlite3.Row) -> dict[str, Any]:
        statement = re.sub(r"\s+", " ", row["body"]).strip()
        if len(statement) > 260:
            statement = statement[:257].rstrip() + "…"
        return {
            "id": row["id"],
            "kind": row["kind"],
            "scope": row["scope"],
            "state": row["status"],
            "statement": statement,
            "source": row["source_path"],
            "uses": row["uses"],
            "last_used": row["last_used"],
            "confidence": row["confidence"],
            "freshness": "current" if row["status"] == "verified" else "stale",
            "authority": "advisory" if row["authority"] == "advisory" else row["authority"],
            "title": row["title"],
        }

    def recall(
        self,
        query: str,
        *,
        mission_id: str | None = None,
        limit: int = 5,
        source_prefix: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._refresh_staleness()
            started = time.perf_counter()
            terms = [term for term in re.findall(r"[A-Za-z0-9_./-]{2,}", query) if term.lower() not in STOPWORDS]
            rows: list[sqlite3.Row] = []
            source_clause = " AND m.source_path LIKE ?" if source_prefix else ""
            source_args: tuple[str, ...] = (source_prefix + "%",) if source_prefix else ()
            if terms:
                expression = " OR ".join('"' + term.replace('"', "") + '"' for term in terms[:8])
                try:
                    rows = self._conn.execute(
                        f"""
                        SELECT m.*, bm25(memory_fts) AS rank
                          FROM memory_fts
                          JOIN memory_items m ON m.rowid = memory_fts.rowid
                         WHERE memory_fts MATCH ? AND m.status = 'verified'{source_clause}
                         ORDER BY rank
                         LIMIT ?
                        """,
                        (expression, *source_args, limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                if not rows:
                    like = "%" + terms[0] + "%"
                    fallback_clause = " AND source_path LIKE ?" if source_prefix else ""
                    rows = self._conn.execute(
                        f"SELECT *, 0.0 AS rank FROM memory_items "
                        f"WHERE status = 'verified' AND (title LIKE ? OR body LIKE ?){fallback_clause} LIMIT ?",
                        (like, like, *source_args, limit),
                    ).fetchall()
            latency = (time.perf_counter() - started) * 1000
            identifiers = [row["id"] for row in rows]
            now = utc_now()
            for identifier in identifiers:
                self._conn.execute(
                    "UPDATE memory_items SET uses = uses + 1, last_used = ? WHERE id = ?", (now, identifier)
                )
            self._conn.execute(
                "INSERT INTO recall_log (ts, query, item_ids, hit, latency_ms, mission_id) VALUES (?, ?, ?, ?, ?, ?)",
                (now, query, json.dumps(identifiers), 1 if identifiers else 0, latency, mission_id),
            )
            self._conn.commit()
            return {
                "query": query,
                "items": identifiers,
                "rows": [self._shape(row) for row in rows],
                "latency_ms": round(latency, 1),
                "hit": bool(identifiers),
                "ts": now,
            }

    def recent_recalls(self, limit: int = 5) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM recall_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            output = []
            for row in rows:
                ids = json.loads(row["item_ids"])
                output.append(
                    {
                        "query": row["query"],
                        "items": ids,
                        "hit": bool(row["hit"]),
                        "latency_ms": row["latency_ms"],
                        "ts": row["ts"],
                        "mission": row["mission_id"] or "manual",
                    }
                )
            return output

    def stats(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_staleness()
            total = self._conn.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0]
            verified = self._conn.execute(
                "SELECT COUNT(*) FROM memory_items WHERE status = 'verified'"
            ).fetchone()[0]
            stale = self._conn.execute("SELECT COUNT(*) FROM memory_items WHERE status = 'stale'").fetchone()[0]
            used = self._conn.execute("SELECT COUNT(*) FROM memory_items WHERE uses > 0").fetchone()[0]
            uses = self._conn.execute("SELECT COALESCE(SUM(uses), 0) FROM memory_items").fetchone()[0]
            queries = self._conn.execute("SELECT COUNT(*) FROM recall_log").fetchone()[0]
            hits = self._conn.execute("SELECT COUNT(*) FROM recall_log WHERE hit = 1").fetchone()[0]
            return {
                "total": total,
                "verified": verified,
                "stale": stale,
                "used_items": used,
                "uses": uses,
                "recall_queries": queries,
                "hit_rate": round(hits / queries, 3) if queries else 0.0,
            }


STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "what",
    "when",
    "where",
    "which",
    "into",
    "over",
    "under",
    "about",
    "does",
    "how",
    "why",
    "les",
    "des",
    "une",
    "dans",
    "pour",
    "avec",
    "sur",
    "est",
    "que",
    "qui",
}


memory_store = MemoryStore()
