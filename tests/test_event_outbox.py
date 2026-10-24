from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from app.event_protocol import otlp
from app.store import Store


def event(ledger, **override):
    return ledger.add_event(type="agent.completed", actor="forge", message="private controlled canary; never export",
        mission="PD-001", payload={"model": "opencode-go/deepseek-v4.1-flash", "run_id": "run-" + "a" * 32,
            "source": "private controlled canary; never export", "cost_usd": .01, "tokens_in": 23}, **override)


def test_event_and_outbox_are_atomic_and_namespace_survives_restart(tmp_path):
    path = tmp_path / "events.db"
    ledger = Store(path)
    try:
        ledger._execute("""CREATE TRIGGER reject_outbox BEFORE INSERT ON event_outbox
            BEGIN SELECT RAISE(ABORT,'controlled outbox failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="outbox failure"):
            event(ledger)
        assert ledger.count_events() == 0 and ledger.outbox_status()["total"] == 0
        ledger._execute("DROP TRIGGER reject_outbox")
        record = event(ledger)
        namespace = ledger.instance_id
        assert record["event_id"] == f"{namespace}:{record['id']}"
    finally:
        ledger._conn.close()
    reopened = Store(path)
    try:
        assert reopened.instance_id == namespace
        claimed = reopened.claim_outbox()
        assert claimed["metadata"]["event_id"] == record["event_id"]
        assert reopened.outbox_status()["total"] == 1
    finally:
        reopened._conn.close()


def test_metadata_uses_allowlist_and_stable_real_otlp_linkage(tmp_path):
    ledger = Store(tmp_path / "events.db")
    try:
        record = event(ledger)
        claimed = ledger.claim_outbox()
        wire = json.dumps(claimed["metadata"])
        assert "private controlled canary" not in wire and "source" not in wire
        logs, traces = otlp(claimed["metadata"])
        log = logs["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        span = traces["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        assert log["traceId"] == span["traceId"] and len(log["traceId"]) == 32
        assert log["spanId"] == span["spanId"] and len(log["spanId"]) == 16
        assert span["startTimeUnixNano"] == span["endTimeUnixNano"]
        assert log["body"] == {"stringValue": "agent.completed"}
        assert {item["key"]: item["value"] for item in log["attributes"]}["aeion.event.id"] == {"stringValue": record["event_id"]}
        assert ledger.acknowledge_outbox(record["id"], claimed["owner"], "audit")
        assert ledger.release_outbox(record["id"], claimed["owner"], "collector_unavailable", now=0)
        retry = ledger.claim_outbox(now=2)
        assert retry["audit_sent"] == 1 and retry["collector_sent"] == 0
        assert otlp(retry["metadata"]) == (logs, traces)
    finally:
        ledger._conn.close()


def test_competing_exporters_have_fenced_lease_and_explicit_replay_after_crash(tmp_path):
    path = tmp_path / "events.db"
    first, second = Store(path), Store(path)
    try:
        record = event(first)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda ledger: ledger.claim_outbox(now=0), (first, second)))
        assert sum(result is not None for result in results) == 1
        original = next(result for result in results if result)
        assert not first.claim_outbox(now=29)
        replay = second.claim_outbox(now=31)
        assert replay["metadata"] == original["metadata"] and replay["owner"] != original["owner"]
        assert not first.acknowledge_outbox(record["id"], original["owner"], "audit")
        assert second.acknowledge_outbox(record["id"], replay["owner"], "audit")
        assert second.acknowledge_outbox(record["id"], replay["owner"], "collector")
        second.release_outbox(record["id"], replay["owner"], now=31)
        assert not first.claim_outbox(now=100)
        assert first.outbox_status() == {"total": 1, "audit_pending": 0, "collector_pending": 0, "failed": 0}
    finally:
        first._conn.close()
        second._conn.close()


def test_full_ledger_and_sqlite_sidecars_are_private(tmp_path):
    import stat
    path = tmp_path / 'events.db'
    ledger = Store(path)
    try:
        event(ledger)
        for target in tmp_path.iterdir():
            assert stat.S_IMODE(target.stat().st_mode) == 0o600
    finally:
        ledger._conn.close()


@pytest.mark.parametrize('suffix', ['', '-wal', '-shm'])
def test_linked_ledger_or_sidecar_is_refused_without_modifying_target(tmp_path, suffix):
    path = tmp_path / 'events.db'
    outside = tmp_path / 'outside'
    outside.write_text('preserve')
    target = tmp_path / ('events.db' + suffix)
    target.symlink_to(outside)
    with pytest.raises(OSError):
        Store(path)
    assert outside.read_text() == 'preserve'
