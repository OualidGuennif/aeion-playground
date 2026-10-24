"""Canonical ledger metrics and metadata-only export to a real local collector."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .config import settings
from .store import store
from .event_export import EventExporter

AUDIT_PATH = settings.audit_path
_lock = threading.Lock()
_exporter = None
_collector_cache = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def emit(event: dict[str, Any]) -> None:
    """Wake delivery; the event and outbox already committed together in Store."""
    if _exporter:
        _exporter.wake.set()


def start_exporter():
    global _exporter
    with _lock:
        if _exporter is None:
            _exporter = EventExporter(store, AUDIT_PATH, os.environ.get("AEION_OTLP_ENDPOINT", "http://127.0.0.1:4318"))
        _exporter.start()


def stop_exporter():
    global _exporter
    with _lock:
        if _exporter:
            _exporter.stop()
            _exporter = None


def collector_health():
    """Probe the actual local receiver, not merely the presence of a file."""
    global _collector_cache
    now = time.monotonic()
    if _collector_cache and now - _collector_cache[0] < 5:
        return _collector_cache[1]
    try:
        with httpx.Client(timeout=1, trust_env=False, follow_redirects=False) as client:
            with client.stream("GET", "http://127.0.0.1:13133/") as response:
                available = response.status_code == 200
    except httpx.HTTPError:
        available = False
    result = {"available": available, "url": "http://127.0.0.1:13133"}
    _collector_cache = (now, result)
    return result


def delivery_status():
    return {**store.outbox_status(), "worker_running": bool(_exporter and _exporter.thread and _exporter.thread.is_alive()),
            "last_error": _exporter.failure if _exporter else None, "collector": collector_health(),
            "export": "local OTLP HTTP, metadata only; delivery is at least once"}


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
    budget = store.budget_snapshot()
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
        "budget_used": round(budget["reported_usd"] + budget["carried_usd"], 4),
        "budget_held": budget["held_usd"],
        "budget_unsettled_attempts": budget["unsettled_attempts"],
        "budget_remaining": round(max(0.0, budget_total - budget["exposure_usd"]), 4),
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
