"""Versioned metadata-only telemetry; free-form event content never leaves the ledger."""
from datetime import datetime
import hashlib
import math
import re
import time

from .operator_settings import KNOWN_MODELS

TYPES = frozenset("""agent.blocked agent.completed agent.no_patch context.recalled croa.audit.checked
delivery.blocked delivery.local delivery.verified diff.captured drill.patch.applied engine.started
evidence.seal_pending evidence.sealed gate.approved gate.decided local.patch.applied
mission.awaiting_human mission.blocked mission.created mission.projection_failed mission.started
model.feedback model.patch.applied model.route model.route_blocked ops.integrity.checked
paperclip.issue.created paperclip.issue.unavailable paperclip.update.failed paperclip.url.corrected queue.error
review.blocked review.completed review.retry_queued sandbox.checked sandbox.created tests.completed""".split())
ACTORS = frozenset("atlas scout rook forge sentinel probe gate engine delivery operator paperclip human croa ops system".split())
SEVERITY = {"debug": 5, "info": 9, "success": 9, "warning": 13, "error": 17, "critical": 21}


def metadata(instance, event):
    identity = f"{instance}:{event['id']}"
    event_type = event["type"] if event["type"] in TYPES else "unrecognized"
    actor = event["actor"] if event["actor"] in ACTORS else "other"
    mission = event.get("mission") or ""
    mission = mission if isinstance(mission, str) and re.fullmatch(r"PD-[0-9]{3,12}", mission) else ""
    fixture = event.get("fixture") or ""
    fixture = fixture if isinstance(fixture, str) and re.fullmatch(r"PD[0-9]{2,4}", fixture) else ""
    try:
        timestamp = datetime.fromisoformat(event["ts"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError()
        nano = int(timestamp.timestamp() * 1_000_000_000)
        if not 0 < nano < 2**63:
            raise ValueError()
    except (ValueError, TypeError, AttributeError, OverflowError):
        nano = time.time_ns()
    attributes = {"aeion.event.id": identity, "aeion.event.type": event_type, "aeion.actor": actor}
    if mission:
        attributes["aeion.mission.id"] = mission
    if fixture:
        attributes["aeion.fixture"] = fixture
    payload = event.get("payload") or {}
    if isinstance(payload, dict):
        if payload.get("provenance") in ("controlled-qualification", "codex", "operator", "factory"):
            attributes["aeion.provenance"] = payload["provenance"]
        for name in ("run_id", "session_id"):
            value = payload.get(name)
            pattern = r"run-[0-9a-f]{12,32}" if name == "run_id" else r"ses_[A-Za-z0-9]{8,64}"
            if isinstance(value, str) and re.fullmatch(pattern, value):
                attributes["aeion." + name] = value
        if isinstance(payload.get("model"), str) and payload["model"] in KNOWN_MODELS:
            attributes["aeion.model"] = payload["model"]
        for name in ("cost_usd", "tokens_in", "tokens_out", "duration_ms", "total", "passed", "failed"):
            value = payload.get(name)
            if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1_000_000_000:
                attributes["aeion." + name] = value
        for name in ("digest", "diff_digest", "packet_digest", "source_digest"):
            value = payload.get(name)
            if isinstance(value, str) and re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", value):
                attributes["aeion." + name] = value
    return {"schema": "aeion.event-metadata/v1", "instance_id": instance, "event_id": identity,
            "time_unix_nano": str(nano), "observed_unix_nano": str(time.time_ns()),
            "trace_id": hashlib.sha256((instance + ":" + (mission or "engine")).encode()).hexdigest()[:32],
            "span_id": hashlib.sha256(identity.encode()).hexdigest()[:16],
            "severity": event["severity"] if event["severity"] in SEVERITY else "info",
            "type": event_type, "attributes": attributes}


def _attributes(values):
    def value(item):
        if type(item) is int:
            return {"intValue": str(item)}
        if type(item) is float:
            return {"doubleValue": item}
        return {"stringValue": item}
    return [{"key": key, "value": value(item)} for key, item in values.items()]


def otlp(record):
    if record.get("schema") != "aeion.event-metadata/v1":
        raise ValueError("Unsupported event metadata version")
    resource = {"attributes": _attributes({"service.name": "aeion-playground", "service.instance.id": record["instance_id"]})}
    scope = {"name": "aeion.ledger", "version": "1"}
    log = {"timeUnixNano": record["time_unix_nano"], "observedTimeUnixNano": record["observed_unix_nano"],
           "severityNumber": SEVERITY[record["severity"]], "severityText": record["severity"].upper(),
           "body": {"stringValue": record["type"]}, "attributes": _attributes(record["attributes"]),
           "traceId": record["trace_id"], "spanId": record["span_id"], "flags": 1}
    # This is an instantaneous ledger-event span, not an invented model-call duration.
    span = {"traceId": record["trace_id"], "spanId": record["span_id"], "name": record["type"], "kind": 1,
            "startTimeUnixNano": record["time_unix_nano"], "endTimeUnixNano": record["time_unix_nano"],
            "attributes": _attributes({**record["attributes"], "aeion.span.kind": "ledger_event"})}
    return {"resourceLogs": [{"resource": resource, "scopeLogs": [{"scope": scope, "logRecords": [log]}]}]}, {
        "resourceSpans": [{"resource": resource, "scopeSpans": [{"scope": scope, "spans": [span]}]}]}
