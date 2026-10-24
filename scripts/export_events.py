#!/usr/bin/env python3
"""Drain a bounded outbox batch; never print full events, context or collector responses."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings
from app.event_export import EventExporter
from app.store import store

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--limit', type=int, default=100, help='Maximum claimed records, 1..1000')
parser.add_argument('--requeue', type=int, help='Explicitly retry one permanent rejection after correcting its cause')
args = parser.parse_args()
if not 1 <= args.limit <= 1000:
    parser.error('--limit must be 1..1000')
if args.requeue is not None and not store.requeue_rejected_event(args.requeue):
    parser.error('No permanently rejected event with that local ID')
exporter = EventExporter(store, settings.audit_path)
processed = 0
while processed < args.limit and exporter.export_one():
    processed += 1
status = store.outbox_status()
print(json.dumps({'claimed': processed, **status, 'last_error': exporter.failure}))
raise SystemExit(1 if status['audit_pending'] or status['collector_pending'] else 0)
