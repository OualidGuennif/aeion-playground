"""Opt-in real Docker/OTLP/restart qualification with explicitly controlled events."""
import json
import os
from pathlib import Path
import subprocess
import time

import httpx
import pytest

from app.event_export import EventExporter
from app.store import Store

pytestmark = pytest.mark.skipif(os.environ.get('AEION_OTEL_TESTS') != '1', reason='Requires the dedicated Compose collector')
ROOT = Path(__file__).parents[1]


def docker(*args):
    result = subprocess.run(['docker', *args], cwd=ROOT, capture_output=True, timeout=45)
    assert result.returncode == 0, 'Collector qualification command failed'
    return result.stdout


def ready():
    deadline = time.monotonic() + 15
    with httpx.Client(timeout=1, trust_env=False, follow_redirects=False) as client:
        while time.monotonic() < deadline:
            try:
                if client.get('http://127.0.0.1:13133/').status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(.1)
    pytest.fail('Collector health endpoint unavailable')


def records(signal, identity):
    matches = []
    for path in (ROOT / 'runtime/otel/collector').glob(f'{signal}*.json*'):
        for line in path.read_text().splitlines():
            if identity in line:
                matches.append(json.loads(line))
    return matches


def test_actual_collector_receives_private_metadata_replays_and_survives_restart(tmp_path):
    ready()
    identity = docker('compose', 'ps', '-q', 'otel-collector').decode().strip()
    host = json.loads(docker('inspect', identity, '--format', '{{json .HostConfig}}'))
    assert host['ReadonlyRootfs'] and host['Privileged'] is False
    assert 'ALL' in host['CapDrop'] and 'no-new-privileges:true' in host['SecurityOpt']
    assert host['Memory'] and host['PidsLimit'] and host['NanoCpus']
    assert host['PortBindings'] == {
        '4318/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '4318'}],
        '13133/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '13133'}]}
    path = tmp_path / 'events.db'
    ledger = Store(path)
    exporter = EventExporter(ledger, tmp_path / 'events.jsonl')
    try:
        first = ledger.add_event(type='engine.started', actor='engine', message='CONTROLLED_PRIVATE_CANARY_DO_NOT_EXPORT',
                                 payload={'provenance': 'controlled-qualification', 'source': 'CONTROLLED_PRIVATE_CANARY_DO_NOT_EXPORT'})
        assert exporter.export_one()
        assert ledger.outbox_status()['collector_pending'] == 0
        for signal in ('logs', 'traces'):
            assert len(records(signal, first['event_id'])) == 1
            assert 'CONTROLLED_PRIVATE_CANARY_DO_NOT_EXPORT' not in json.dumps(records(signal, first['event_id']))
        # Simulate loss of local acknowledgement after the receiver accepted both signals.
        # Actual HTTP replay must preserve IDs, with explicit duplicates at the receiver.
        metadata = json.loads(ledger._query('SELECT metadata_json FROM event_outbox')[0][0])
        exporter.send_collector(metadata)
        assert len(records('logs', first['event_id'])) == 2
        assert len(records('traces', first['event_id'])) == 2
        docker('compose', 'stop', '-t', '5', 'otel-collector')
        second = ledger.add_event(type='engine.started', actor='engine', message='controlled outage',
                                  payload={'provenance': 'controlled-qualification'})
        assert exporter.export_one()
        assert exporter.failure == 'collector_unavailable'
        assert ledger.outbox_status()['audit_pending'] == 0 and ledger.outbox_status()['collector_pending'] == 1
        ledger._conn.close()
        ledger = Store(path)
        exporter = EventExporter(ledger, exporter.audit_path)
        docker('compose', 'start', 'otel-collector')
        ready()
        # Rotation preserves previously accepted records across collector restart.
        assert len(records('logs', first['event_id'])) == 2
        assert len(records('traces', first['event_id'])) == 2
        assert exporter.export_one(now=time.time() + 120)
        assert ledger.outbox_status()['collector_pending'] == 0
        assert len(records('logs', second['event_id'])) == 1
        assert len(records('traces', second['event_id'])) == 1
        assert len(exporter.audit_path.read_text().splitlines()) == 2
    finally:
        ledger._conn.close()
        # Never leave the real local collector stopped, including on assertion failure.
        docker('compose', 'start', 'otel-collector')
