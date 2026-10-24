"""Deterministic routing from authorized pools and observed execution outcomes."""
from __future__ import annotations

import json
import time
from datetime import datetime

from .agent_runner import model_family


class RoutingBlocked(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def task_class(scenario):
    return ("coverage" if scenario.get("source") == "local-product" else "implementation") + ":" + scenario["risk"]


def availability_error(error):
    text = (error or "").lower()
    if any(phrase in text for phrase in ("free usage limit", "rate limit", "ratelimit", "too many requests", "quota exhausted", "usage limit reached")):
        return "rate_limit"
    if "free tier can only be used from within opencode" in text:
        return "access_restriction"
    if any(phrase in text for phrase in ("model not found", "modelnotfound", "model is not available")):
        return "catalog_unavailable"
    if any(phrase in text for phrase in ("insufficient account funds", "requires global regions", "unauthorized", "invalid api key", "no api key")):
        return "operator_action"
    return None


class ModelRouter:
    def __init__(self, ledger, policy):
        self.ledger, self.policy = ledger, policy

    def remaining(self):
        deadline = datetime.fromisoformat(self.policy["authorization_expires_at"].replace("Z", "+00:00")).timestamp()
        remaining = deadline - time.time()
        if remaining < 1:
            raise RoutingBlocked("AUTHORIZATION_EXPIRED", "The authorized autonomous work window expired; no model call started")
        snapshot = self.ledger.budget_snapshot() if hasattr(self.ledger, "budget_snapshot") else {"exposure_usd": self.ledger.metrics()["cost"], "unsettled_attempts": 0}
        if snapshot["unsettled_attempts"]:
            raise RoutingBlocked("EXPOSURE_HELD", "Unsettled inference exposure blocks new model calls; no automatic refund/retry")
        if snapshot["exposure_usd"] >= self.policy["budget_usd"]:
            raise RoutingBlocked("BUDGET_EXHAUSTED", "The reported local model budget is exhausted")
        return remaining

    def analyze(self, scenario, role):
        classification = task_class(scenario)
        records, missions, stats, included = {}, {}, {}, []
        for run in self.ledger.runs(limit=500):
            if run["agent"] != role:
                continue
            mission_id = run["mission_id"]
            if mission_id not in missions:
                missions[mission_id] = self.ledger.mission(mission_id)
            mission = missions[mission_id]
            if not mission:
                continue
            admitted = json.loads(mission.get("contract_json") or "{}").get("scenario", {})
            if not admitted or task_class(admitted) != classification:
                continue
            family = model_family(run.get("model") or "")
            if records.get(family, 0) >= self.policy["history_window"]:
                continue
            records[family] = records.get(family, 0) + 1
            included.append(run["run_id"])
            row = stats.setdefault(family, {"samples": 0, "passed": 0, "deterministic_failures": 0,
                                            "protocol_failures": 0, "cost_usd": 0.0, "duration_ms": 0.0, "timed_samples": 0})
            error = run.get("error") or ""
            if availability_error(error):
                continue  # Availability is not evidence of coding quality.
            if error.startswith("Invalid patch protocol response:"):
                row["protocol_failures"] += 1
                continue
            if run["status"] != "completed":
                continue
            if role == "sentinel" and run.get("verdict") != "OK":
                continue  # A semantic rejection is not proof of reviewer failure.
            tests = json.loads(mission.get("tests_json") or "{}")
            code = mission.get("gate_reason_code")
            if code in {"TEST_EXECUTOR_CHANGED", "EXPOSURE_HELD", "BUDGET_EXCEEDED"}:
                continue  # Trusted executor/accounting failures are not coding quality.
            if role == "forge" and code in {"TEST_FAILURE", "TASK_SCOPE", "PROTECTED_PATH", "MODEL_PATCH_INVALID", "OUT_OF_SCOPE",
                                             "NO_PATCH", "TEST_COVERAGE", "TESTS_INCOMPLETE", "TEST_EXECUTION_ERROR"}:
                row["deterministic_failures"] += 1
            elif tests.get("total", 0) > 0 and tests.get("passed") == tests.get("total") and not tests.get("error"):
                if role == "forge" or run.get("verdict") == "OK":
                    row["passed"] += 1
            row["samples"] += 1
            row["cost_usd"] += float(run.get("cost_usd") or 0)
            raw = json.loads(run.get("raw_json") or "{}")
            if raw.get("duration_ms", 0) > 0:
                row["duration_ms"] += raw["duration_ms"]
                row["timed_samples"] += 1
        analysis = {"task_class": classification, "role": role, "families": stats, "run_ids": included,
                    "policy_digest": self.policy["digest"], "analyzed_at": time.time()}
        self.ledger.set_kv(f"routing:analysis:{classification}:{role}", json.dumps(analysis, sort_keys=True))
        return analysis

    def plan(self, scenario, role, author_model=""):
        self.remaining()
        analysis = self.analyze(scenario, role)
        rule = self.policy["roles"][role]
        excluded_family = model_family(author_model) if author_model else None
        eligible, skipped = [], []
        for model in rule["models"]:
            if model_family(model) == excluded_family:
                skipped.append({"model": model, "reason": "same author family"})
                continue
            state = json.loads(self.ledger.get_kv(f"routing:health:{model}", "{}"))
            blocked = (state.get("classification") == "operator_action" and state.get("policy_digest") == self.policy["digest"]) or state.get("retry_at", 0) > time.time()
            if blocked:
                skipped.append({"model": model, "reason": state.get("classification"), "observation": state.get("run_id")})
                continue
            eligible.append(model)
        def rank(model):
            family = model_family(model)
            stats = analysis["families"].get(family, {})
            penalty = stats.get("deterministic_failures", 0) + max(0, stats.get("protocol_failures", 0) - 1)
            sampled = stats.get("samples", 0) >= 3
            return (penalty >= 2,
                    -stats.get("passed", 0) / max(1, stats.get("samples", 0)) if sampled else 0,
                    stats.get("cost_usd", 0) / stats["samples"] if sampled else float("inf"),
                    stats.get("duration_ms", 0) / stats["timed_samples"] if stats.get("timed_samples", 0) >= 3 else float("inf"),
                    family != rule["preferred_family"], rule["models"].index(model))
        candidates = []
        for free in (True, False):
            tier = [model for model in eligible if model.endswith("-free") == free]
            if tier:
                candidates.append(min(tier, key=rank))
        return {"candidates": candidates, "skipped": skipped, "analysis": analysis,
                "rule": "one eligible free attempt, then authorized paid fallback on known availability refusal"}

    def observe(self, model, result, observed_at=None):
        if (getattr(result, "raw", {}) or {}).get("dispatch") == "not_started":
            return  # An unavailable local backend says nothing about model availability.
        observed_at = observed_at or time.time()
        previous = json.loads(self.ledger.get_kv(f"routing:health:{model}", "{}"))
        if previous.get("observed_at", 0) > observed_at:
            return
        classification = availability_error(result.error) if result.status != "completed" else None
        mediated = (getattr(result, "raw", {}) or {}).get("inference")
        if mediated and mediated.get("state") != "refused" and result.status != "completed":
            classification = "unknown_outcome"
        if classification is None and result.status != "completed" and not (getattr(result, "raw", {}) or {}).get("finish_reason") == "stop":
            classification = "unknown_outcome"
        state = {"model": model, "observed_at": observed_at, "run_id": result.run_id,
                 "classification": classification, "error": result.error,
                 "retry_at": observed_at + self.policy["cooldown_seconds"] if classification in {"rate_limit", "access_restriction", "unknown_outcome", "catalog_unavailable"} else 0,
                 "policy_digest": self.policy["digest"]}
        self.ledger.set_kv(f"routing:health:{model}", json.dumps(state, sort_keys=True))
