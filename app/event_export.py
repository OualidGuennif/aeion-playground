"""Bounded at-least-once export from the canonical SQLite event outbox."""
import fcntl
import json
import math
import os
from pathlib import Path
import stat
import sqlite3
import threading
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import httpx

from .event_protocol import otlp

MAX_AUDIT_BYTES = 5 * 1024 * 1024
MAX_EVENT_BYTES = 2 * 1024 * 1024


class DeliveryError(OSError):
    def __init__(self, code, retry_at=None):
        super().__init__(code)
        self.code = code
        self.retry_at = retry_at


def retry_after(value):
    if not value:
        return None
    try:
        if value.isdigit():
            result = time.time() + int(value)
        else:
            result = parsedate_to_datetime(value).timestamp()
        return float(result) if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _private_fd(path, flags):
    descriptor = os.open(path, flags | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise OSError("Audit target must be an operator-owned regular file")
        os.fchmod(descriptor, 0o600)
    except OSError:
        os.close(descriptor)
        raise
    return descriptor


def append_audit(path, event, identity):
    data = (json.dumps({**event, "event_id": identity}, ensure_ascii=False) + "\n").encode()
    if len(data) > MAX_EVENT_BYTES:
        raise OSError("Audit event exceeds projection bound; canonical record preserved")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(_private_fd(path.with_suffix(path.suffix + ".lock"), os.O_WRONLY), "wb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.is_symlink():
            raise OSError("Audit projection cannot be a symlink")
        if path.exists() and path.stat().st_size + len(data) > MAX_AUDIT_BYTES:
            oldest = path.with_suffix(path.suffix + ".2")
            previous = path.with_suffix(path.suffix + ".1")
            if oldest.is_symlink() or previous.is_symlink():
                raise OSError("Audit archives cannot be symlinks")
            oldest.unlink(missing_ok=True)
            if previous.exists():
                previous.rename(oldest)
            path.rename(previous)
        with os.fdopen(_private_fd(path, os.O_RDWR | os.O_APPEND), "r+b") as stream:
            # Preserve a torn crash/disk-full line as invalid data, then start a
            # new complete frame. Readers deduplicate by canonical event_id.
            if stream.seek(0, os.SEEK_END):
                stream.seek(-1, os.SEEK_END)
                if stream.read(1) != b"\n":
                    os.write(stream.fileno(), b"\n")
            if os.write(stream.fileno(), data) != len(data):
                raise OSError("Incomplete audit append; event remains pending")
            os.fsync(stream.fileno())


class EventExporter:
    def __init__(self, ledger, audit_path, endpoint="http://127.0.0.1:4318"):
        self.ledger, self.audit_path = ledger, Path(audit_path)
        endpoint = endpoint.rstrip("/")
        url = urlparse(endpoint)
        allowed = {host.strip() for host in os.environ.get("AEION_OTLP_ALLOWED_HOSTS", "").split(",") if host.strip()}
        if url.scheme != "http" or url.hostname not in ({"127.0.0.1"} | allowed) or not url.port or url.path or url.username or url.password or url.query or url.fragment:
            raise ValueError("Telemetry export requires a loopback or explicitly allowed collector origin")
        self.endpoint = endpoint
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.thread = None
        self.failure = None

    def send_collector(self, metadata):
        try:
            logs, spans = otlp(metadata)
            with httpx.Client(base_url=self.endpoint, timeout=2, trust_env=False, follow_redirects=False) as client:
                for path, payload, rejected in (("/v1/logs", logs, "rejectedLogRecords"), ("/v1/traces", spans, "rejectedSpans")):
                    with client.stream("POST", path, json=payload) as response:
                        if response.status_code != 200:
                            retryable = response.status_code in {429, 502, 503, 504}
                            raise DeliveryError("collector_unavailable" if retryable else "collector_rejected",
                                                retry_after(response.headers.get("Retry-After")) if retryable else None)
                        raw = bytearray()
                        for chunk in response.iter_bytes(chunk_size=4096):
                            raw.extend(chunk)
                            if len(raw) > 16384:
                                raise DeliveryError("collector_rejected")
                        result = json.loads(raw) if raw else {}
                        partial = result.get("partialSuccess") or {}
                        if int(partial.get(rejected) or 0) != 0:
                            # OTLP partial success is terminal, never auto-retry.
                            raise DeliveryError("collector_rejected")
        except httpx.HTTPError:
            raise DeliveryError("collector_unavailable") from None
        except (ValueError, TypeError, AttributeError, KeyError):
            raise DeliveryError("collector_rejected") from None

    def export_one(self, *, now=None):
        job = self.ledger.claim_outbox(now=now)
        if not job:
            return False
        error = None
        next_retry = None
        try:
            if not job["audit_sent"]:
                try:
                    append_audit(self.audit_path, job["event"], job["metadata"]["event_id"])
                except OSError:
                    raise DeliveryError("audit_unavailable") from None
                if not self.ledger.acknowledge_outbox(job["event_id"], job["owner"], "audit"):
                    return True  # A newer owner fences this sender; replay is safe.
            if not job["collector_sent"]:
                self.send_collector(job["metadata"])
                self.ledger.acknowledge_outbox(job["event_id"], job["owner"], "collector")
        except DeliveryError as failure:
            error = failure.code
            next_retry = failure.retry_at
        except sqlite3.Error:
            error = "persistence_unavailable"
            raise
        finally:
            self.ledger.release_outbox(job["event_id"], job["owner"], error, now=now, retry_at=next_retry)
        self.failure = error
        return True

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stopped.clear()
        self.thread = threading.Thread(target=self._run, name="aeion-event-exporter", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=6)

    def _run(self):
        while not self.stopped.is_set():
            try:
                worked = self.export_one()
            except (OSError, ValueError, sqlite3.Error):
                self.failure = "persistence_unavailable"
                worked = False
            if not worked:
                self.wake.wait(1)
                self.wake.clear()
