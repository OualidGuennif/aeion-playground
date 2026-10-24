"""Delivery faults use controlled HTTP responses, never an external collector/provider."""
import fcntl
import json
import os
from pathlib import Path
import stat

import httpx
import pytest

from app import event_export, observability
from app.event_export import EventExporter, append_audit, retry_after
from app.store import Store

CLIENT_TYPE = httpx.Client

@pytest.fixture
def delivery(tmp_path):
    ledger = Store(tmp_path / 'events.db')
    record = ledger.add_event(type='engine.started', actor='engine', message='private-canary',
                              payload={'provenance': 'controlled-qualification'})
    exporter = EventExporter(ledger, tmp_path / 'audit/events.jsonl')
    yield ledger, exporter, record
    exporter.stop()
    ledger._conn.close()


def transport(monkeypatch, handler):
    monkeypatch.setattr(event_export.httpx, 'Client', lambda **kwargs: CLIENT_TYPE(transport=httpx.MockTransport(handler), **kwargs))


def test_transient_failure_restarts_from_acknowledged_audit_and_honors_retry_after(delivery, monkeypatch):
    ledger, exporter, record = delivery
    requests = []
    def unavailable(request):
        requests.append(request)
        return httpx.Response(503, headers={'Retry-After': '40'})
    monkeypatch.setattr(event_export.time, 'time', lambda: 100)
    transport(monkeypatch, unavailable)
    assert exporter.export_one(now=100)
    assert exporter.failure == 'collector_unavailable'
    assert ledger.outbox_status()['audit_pending'] == 0
    assert ledger.outbox_status()['collector_pending'] == 1
    assert not exporter.export_one(now=139)
    assert len(requests) == 1
    # A new exporter represents a controller restart; its durable audit ack survives.
    restarted = EventExporter(ledger, exporter.audit_path)
    received = []
    def recovered(request):
        received.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={})
    transport(monkeypatch, recovered)
    assert restarted.export_one(now=141)
    assert ledger.outbox_status() == {'total': 1, 'audit_pending': 0, 'collector_pending': 0, 'failed': 0}
    audit = [json.loads(line) for line in exporter.audit_path.read_text().splitlines()]
    assert len(audit) == 1 and audit[0]['event_id'] == record['event_id']
    assert [path for path, _ in received] == ['/v1/logs', '/v1/traces']
    assert 'private-canary' not in json.dumps(received)


@pytest.mark.parametrize('response', [
    (400, {}), (401, {}), (403, {}), (404, {}), (500, {}), (302, {}),
    (200, {'partialSuccess': {'rejectedLogRecords': '1'}}),
    (200, ['malformed']), (200, {'partialSuccess': 'malformed'}),
])
def test_permanent_refusal_requires_explicit_operator_requeue(delivery, monkeypatch, response):
    ledger, exporter, record = delivery
    calls = []
    def refuse(request):
        calls.append(request)
        return httpx.Response(response[0], json=response[1])
    transport(monkeypatch, refuse)
    assert exporter.export_one(now=100)
    assert exporter.failure == 'collector_rejected'
    assert not exporter.export_one(now=10_000)
    assert len(calls) == 1
    assert ledger.outbox_status()['failed'] == 1
    assert ledger.requeue_rejected_event(record['id'])
    transport(monkeypatch, lambda request: httpx.Response(200, json={}))
    assert exporter.export_one(now=10_000)
    assert ledger.outbox_status()['collector_pending'] == 0


@pytest.mark.parametrize('status', [429, 502, 503, 504])
def test_only_otlp_retryable_http_statuses_retry(delivery, monkeypatch, status):
    ledger, exporter, _ = delivery
    transport(monkeypatch, lambda request: httpx.Response(status))
    assert exporter.export_one(now=100)
    assert exporter.failure == 'collector_unavailable'
    assert not exporter.export_one(now=100)
    assert ledger.claim_outbox(now=200)


def test_network_failure_is_pending_without_disclosing_response_or_context(delivery, monkeypatch):
    ledger, exporter, _ = delivery
    def failed(request):
        raise httpx.ConnectError('private-error-canary', request=request)
    transport(monkeypatch, failed)
    assert exporter.export_one(now=100)
    row = ledger._query('SELECT * FROM event_outbox')[0]
    assert row['last_error'] == 'collector_unavailable'
    assert 'private-error-canary' not in json.dumps(dict(row))


def test_second_signal_failure_replays_stable_ids_with_explicit_duplicates(delivery, monkeypatch):
    ledger, exporter, record = delivery
    logs = []
    failing = True
    def receive(request):
        if request.url.path == '/v1/logs':
            logs.append(json.loads(request.content))
        return httpx.Response(503 if failing and request.url.path == '/v1/traces' else 200, json={})
    transport(monkeypatch, receive)
    assert exporter.export_one(now=100)
    failing = False
    assert exporter.export_one(now=200)
    assert logs[0] == logs[1]  # Consumers deduplicate by aeion.event.id; no exactly-once claim.
    assert ledger.outbox_status()['collector_pending'] == 0
    assert exporter.audit_path.read_text().count(record['event_id']) == 1


def test_audit_lock_cannot_block_exporter_forever_and_no_ack_is_lost(delivery, monkeypatch):
    ledger, exporter, _ = delivery
    exporter.audit_path.parent.mkdir()
    lockpath = exporter.audit_path.with_suffix('.jsonl.lock')
    with lockpath.open('w') as locked:
        fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert exporter.export_one(now=100)
        assert exporter.failure == 'audit_unavailable'
        assert ledger.outbox_status()['audit_pending'] == 1
    transport(monkeypatch, lambda request: httpx.Response(200, json={}))
    assert exporter.export_one(now=200)
    assert ledger.outbox_status()['audit_pending'] == 0


def test_audit_rotates_privately_and_preserves_torn_frame(tmp_path, monkeypatch):
    path = tmp_path / 'audit/events.jsonl'
    path.parent.mkdir()
    path.write_bytes(b'{torn')
    append_audit(path, {'id': 1}, 'instance:1')
    lines = path.read_text().splitlines()
    assert lines[0] == '{torn' and json.loads(lines[1])['event_id'] == 'instance:1'
    monkeypatch.setattr(event_export, 'MAX_AUDIT_BYTES', 1)
    for number in range(2, 5):
        append_audit(path, {'id': number}, f'instance:{number}')
    assert json.loads(path.read_text())['event_id'] == 'instance:4'
    assert json.loads(path.with_suffix('.jsonl.1').read_text())['event_id'] == 'instance:3'
    assert json.loads(path.with_suffix('.jsonl.2').read_text())['event_id'] == 'instance:2'
    assert all(stat.S_IMODE(item.stat().st_mode) == 0o600 for item in path.parent.iterdir())


def test_audit_symlink_does_not_modify_outside_file(tmp_path):
    outside = tmp_path / 'outside'
    outside.write_text('preserve')
    target = tmp_path / 'events.jsonl'
    target.symlink_to(outside)
    with pytest.raises(OSError, match='symlink'):
        append_audit(target, {}, 'instance:1')
    assert outside.read_text() == 'preserve'


def test_retry_after_is_bounded_numeric_or_http_date(monkeypatch):
    monkeypatch.setattr(event_export.time, 'time', lambda: 100)
    assert retry_after('40') == 140
    assert retry_after('Thu, 01 Jan 1970 00:03:20 GMT') == 200
    assert retry_after('unknown') is None


@pytest.mark.parametrize('endpoint', ['https://127.0.0.1:4318', 'http://remote.example:4318',
                                      'http://127.0.0.1:4318/path', 'http://user@127.0.0.1:4318'])
def test_telemetry_refuses_nonlocal_or_credentialed_export(delivery, endpoint):
    ledger, exporter, _ = delivery
    with pytest.raises(ValueError, match='loopback'):
        EventExporter(ledger, exporter.audit_path, endpoint)


def test_health_does_not_treat_a_projection_file_as_a_running_collector(tmp_path, monkeypatch):
    monkeypatch.setattr(observability, '_collector_cache', None)
    transport(monkeypatch, lambda request: httpx.Response(503))
    assert observability.collector_health()['available'] is False
