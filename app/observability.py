"""Local observability projection.

Events are written as OTel-shaped JSONL under `runtime/otel/events.jsonl`. No
collector is running; the file is a local, inspectable projection of the same
SQLite ledger. KPIs are computed from real mission rows and agent runs.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings
from .store import store

OTEL_PATH = settings.poc_dir / "runtime" / "otel" / "events.jsonl"
AUDIT_PATH = settings.audit_path
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def emit(event: dict[str, Any]) -> None:
    """Append one event to the JSONL audit trail and OTel-shaped projection."""
    OTEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = event.get("ts") or _now()
    nano = int(time.time() * 1_000_000_000)
    otel = {
        "ts": timestamp,
        "observedTimeUnixNano": str(nano),
        "severityText": str(event.get("severity", "info")).upper(),
        "body": event.get("message"),
        "attributes": {
            "service.name": "aeion-parceldesk-poc",
            "aeion.event.type": event.get("type"),
            "aeion.actor": event.get("actor"),
            "aeion.mission.id": event.get("mission"),
            "aeion.fixture": event.get("fixture"),
            "aeion.payload": event.get("payload") or {},
        },
    }
    with _lock:
        with OTEL_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(otel, ensure_ascii=False) + "\n")
        with AUDIT_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def kpis(agent_rows: list[dict[str, Any]], budget_total: float) -> dict[str, Any]:
    metrics = store.metrics()
    by_status = metrics["by_status"]
    by_outcome = metrics["by_outcome"]
    completed = by_status.get("completed", 0)
    autonomous = by_outcome.get("autonomous", 0)
    assisted = by_outcome.get("assisted", 0)
    failed = by_outcome.get("failed", 0)
    unknown = by_outcome.get("unknown", 0)
    mature = autonomous + assisted + failed
    cost = metrics["cost"]
    tokens = metrics["tokens_in"] + metrics["tokens_out"]
    gate_rows = [mission for mission in store.missions(500) if mission.get("gate_decision")]
    approvals = [mission for mission in store.missions(500) if mission.get("human_approval")]
    active_agents = sum(1 for row in agent_rows if row.get("status") in {"working", "waiting"})
    return {
        "active_agents": active_agents,
        "total_agents": len(agent_rows),
        "running_missions": by_status.get("running", 0),
        "completed": completed,
        "autonomous": autonomous,
        "assisted": assisted,
        "failed": failed,
        "unknown": unknown,
        "success_rate": round((autonomous + assisted) / mature, 3) if mature else 0.0,
        "cost_total": round(cost, 4),
        "tokens_total": tokens,
        "cost_per_success": round(cost / autonomous, 4) if autonomous else None,
        "p95_seconds": metrics["p95_seconds"],
        "avg_seconds": metrics["avg_seconds"],
        "evidence_coverage": metrics["evidence_coverage"],
        "gate_interventions": len(gate_rows),
        "human_approvals": len(approvals),
        "governance_minutes": 0,
        "corrective_minutes": 0,
        "events_total": store.count_events(),
        "budget_total": budget_total,
        "budget_used": round(cost, 4),
        "budget_remaining": round(max(0.0, budget_total - cost), 4),
    }


def charts(missions: list[dict[str, Any]], runs: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(missions, key=lambda item: item.get("created_at") or "")
    series = []
    cumulative_cost = 0.0
    cumulative_tokens = 0
    for index, mission in enumerate(ordered[-40:]):
        cumulative_cost += float(mission.get("cost_usd") or 0)
        cumulative_tokens += int(mission.get("tokens_in") or 0) + int(mission.get("tokens_out") or 0)
        series.append(
            {
                "t": index + 1,
                "cost": round(cumulative_cost, 4),
                "tokens": cumulative_tokens,
                "active": 1 if mission.get("status") == "running" else 0,
                "done": 1 if mission.get("status") == "completed" else 0,
                "autonomous": 1 if mission.get("outcome") == "autonomous" else 0,
            }
        )

    model_totals: dict[str, dict[str, Any]] = {}
    palette = ["#22d3ee", "#a3e635", "#f472b6", "#fb923c", "#818cf8", "#facc15"]
    for run in runs:
        model = run.get("model") or run.get("runtime") or "unconfigured"
        entry = model_totals.setdefault(model, {"model": model, "calls": 0, "cost": 0.0, "tokens": 0})
        entry["calls"] += 1
        entry["cost"] += float(run.get("cost_usd") or 0)
        entry["tokens"] += int(run.get("tokens_in") or 0) + int(run.get("tokens_out") or 0)
    model_usage = [
        {**entry, "cost": round(entry["cost"], 4), "color": palette[index % len(palette)]}
        for index, entry in enumerate(model_totals.values())
    ]

    outcomes = {"autonomous": 0, "assisted": 0, "failed": 0, "unknown": 0}
    risks: dict[str, int] = {}
    for mission in missions:
        outcome = mission.get("outcome")
        if outcome in outcomes:
            outcomes[outcome] += 1
        risk = mission.get("risk") or "unknown"
        risks[risk] = risks.get(risk, 0) + 1

    latency = []
    for mission in missions:
        if mission.get("status") != "completed":
            continue
        try:
            start = datetime.fromisoformat(mission["started_at"])
            end = datetime.fromisoformat(mission["ended_at"])
            latency.append(round((end - start).total_seconds(), 3))
        except (KeyError, TypeError, ValueError):
            continue

    return {
        "series": series,
        "model_usage": model_usage,
        "outcomes": outcomes,
        "risks": risks,
        "latency": latency,
    }
