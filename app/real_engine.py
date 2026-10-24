"""Real local engine for the Æion ParcelDesk POC.

The engine is a thin orchestrator over real components:

- Graphify AST graph (`runtime/graphify/graphify-out/graph.json`)
- SQLite FTS5 memory (`runtime/aeion.db`)
- Paperclip API (`PAPERCLIP_BASE_URL`)
- Git sandbox + pytest (`sandbox/parceldesk` → `runtime/sandbox/runs`)
- OpenCode CLI (strict sandboxed permissions) or Claude Code CLI when authenticated
- CROA CLI and the OPS integrity checker
- Local OTel-shaped JSONL + SQLite metrics

There is no random tick, no seeded mission and no synthetic model call. When a
component is unavailable, the mission stops with the real reason.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent_runner import AgentResult, agent_runner, model_family
from .config import settings
from .delivery import LocalDelivery
from .evidence import evidence_writer
from .graphify import graph_projection
from .memory import memory_store
from .model_routing import ModelRouter, RoutingBlocked, availability_error
from .operator_settings import load_routing_policy
from .observability import charts as build_charts
from .observability import emit, kpis as build_kpis
from .paperclip import paperclip
from .policy_engine import PolicyEngine
from .patch_protocol import PATCH_PROTOCOL, parse_patch, source_context
from .sandbox import sandbox
from .store import store, utc_now

VERSION = "1.10.0-real"
MAX_QUEUED_MISSIONS = 20
PROVIDER_COOLDOWN_SECONDS = 3600

AGENTS = [
    {"id": "atlas", "name": "Atlas", "role": "Intake & contract", "effort": "low", "color": "#c084fc", "initials": "AT"},
    {"id": "scout", "name": "Scout", "role": "Recon & context", "effort": "medium", "color": "#22d3ee", "initials": "SC"},
    {"id": "forge", "name": "Forge", "role": "Patch author", "effort": "high", "color": "#a3e635", "initials": "FG"},
    {"id": "probe", "name": "Probe", "role": "Test execution", "effort": "low", "color": "#facc15", "initials": "PR"},
    {"id": "sentinel", "name": "Sentinel", "role": "Independent review", "effort": "high", "color": "#f472b6", "initials": "SN"},
    {"id": "gate", "name": "Gate", "role": "Policy authority", "effort": "—", "color": "#fb923c", "initials": "GT"},
]

SCENARIOS: dict[str, dict[str, Any]] = {
    "PD00": {
        "fixture": "PD00",
        "kind": "drill-baseline",
        "title": "Sandbox baseline verification",
        "subtitle": "Run the real contract suite on a clean sandbox copy. No patch, no agent.",
        "risk": "low",
        "priority": "low",
        "memory_query": "pricing quote contract tests baseline defect",
        "task": "",
    },
    "PD01": {
        "fixture": "PD01",
        "kind": "agent",
        "title": "Started-kilogram rounding",
        "subtitle": "The quote rule floors 1,001–1,999 g to the first kilogram.",
        "risk": "high",
        "priority": "high",
        "memory_query": "pricing quote started kilogram rounding boundary contract",
        "task": (
            "Repair the started-kilogram rounding defect in app/pricing.py. "
            "The contract is 500 cents for the first started kilogram and 300 cents for every "
            "additional started kilogram. tests/test_pricing.py encodes the contract. "
            "Run `python -m pytest -q` and make every test pass. Do not delete, skip or weaken a test."
        ),
    },
    "PD02": {
        "fixture": "PD02",
        "kind": "agent",
        "title": "Boundary regression coverage",
        "subtitle": "Add a missing started-kilogram boundary test and repair what it reveals.",
        "risk": "normal",
        "priority": "medium",
        "memory_query": "pricing boundary tests 1 gram 1001 1999 30000 contract",
        "task": (
            "Audit the boundary contract in tests/test_pricing.py and app/pricing.py. "
            "Add at least one missing started-kilogram boundary test (for example 1 g, 999 g, "
            "1001 g, 1999 g, 2001 g or 29 999 g), repair any defect the suite reveals, and make "
            "the full suite pass. Do not delete, skip or weaken an existing test."
        ),
    },
    "PD03": {
        "fixture": "PD03",
        "kind": "drill-protected",
        "title": "Protected policy change",
        "subtitle": "A real patch repairs pricing and widens the gate policy. The gate must deny it.",
        "risk": "critical",
        "priority": "critical",
        "memory_query": "protected path policy deny precedence authority",
        "task": "",
    },
    "PD04": {
        "fixture": "PD04", "kind": "agent",
        "protocol": PATCH_PROTOCOL,
        "title": "Exhaustive started-kilogram regression coverage",
        "subtitle": "Extend tests on the accepted local product; original code and tests stay fixed.",
        "risk": "low", "priority": "medium",
        "memory_query": "started kilogram pricing boundaries approved local release regression coverage",
        "source": "local-product", "delivery": "local",
        "allowed_paths": ["tests/test_boundaries_extended.py"], "minimum_tests": 49,
        "new_test_file": "tests/test_boundaries_extended.py", "minimum_new_tests": 30,
        "task": (
            "The local product already contains the human-approved pricing repair. "
            "Create ONLY tests/test_boundaries_extended.py. Add 30 parametrized regression cases "
            "for quote(1000*k + 1) == 500 + 300*k for every integer k from 0 through 29. "
            "Import quote from app.pricing. Preserve every existing file, implementation and test. "
            "The worker runs the full pytest suite; at least 49 collected cases must all pass. "
            "Do not skip tests or edit the pricing code, policy, configuration or Git history."
        ),
    },
}

DRILL_AGENT = "operator-harness"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        decoded = json.loads(value)
        return decoded if isinstance(decoded, type(default)) else default
    except (json.JSONDecodeError, TypeError):
        return default


class RealEngine:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._started = False
        self._wake = threading.Event()
        self._worker: threading.Thread | None = None
        self._warmup_started = False
        self._agent_probe: tuple[float, dict[str, Any]] | None = None
        self._baseline: dict[str, Any] | None = None
        self._baseline_at = 0.0
        self._ops: dict[str, Any] | None = None
        self._ops_at = 0.0
        self._croa: dict[str, Any] | None = None
        self._croa_at = 0.0
        self._issues: dict[str, Any] | None = None
        self._issues_at = 0.0

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._started:
            return
        self.recover_interrupted()
        agent_runner.resume()
        self._started = True
        memory_store.seed_sources()
        emit(
            store.add_event(
                type="engine.started",
                actor="engine",
                message=f"Real engine {VERSION} started.",
                severity="success",
            )
        )
        if not self._warmup_started:
            self._warmup_started = True
            thread = threading.Thread(target=self._warmup, name="aeion-warmup", daemon=True)
            thread.start()
        self._worker = threading.Thread(target=self._dispatch_loop, name="aeion-mission-queue", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._started = False
        self._wake.set()
        agent_runner.cancel_all()
        if self._worker:
            self._worker.join(timeout=5)

    def recover_interrupted(self) -> None:
        """Never replay an external call whose result was lost in a restart."""
        for mission in store.running_missions():
            try:
                with store.mission_lock(mission["id"]):
                    if store.mission(mission["id"])["status"] == "running":
                        if store.get_kv(f"seal:{mission['id']}") is not None:
                            try:
                                self._resume_seal(mission["id"])
                            except Exception as error:
                                self._execution_failed(mission["id"], error)
                            continue
                        for step in store.steps(mission["id"]):
                            if step["status"] == "running":
                                store.update_step(step["id"], status="blocked", detail="Worker interrupted; result unknown.", ended_at=_now())
                        self._block(
                            mission["id"], "EXECUTION_INTERRUPTED",
                            "The worker exited before recording a terminal result. Outputs and provider usage may be incomplete. "
                            "The original checkout is preserved; no model call is replayed automatically.",
                        )
            except BlockingIOError:
                continue  # Another live worker still owns this mission.
        for mission in store.missions(limit=1000):
            if mission["status"] == "completed" and _loads(mission.get("contract_json"), {}).get("scenario", {}).get("delivery") == "local" and store.get_kv(f"delivery:{mission['id']}") is None:
                self._deliver_local(mission["id"])

    def _dispatch_loop(self) -> None:
        while self._started:
            try:
                worked = self.dispatch_once()
            except Exception as error:
                emit(store.add_event(type="queue.error", actor="engine", severity="error", message=f"{type(error).__name__}: {error}"))
                worked = False
            if not worked:
                self._wake.wait(1)
                self._wake.clear()

    def dispatch_once(self) -> bool:
        for mission in store.queued_missions():
            if self._execute_mission(mission["id"]):
                return True
        return False

    def _warmup(self) -> None:
        self.baseline_check()
        self.ops_check()
        self.croa_check()
        self.agent_probe(force=True)
        paperclip.health(force=True)
        self.paperclip_issues(force=True)

    # -------------------------------------------------------------------- probes
    def agent_probe(self, force: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force and self._agent_probe and now - self._agent_probe[0] < 30:
            return self._agent_probe[1]
        probe = agent_runner.probe()
        self._agent_probe = (now, probe)
        return probe

    def baseline_check(self, force: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force and self._baseline and now - self._baseline_at < 300:
            return self._baseline
        result = sandbox.baseline_check()
        self._baseline = result
        self._baseline_at = now
        store.set_kv("sandbox_baseline", json.dumps(result))
        return result

    def ops_check(self, force: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force and self._ops and now - self._ops_at < 300:
            return self._ops
        result = sandbox.run_ops_integrity()
        self._ops = result
        self._ops_at = now
        store.set_kv("ops_integrity", json.dumps(result))
        return result

    def croa_check(self, force: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force and self._croa and now - self._croa_at < 300:
            return self._croa
        result = sandbox.run_croa_audit()
        self._croa = result
        self._croa_at = now
        store.set_kv("croa_control", json.dumps(result))
        return result

    def paperclip_issues(self, force: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force and self._issues and now - self._issues_at < 15:
            return self._issues
        result = paperclip.list_issues(limit=50)
        self._issues = result
        self._issues_at = now
        return result

    # -------------------------------------------------------------------- launch
    def launch_mission(
        self, fixture: str | None = None, *, local_patch: str | None = None,
        request_id: str | None = None, defer: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            return self._launch_mission(fixture, local_patch=local_patch, request_id=request_id, defer=defer)

    def _launch_mission(
        self, fixture: str | None = None, *, local_patch: str | None = None,
        request_id: str | None = None, defer: bool = False,
    ) -> dict[str, Any]:
        key = (fixture or "PD01").upper()
        if key not in SCENARIOS:
            raise ValueError(f"Unknown scenario: {key}")
        if request_id is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", request_id):
            raise ValueError("Request id must contain 1–128 letters, digits, dots, colons, underscores or hyphens.")
        scenario = dict(SCENARIOS[key])
        if local_patch is not None:
            if scenario["kind"] != "agent" or not local_patch.strip():
                raise ValueError("A non-empty local patch requires scenario PD01 or PD02.")
            scenario.update(kind="local-patch", patch=local_patch)
        return self._admit_scenario(
            scenario, request_id=request_id, defer=defer,
            request={"fixture": key, "patch": local_patch},
        )

    def enqueue_test_task(
        self, *, request_id: str, title: str, task: str,
        test_file: str, minimum_new_tests: int,
    ) -> dict[str, Any]:
        """User-defined coverage work on the accepted product, with fixed authority."""
        title, task = title.strip(), task.strip()
        if not 4 <= len(title) <= 120 or not 20 <= len(task) <= 4000:
            raise ValueError("Title requires 4–120 characters; task requires 20–4000 characters.")
        if not re.fullmatch(r"tests/test_[a-z][a-z0-9_]{0,72}\.py", test_file):
            raise ValueError("Choose a new tests/test_<name>.py file using lowercase letters, digits and underscores.")
        if isinstance(minimum_new_tests, bool) or not isinstance(minimum_new_tests, int) or not 1 <= minimum_new_tests <= 100:
            raise ValueError("minimum_new_tests must be an integer from 1 through 100.")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", request_id):
            raise ValueError("A valid stable request_id is required.")
        request = {"type": "product-test-task", "title": title, "task": task,
                   "test_file": test_file, "minimum_new_tests": minimum_new_tests}
        scenario = {
            "fixture": "TASK", "kind": "agent", "title": title,
            "protocol": PATCH_PROTOCOL,
            "subtitle": "User-defined regression coverage on the accepted local product.",
            "risk": "low", "priority": "medium", "memory_query": title + " pricing regression contract",
            "source": "local-product", "delivery": "local", "allowed_paths": [test_file],
            "new_test_file": test_file, "minimum_new_tests": minimum_new_tests,
            "task": (
                task + f"\nTask limits: create only {test_file}. Preserve every existing file and test. "
                f"Add at least {minimum_new_tests} collected passing cases; the worker runs the full suite. "
                "Do not edit implementation, policy, configuration or Git history."
            ),
        }
        with self._lock:
            return self._admit_scenario(scenario, request_id=request_id, request=request, defer=True)

    def _admit_scenario(
        self, scenario: dict[str, Any], *, request_id: str | None,
        request: dict[str, Any], defer: bool,
    ) -> dict[str, Any]:
        # Replays must survive dependency drift, deletion, capacity and provider outages.
        existing = store.replay_request(request_id, request)
        if existing:
            return existing
        routing = load_routing_policy() if scenario["kind"] == "agent" and settings.agent_runtime == "opencode-cli" else None
        if routing:
            ModelRouter(store, routing).remaining()
        if scenario["kind"] == "agent":
            profile = "adaptive OpenCode routing" if routing else settings.opencode_model if settings.agent_runtime == "opencode-cli" else settings.agent_model or "claude default"
        elif scenario["kind"] == "local-patch":
            profile = "operator-submitted local patch · no model API call"
        else:
            profile = "local drill harness"
        source_release = None
        if scenario.get("source") == "local-product":
            head = _loads(store.get_kv("product_head"), {})
            if not head:
                raise RuntimeError("No accepted local product release. Deliver an approved mission first.")
            verified = LocalDelivery(store, sandbox).verify(head["mission"], head["packet_digest"])
            source_release = {key: verified[key] for key in ("mission", "packet_digest", "commit", "tree_digest", "path", "tests")}
            if scenario.get("new_test_file"):
                if (Path(source_release["path"]) / scenario["new_test_file"]).exists():
                    raise ValueError("The task must create a new test file; the selected path already exists in the accepted product.")
                minimum = source_release["tests"]["total"] + scenario["minimum_new_tests"]
                if minimum > 1000:
                    raise ValueError("The admitted minimum test count cannot exceed 1000 cases.")
                scenario = {**scenario, "minimum_tests": minimum}
        record = {
            "scenario": scenario["kind"],
            "fixture": scenario["fixture"],
            "title": scenario["title"],
            "subtitle": scenario["subtitle"],
            "risk": scenario["risk"],
            "priority": scenario["priority"],
            "profile": profile,
            "status": "queued",
            "outcome": None,
            "progress": 0.0,
            "current_step": "Queued",
            "current_agent": "atlas",
            "created_at": _now(),
            "blocked_reason": None,
            "request_id": request_id,
            "contract_json": json.dumps({
                "scenario": scenario,
                "template_digest": source_release["tree_digest"] if source_release else sandbox.template_digest(),
                "policy_digest": sandbox.policy_digest(),
                "runtime": self._runtime_contract(),
                "test_executor": sandbox.executor_contract(),
                "source_release": source_release,
                "request": request,
                **({"routing": routing} if routing else {}),
            }, sort_keys=True),
        }
        record, created = store.admit_mission(
            record, defer=defer, capacity=settings.max_active_missions, queue_limit=MAX_QUEUED_MISSIONS,
        )
        if not created:
            return record
        mission_id = record["id"]
        emit(
            store.add_event(
                type="mission.created",
                actor="atlas",
                mission=mission_id,
                fixture=scenario["fixture"],
                message=f"{scenario['title']} admitted · risk {scenario['risk']} · {profile}",
                severity="info",
                payload={"scenario": scenario["kind"]},
            )
        )

        if not defer and not self._started:
            thread = threading.Thread(target=self._execute_mission, args=(mission_id,), name=f"mission-{mission_id}", daemon=True)
            thread.start()
        self._wake.set()
        return store.mission(mission_id) or record

    @staticmethod
    def _runtime_contract() -> dict[str, str]:
        return {
            "runtime": settings.agent_runtime, "author": settings.opencode_model if settings.agent_runtime == "opencode-cli" else settings.agent_model,
            "reviewer": settings.opencode_review_model if settings.agent_runtime == "opencode-cli" else settings.agent_model,
        }

    def _create_issue(self, record: dict[str, Any]) -> None:
        mission_id = record["id"]
        issue = paperclip.create_issue(record)
        if issue.get("created"):
            store.update_mission(
                mission_id,
                paperclip_issue_id=issue.get("issue_id"),
                paperclip_identifier=issue.get("identifier"),
                paperclip_url=issue.get("url"),
            )
            emit(
                store.add_event(
                    type="paperclip.issue.created",
                    actor="paperclip",
                    mission=mission_id,
                    fixture=record["fixture"],
                    message=f"Paperclip issue {issue.get('identifier')} created.",
                    severity="success",
                    payload={"url": issue.get("url")},
                )
            )
        else:
            emit(
                store.add_event(
                    type="paperclip.issue.unavailable",
                    actor="paperclip",
                    mission=mission_id,
                    fixture=record["fixture"],
                    message=f"Paperclip issue not created: {issue.get('error') or 'service unavailable'}",
                    severity="warning",
                )
            )

    # ----------------------------------------------------------------- execution
    def _step(
        self,
        mission_id: str,
        idx: int,
        agent: str,
        label: str,
        detail: str,
        status: str,
        *,
        progress: float | None = None,
        cost: float = 0.0,
        tokens_in: int = 0,
        tokens_out: int = 0,
        evidence_path: str | None = None,
    ) -> int:
        existing = next((step for step in store.steps(mission_id) if step["idx"] == idx), None)
        if existing:
            step_id = existing["id"]
            store.update_step(step_id, agent=agent, label=label, detail=detail)
        else:
            step_id = store.add_step(
                mission_id=mission_id, idx=idx, agent=agent, label=label, detail=detail,
                status="running" if status == "running" else "pending", progress=progress or 0.0,
            )
        if status in {"done", "failed", "blocked", "skipped"}:
            store.update_step(
                step_id,
                status=status,
                progress=1.0 if status == "done" else 0.0,
                ended_at=_now(),
                cost_usd=cost,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                evidence_path=evidence_path,
            )
        elif status == "running":
            store.update_step(step_id, status="running", progress=progress or 0.1)
        return step_id

    def _execute_mission(self, mission_id: str, scenario: dict[str, Any] | None = None) -> bool:
        # The persisted admission contract wins over caller or current catalog data.
        try:
            with store.mission_lock(mission_id):
                mission = store.mission(mission_id)
                if not mission or mission["status"] != "queued":
                    return False
                contract = _loads(mission.get("contract_json"), {})
                admitted = contract.get("scenario", {})
                if not isinstance(admitted, dict):
                    admitted = {}
                if admitted.get("kind") == "agent" and not contract.get("routing") and time.time() < float(store.get_kv("provider_retry_at", "0")):
                    if mission.get("current_step") != "Provider cooldown":
                        store.update_mission(mission_id, current_step="Provider cooldown")
                    return False
                if not store.claim_mission(mission_id, settings.max_active_missions):
                    return False
                try:
                    source_release = contract.get("source_release")
                    source_path = Path(source_release["path"]) if source_release else None
                    if source_release and _loads(store.get_kv("product_head"), {}).get("commit") != source_release.get("commit"):
                        self._block(mission_id, "STALE_BASE", "The accepted local product advanced after admission. Submit a fresh task.")
                    elif (
                        not admitted or contract.get("template_digest") != sandbox.template_digest(source_path)
                        or contract.get("policy_digest") != sandbox.policy_digest()
                    ):
                        self._block(mission_id, "CONTRACT_CHANGED", "The trusted template or policy changed, or the persisted admission contract is missing. Submit a new task.")
                    elif admitted.get("kind") == "agent" and contract.get("runtime") != self._runtime_contract():
                        self._block(mission_id, "RUNTIME_CHANGED", "The model runtime changed after admission. Submit a new task explicitly.")
                    elif "test_executor" in contract and contract["test_executor"] != sandbox.executor_contract():
                        self._block(mission_id, "TEST_EXECUTOR_CHANGED", "The canonical test executor changed after admission; no model or test call started.")
                    elif contract.get("routing") and contract["routing"]["digest"] != (load_routing_policy() or {}).get("digest"):
                        self._block(mission_id, "ROUTING_CHANGED", "The authorized model-routing policy changed after admission")
                    elif admitted.get("kind") == "agent" and store.metrics()["cost"] >= settings.budget_usd:
                        self._block(mission_id, "BUDGET_EXHAUSTED", "The recorded workspace budget is exhausted; no model call was started.")
                    elif store.get_kv(f"review_retry:{mission_id}"):
                        self._resume_review(mission_id, admitted)
                    else:
                        self._run_mission(mission_id, admitted)
                except Exception as error:
                    self._execution_failed(mission_id, error)
                return True
        except BlockingIOError:
            return False

    def _call_agent(self, *, mission_id: str, agent_id: str, route_author_model: str = "", **kwargs) -> AgentResult:
        mission = store.mission(mission_id)
        contract = _loads(mission.get("contract_json"), {})
        frozen = contract.get("routing")
        current = load_routing_policy()
        try:
            if frozen and frozen["digest"] != (current or {}).get("digest"):
                raise RoutingBlocked("ROUTING_CHANGED", "The authorized routing policy changed during execution")
            remaining = ModelRouter(store, current).remaining() if current else None
            router = ModelRouter(store, frozen) if frozen else None
            plan = router.plan(contract["scenario"], agent_id, route_author_model) if router else None
            candidates = plan["candidates"] if plan else [None]
            if not candidates:
                raise RoutingBlocked("MODEL_UNAVAILABLE", "No authorized model is eligible under the observed availability and family rules")
            if plan:
                emit(store.add_event(type="model.route", actor="engine", mission=mission_id,
                                    message=f"Selected bounded {agent_id} route from actual session outcomes.", payload=plan))
            for index, model in enumerate(candidates):
                if "test_executor" in contract and contract["test_executor"] != sandbox.executor_contract():
                    raise RoutingBlocked("TEST_EXECUTOR_CHANGED", "The canonical test executor changed before the next model call")
                latest = load_routing_policy()
                if frozen and frozen["digest"] != (latest or {}).get("digest"):
                    raise RoutingBlocked("ROUTING_CHANGED", "The routing policy changed before the next model call")
                remaining = ModelRouter(store, latest).remaining() if latest else None
                overrides = {"model_override": model} if model else {}
                if remaining is not None:
                    overrides["timeout_seconds"] = min(settings.agent_timeout, remaining)
                result = agent_runner.run(mission_id=mission_id, agent_id=agent_id, **kwargs, **overrides)
                if router:
                    result.raw["routing"] = {"policy_digest": frozen["digest"], "attempt": index + 1,
                                             "selected_model": model, "task_class": plan["analysis"]["task_class"],
                                             "rule": plan["rule"], "skipped": plan["skipped"], "analysis": plan["analysis"]}
                    router.observe(model, result)
                store.add_run({"mission_id": mission_id, "agent": agent_id, **result.to_dict(),
                               "cost_usd": result.cost_usd})
                if result.status == "completed" or not router or not availability_error(result.error):
                    return result
            return result
        except RoutingBlocked as error:
            emit(store.add_event(type="model.route_blocked", actor="engine", mission=mission_id,
                                message=str(error), severity="error", payload={"code": error.code}))
            # A decision that made no CLI call is an event, not a fabricated agent run.
            return AgentResult(status="blocked", runtime=settings.agent_runtime,
                               run_id=f"routing-{mission_id}-{agent_id}", error=str(error), raw={"block_code": error.code})

    def _run_mission(self, mission_id: str, scenario: dict[str, Any]) -> None:
        mission = store.mission(mission_id)
        if not mission:
            return
        store.update_mission(
            mission_id,
            status="running",
            started_at=_now(),
            current_step="Intake",
            current_agent="atlas",
            progress=0.05,
        )
        emit(
            store.add_event(
                type="mission.started",
                actor="atlas",
                mission=mission_id,
                fixture=scenario["fixture"],
                message="Intake started · contract frozen.",
                severity="info",
            )
        )
        self._create_issue(mission)

        # 0 · Intake ---------------------------------------------------------
        try:
            contract = _loads(mission.get("contract_json"), {})
            run = sandbox.create_run(mission_id, source_release=contract.get("source_release"))
        except Exception as error:  # noqa: BLE001 - surface the real failure
            self._block(mission_id, "SANDBOX_FAILED", f"Sandbox creation failed: {error}", step_index=0)
            return
        hashes = sandbox.file_hashes(Path(run["path"]))
        contract = _loads(mission.get("contract_json"), {})
        if sandbox.template_digest(Path(run["path"])) != contract.get("template_digest"):
            self._block(mission_id, "CONTRACT_CHANGED", "The copied sandbox differs from the admitted template.")
            return
        policy_digest = sandbox.policy_digest()
        store.update_mission(
            mission_id,
            sandbox_path=run["path"],
            base_commit=run["base_commit"],
            progress=0.12,
        )
        self._step(
            mission_id,
            0,
            "atlas",
            "Intake",
            f"Contract frozen · base {run['base_commit'][:10]} · policy {policy_digest[:10]}",
            "done",
        )
        emit(
            store.add_event(
                type="sandbox.created",
                actor="atlas",
                mission=mission_id,
                fixture=scenario["fixture"],
                message=f"Sandbox committed at {run['base_commit'][:10]} · {len(hashes)} contract files hashed.",
                severity="success",
                payload={"base_commit": run["base_commit"], "hashes": hashes},
            )
        )

        # 1 · Recon ----------------------------------------------------------
        store.update_mission(mission_id, current_step="Recon", current_agent="scout", progress=0.2)
        self._step(mission_id, 1, "scout", "Recon", "Recall memory and query the Graphify AST graph.", "running")
        recall = memory_store.recall(scenario["memory_query"], mission_id=mission_id, limit=4)
        graph_view = graph_projection.view()
        graph_context = "\n".join(
            f"- {node['label']} ({node['meta']}, degree {node['degree']})"
            for node in graph_view.get("nodes", [])[:6]
        )
        memory_context = "\n".join(
            f"- [{row['source']}] {row['statement']}" for row in recall.get("rows", [])
        ) or "no memory hit"
        self._step(
            mission_id,
            1,
            "scout",
            "Recon",
            f"{len(recall.get('rows', []))} memory hit(s) · {graph_view.get('nodes_total', 0)} AST nodes · {recall.get('latency_ms', 0)} ms",
            "done",
        )
        store.update_mission(mission_id, progress=0.28)
        emit(
            store.add_event(
                type="context.recalled",
                actor="scout",
                mission=mission_id,
                fixture=scenario["fixture"],
                message=f"Recall returned {len(recall.get('rows', []))} advisory item(s) in {recall.get('latency_ms', 0)} ms.",
                severity="info",
                payload={"items": recall.get("items", []), "graph_digest": graph_view.get("digest")},
            )
        )

        # 2 · Patch ----------------------------------------------------------
        store.update_mission(mission_id, current_step="Patch", current_agent="forge", progress=0.36)
        cost = 0.0
        tokens_in = 0
        tokens_out = 0
        agent_error: str | None = None
        runtime_label = "OpenCode" if settings.agent_runtime == "opencode-cli" else "Claude Code CLI"
        protocol = scenario.get("protocol")
        context = ""
        if scenario["kind"] == "local-patch":
            applied = sandbox.apply_patch(Path(run["path"]), scenario["patch"])
            if applied.returncode != 0:
                self._block(mission_id, "LOCAL_PATCH_INVALID", applied.stderr.strip() or "git apply failed", step_index=2)
                return
            detail = "Operator-submitted local patch applied. No model API call or independent review is claimed."
            self._step(mission_id, 2, "local-contributor", "Patch", detail, "done")
            emit(store.add_event(
                type="local.patch.applied", actor="local-contributor", mission=mission_id,
                fixture=scenario["fixture"], message=detail, severity="info",
                payload={"patch_digest": _sha(scenario["patch"]), "cost_basis": "No model API call; external editor/chat usage is not measured."},
            ))
        elif scenario["kind"].startswith("drill"):
            detail = self._apply_drill(Path(run["path"]), scenario["kind"])
            self._step(mission_id, 2, DRILL_AGENT, "Patch", detail, "done")
            emit(
                store.add_event(
                    type="drill.patch.applied",
                    actor=DRILL_AGENT,
                    mission=mission_id,
                    fixture=scenario["fixture"],
                    message=detail,
                    severity="warning" if scenario["kind"] == "drill-protected" else "info",
                )
            )
        else:
            if protocol:
                if protocol != PATCH_PROTOCOL:
                    self._block(mission_id, "PATCH_PROTOCOL", "Unknown admitted patch protocol.", step_index=2)
                    return
                try:
                    context = source_context(Path(run["path"]))
                except (OSError, ValueError) as error:
                    self._block(mission_id, "PATCH_CONTEXT", str(error), step_index=2)
                    return
            self._step(mission_id, 2, "forge", "Patch", f"{runtime_label} is proposing a patch." if protocol else f"{runtime_label} is editing the sandbox.", "running")
            result = self._call_agent(
                mission_id=mission_id,
                agent_id="forge",
                role="the patch author",
                title=scenario["title"],
                scenario=scenario["kind"],
                risk=scenario["risk"],
                task=scenario["task"],
                cwd=Path(run["path"]),
                memory_context=memory_context,
                graph_context=graph_context,
                protocol=protocol,
                source_context=context,
            )
            measured = store.mission(mission_id)
            cost, tokens_in, tokens_out = measured["cost_usd"], measured["tokens_in"], measured["tokens_out"]
            if contract.get("routing") and result.model:
                store.update_mission(mission_id, profile=result.model)
            if result.status != "completed":
                agent_error = result.error or "Agent run failed"
                self._step(
                    mission_id,
                    2,
                    "forge",
                    "Patch",
                    agent_error,
                    "blocked",
                    cost=result.cost_usd,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                )
                emit(
                    store.add_event(
                        type="agent.blocked",
                        actor="forge",
                        mission=mission_id,
                        fixture=scenario["fixture"],
                        message=agent_error,
                        severity="error",
                        payload={"run_id": result.run_id, "runtime": result.runtime},
                    )
                )
                code = result.raw.get("block_code") or (("MODEL_UNAVAILABLE" if availability_error(agent_error) else "AGENT_BLOCKED") if contract.get("routing") else
                       "PROVIDER_LIMIT" if self._provider_limit(agent_error) else "AGENT_BLOCKED")
                self._block(mission_id, code, agent_error, step_index=2)
                return
            if protocol:
                if (sandbox.template_digest(Path(run["path"])) != contract["template_digest"]
                        or sandbox._git(Path(run["path"]), "rev-parse", "HEAD").stdout.strip() != run["base_commit"]):
                    self._block(mission_id, "PROPOSAL_MUTATION", "The text-only author modified the checkout or Git HEAD.", step_index=2)
                    return
                try:
                    patch, summary = parse_patch(result.result_text)
                except ValueError as error:
                    self._block(mission_id, "PATCH_PROTOCOL", str(error), step_index=2)
                    return
                applied = sandbox.apply_patch(Path(run["path"]), patch)
                if applied.returncode:
                    self._block(mission_id, "MODEL_PATCH_INVALID", applied.stderr.strip() or "git apply failed", step_index=2)
                    return
                emit(store.add_event(
                    type="model.patch.applied", actor="forge", mission=mission_id, fixture=scenario["fixture"],
                    message="Worker applied the model's patch proposal: " + summary, severity="info",
                    payload={"run_id": result.run_id, "protocol": protocol, "patch_digest": _sha(patch), "source_digest": _sha(context)},
                ))
            early_diff = sandbox.collect_changes(Path(run["path"]), base_commit=run["base_commit"])
            store.update_mission(mission_id, diff_json=early_diff.to_dict(), diff_digest=early_diff.digest)
            if not early_diff.available:
                self._block(mission_id, "NO_DIFF", early_diff.error or "Cannot capture the patch.", step_index=2)
                return
            if protocol and any(name not in scenario["allowed_paths"] for name in early_diff.changed_files):
                self._block(mission_id, "TASK_SCOPE", "The proposed patch exceeds its admitted paths; tests were not run.", step_index=2)
                return
            if protocol and any((Path(run["path"]) / name).is_symlink() or not (Path(run["path"]) / name).is_file() for name in early_diff.changed_files):
                self._block(mission_id, "PATCH_PROTOCOL", "A proposal must add regular test files.", step_index=2)
                return
            if not early_diff.files:
                final_line = next(
                    (line.strip() for line in reversed(result.result_text.splitlines()) if line.strip()),
                    "",
                )
                reason = (
                    f"{runtime_label} finished {result.num_turns or 0} turn(s) but produced no file change. "
                    "The patch step is blocked before tests, review and gate; an empty diff cannot be accepted."
                )
                if final_line:
                    reason += f" Agent final message: {final_line[:400]}"
                self._step(
                    mission_id,
                    2,
                    "forge",
                    "Patch",
                    f"NO_PATCH · {reason}",
                    "blocked",
                    cost=result.cost_usd,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    evidence_path=result.stdout_path,
                )
                emit(
                    store.add_event(
                        type="agent.no_patch",
                        actor="forge",
                        mission=mission_id,
                        fixture=scenario["fixture"],
                        message=reason,
                        severity="error",
                        payload={
                            "run_id": result.run_id,
                            "tokens_in": result.tokens_in,
                            "tokens_out": result.tokens_out,
                            "result_text": result.result_text[-2000:],
                        },
                    )
                )
                self._block(mission_id, "NO_PATCH", reason, step_index=2)
                return
            self._step(
                mission_id,
                2,
                "forge",
                "Patch",
                f"Agent completed in {result.num_turns or 0} turns · {len(early_diff.files)} file(s) changed · "
                f"${result.cost_usd:.4f} · {result.run_id}",
                "done",
                cost=result.cost_usd,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                evidence_path=result.stdout_path,
            )
            emit(
                store.add_event(
                    type="agent.completed",
                    actor="forge",
                    mission=mission_id,
                    fixture=scenario["fixture"],
                    message=f"Patch agent completed · {result.num_turns or 0} turns · {len(early_diff.files)} file(s) changed.",
                    severity="success",
                    payload={
                        "run_id": result.run_id,
                        "tokens_in": result.tokens_in,
                        "tokens_out": result.tokens_out,
                        "changed_files": early_diff.changed_files,
                    },
                )
            )

        self._verify_and_gate(
            mission_id, scenario, run=run, cost=cost, tokens_in=tokens_in, tokens_out=tokens_out,
            author_model=result.model if scenario["kind"] == "agent" else "", context=context,
        )

    def _verify_and_gate(
        self, mission_id: str, scenario: dict[str, Any], *, run: dict[str, Any],
        cost: float, tokens_in: int, tokens_out: int, author_model: str, context: str,
    ) -> None:
        contract = _loads(store.mission(mission_id).get("contract_json"), {})
        protocol = scenario.get("protocol")
        runtime_label = "OpenCode" if settings.agent_runtime == "opencode-cli" else "Claude Code CLI"
        # 3 · Tests ----------------------------------------------------------
        submitted_diff = sandbox.collect_changes(Path(run["path"]), base_commit=run["base_commit"])
        if not submitted_diff.available:
            self._block(mission_id, "NO_DIFF", submitted_diff.error or "Cannot capture the submitted patch.")
            return
        store.update_mission(mission_id, current_step="Tests", current_agent="probe", progress=0.58)
        self._step(mission_id, 3, "probe", "Tests", "pytest is running the contract suite.", "running")
        tests = sandbox.run_tests(Path(run["path"]), expected_executor=contract.get("test_executor"))
        store.update_mission(mission_id, tests_json=tests.to_dict(), progress=0.68)
        if "test_executor" in contract and tests.executor != contract["test_executor"]:
            self._block(mission_id, "TEST_EXECUTOR_CHANGED", "The executed test identity differs from admission; no review or acceptance issued.")
            return
        self._step(
            mission_id,
            3,
            "probe",
            "Tests",
            tests.summary(),
            "done" if tests.available and not tests.failed and not tests.errors else "failed",
            evidence_path=tests.junit_path,
        )
        emit(
            store.add_event(
                type="tests.completed",
                actor="probe",
                mission=mission_id,
                fixture=scenario["fixture"],
                message=tests.summary(),
                severity="success" if tests.available and not tests.failed and not tests.errors else "error",
                payload={
                    "total": tests.total,
                    "passed": tests.passed,
                    "failed": tests.failed,
                    "errors": tests.errors,
                },
            )
        )

        # Diff ---------------------------------------------------------------
        diff = sandbox.collect_changes(Path(run["path"]), base_commit=run["base_commit"])
        store.update_mission(
            mission_id,
            diff_json=diff.to_dict(),
            diff_digest=diff.digest,
            progress=0.74,
        )
        if not diff.available or diff.digest != submitted_diff.digest:
            self._block(mission_id, "TESTS_CHANGED_PATCH", "The checkout changed while tests ran. Test results are not bound to the final patch.")
            return
        emit(
            store.add_event(
                type="diff.captured",
                actor="probe",
                mission=mission_id,
                fixture=scenario["fixture"],
                message=f"Diff {diff.digest[:12] if diff.digest else '—'} · {len(diff.files)} file(s) · +{diff.lines_added}/-{diff.lines_removed}.",
                severity="info",
                payload={"changed_files": diff.changed_files},
            )
        )

        # 4 · Review ---------------------------------------------------------
        review_verdict = None
        review_independent = False
        tests_complete = (
            tests.available and not tests.error and tests.exit_code == 0 and tests.total > 0
            and tests.passed == tests.total and not (tests.failed or tests.errors or tests.skipped)
        )
        if scenario["kind"] == "agent" and tests_complete:
            store.update_mission(mission_id, current_step="Review", current_agent="sentinel", progress=0.78)
            self._step(
                mission_id,
                4,
                "sentinel",
                "Review",
                f"{runtime_label} is reviewing the staged diff.",
                "running",
            )
            review = self._call_agent(
                mission_id=mission_id,
                agent_id="sentinel",
                role="an independent reviewer",
                title=scenario["title"],
                scenario=scenario["kind"],
                risk=scenario["risk"],
                task=f"{scenario['task']}\nWorker tests: {tests.summary()}. Allowed task paths: {scenario.get('allowed_paths', ['app/**', 'tests/**'])}.",
                cwd=Path(run["path"]),
                memory_context="none",
                graph_context="none",
                review=True,
                diff_text=diff.patch,
                protocol=protocol,
                source_context=context,
                route_author_model=author_model,
            )
            forge_model = author_model
            review_model = review.model
            independent = bool(forge_model and review_model and model_family(forge_model) != model_family(review_model))
            independence_label = (
                f"different CLI-selected model families ({model_family(forge_model)} vs {model_family(review_model)}) · not independent providers"
                if independent else "same or unidentified model family"
            )
            measured = store.mission(mission_id)
            cost, tokens_in, tokens_out = measured["cost_usd"], measured["tokens_in"], measured["tokens_out"]
            if review.raw.get("block_code"):
                self._block(mission_id, review.raw["block_code"], review.error or "Model routing blocked")
                return
            if review.status == "completed":
                review_verdict = review.verdict or "UNPARSED"
                review_independent = independent
                self._step(
                    mission_id,
                    4,
                    "sentinel",
                    "Review",
                    f"{review_verdict} · {review.reason or 'no reason'} · {independence_label}",
                    "done" if review_verdict == "OK" else "failed",
                    cost=review.cost_usd,
                    tokens_in=review.tokens_in,
                    tokens_out=review.tokens_out,
                    evidence_path=review.stdout_path,
                )
                emit(
                    store.add_event(
                        type="review.completed",
                        actor="sentinel",
                        mission=mission_id,
                        fixture=scenario["fixture"],
                        message=f"Review {review_verdict}: {review.reason or 'no reason'} ({independence_label}).",
                        severity="success" if review_verdict == "OK" else "warning",
                        payload={"run_id": review.run_id, "independent": review_independent},
                    )
                )
            else:
                if not contract.get("routing"):
                    self._provider_limit(review.error or "")
                self._step(
                    mission_id,
                    4,
                    "sentinel",
                    "Review",
                    review.error or "Review failed",
                    "blocked",
                    cost=review.cost_usd,
                    tokens_in=review.tokens_in,
                    tokens_out=review.tokens_out,
                )
                emit(
                    store.add_event(
                        type="review.blocked",
                        actor="sentinel",
                        mission=mission_id,
                        fixture=scenario["fixture"],
                        message=review.error or "Review failed",
                        severity="error",
                    )
                )
        else:
            self._step(
                mission_id,
                4,
                "sentinel",
                "Review",
                "Skipped: worker tests did not pass completely." if scenario["kind"] == "agent" else
                "Not run: local contribution; human review required." if scenario["kind"] == "local-patch" else
                "Skipped: operator drill, not an agent patch.",
                "skipped",
            )

        # 5 · Gate -----------------------------------------------------------
        reviewed_diff = sandbox.collect_changes(Path(run["path"]), base_commit=run["base_commit"])
        if not reviewed_diff.available or reviewed_diff.digest != diff.digest:
            store.update_mission(mission_id, diff_json=reviewed_diff.to_dict(), diff_digest=reviewed_diff.digest)
            self._block(mission_id, "REVIEW_CHANGED_PATCH", "The checkout changed after testing, during review. No acceptance is issued for an untested patch.")
            return
        if sandbox.policy_digest() != contract.get("policy_digest"):
            self._block(mission_id, "CONTRACT_CHANGED", "The trusted policy changed during execution; no acceptance is issued.")
            return
        if "test_executor" in contract and contract["test_executor"] != sandbox.executor_contract():
            self._block(mission_id, "TEST_EXECUTOR_CHANGED", "The canonical test executor changed after testing; no acceptance issued.")
            return
        store.update_mission(mission_id, current_step="Gate", current_agent="gate", progress=0.82)
        policy = PolicyEngine(sandbox.read_policy())
        decision = policy.evaluate(
            diff=diff,
            tests=tests,
            risk=scenario["risk"],
            cost_usd=cost,
            review_verdict=review_verdict if scenario["kind"] == "agent" else None,
            review_independent=review_independent if scenario["kind"] == "agent" else False,
            allowed_paths=scenario.get("allowed_paths"),
            minimum_tests=scenario.get("minimum_tests", 1),
        )
        store.update_mission(
            mission_id,
            gate_decision=decision.decision,
            gate_reason=decision.reason,
            gate_reason_code=decision.reason_code,
            gate_json=decision.to_dict(),
            progress=0.88,
        )
        if contract.get("routing"):
            router = ModelRouter(store, contract["routing"])
            for role in ("forge", "sentinel"):
                analysis = router.analyze(scenario, role)
                emit(store.add_event(type="model.feedback", actor="engine", mission=mission_id,
                                    message=f"Analyzed actual {role} sessions and deterministic outcomes.", payload=analysis))
        self._step(
            mission_id,
            5,
            "gate",
            "Gate",
            f"{decision.decision.upper()} · {decision.reason_code} · {decision.reason}",
            "done" if decision.decision in {"allow", "checkpoint"} else "failed",
        )
        emit(
            store.add_event(
                type="gate.decided",
                actor="gate",
                mission=mission_id,
                fixture=scenario["fixture"],
                message=f"{decision.decision.upper()} ({decision.reason_code}) · {decision.reason}",
                severity="success" if decision.decision == "allow" else "warning" if decision.decision == "checkpoint" else "error",
                payload=decision.to_dict(),
            )
        )

        if decision.decision == "deny":
            self._finish_blocked(mission_id, decision.reason, decision.reason_code)
            return

        if decision.decision == "checkpoint":
            store.update_mission(
                mission_id,
                status="awaiting_human",
                current_step="Owner approval",
                current_agent="gate",
                progress=0.9,
            )
            self._step(
                mission_id,
                6,
                "gate",
                "Owner approval",
                "Operator must approve the checkpoint before the packet is sealed.",
                "running",
            )
            packet = self._write_evidence(mission_id, sealed=False)
            self._update_issue(
                mission_id,
                status="in_review",
                comment=(
                    f"Gate checkpoint: {decision.reason}\n"
                    f"Packet draft: {packet['digest']}\n"
                    "Operator approval is required before completion."
                ),
            )
            emit(
                store.add_event(
                    type="mission.awaiting_human",
                    actor="gate",
                    mission=mission_id,
                    fixture=scenario["fixture"],
                    message=f"Checkpoint waiting for the operator · packet {packet['digest']}.",
                    severity="warning",
                )
            )
            return

        # allow
        self._seal(mission_id, outcome="autonomous")

    # -------------------------------------------------------------------- drills
    @staticmethod
    def _provider_limit(error: str) -> bool:
        if any(phrase in error.lower() for phrase in ("free usage limit reached", "insufficient account funds", "quota exhausted")):
            store.set_kv("provider_retry_at", str(time.time() + PROVIDER_COOLDOWN_SECONDS))
            return True
        return False

    @staticmethod
    def _apply_drill(path: Path, kind: str) -> str:
        pricing = path / "app" / "pricing.py"
        text = pricing.read_text()
        fixed = text.replace(
            "kilograms = grams // 1000",
            "kilograms = (grams + 999) // 1000",
        )
        pricing.write_text(fixed)
        if kind == "drill-baseline":
            pricing.write_text(text)  # no patch: the baseline stays defective
            return "No patch applied · baseline defect left in place."
        policy = path / "policy" / "policy.yaml"
        policy_text = policy.read_text()
        policy.write_text(policy_text.replace("max_diff_lines: 200", "max_diff_lines: 2000"))
        return (
            "Drill patch applied by the operator harness: pricing repaired and "
            "policy/policy.yaml widened to 2000 diff lines."
        )

    # -------------------------------------------------------------------- finish
    def _update_issue(self, mission_id: str, *, status: str, comment: str) -> None:
        mission = store.mission(mission_id)
        if not mission or not mission.get("paperclip_issue_id"):
            return
        result = paperclip.update_issue(mission["paperclip_issue_id"], status=status, comment=comment)
        if not result.get("ok"):
            emit(
                store.add_event(
                    type="paperclip.update.failed",
                    actor="paperclip",
                    mission=mission_id,
                    fixture=mission.get("fixture"),
                    message=f"Paperclip update failed: {result.get('error')}",
                    severity="warning",
                )
            )

    def _block(self, mission_id: str, code: str, reason: str, *, step_index: int | None = None) -> None:
        mission = store.mission(mission_id)
        if not mission or mission["status"] == "completed":
            return
        store.update_mission(
            mission_id,
            status="blocked",
            outcome="unknown",
            ended_at=_now(),
            progress=0.0,
            blocked_reason=reason,
            gate_decision="deny",
            gate_reason=reason,
            gate_reason_code=code,
            gate_json={"decision": "deny", "reason_code": code, "reason": reason, "checks": []},
        )
        if step_index is not None:
            steps = store.steps(mission_id)
            for step in steps:
                if step["idx"] == step_index and step["status"] not in {"done", "failed", "blocked"}:
                    store.update_step(step["id"], status="blocked", detail=reason, ended_at=_now())
        packet = self._write_evidence(mission_id, sealed=False)
        self._update_issue(
            mission_id,
            status="blocked",
            comment=f"Mission blocked ({code}): {reason}\nDraft packet: {packet['digest']}",
        )
        emit(
            store.add_event(
                type="mission.blocked",
                actor="gate",
                mission=mission_id,
                fixture=mission.get("fixture"),
                message=f"{code} · {reason}",
                severity="error",
            )
        )

    def _finish_blocked(self, mission_id: str, reason: str, code: str) -> None:
        mission = store.mission(mission_id)
        if not mission or mission["status"] == "completed":
            return
        store.update_mission(
            mission_id,
            status="blocked",
            outcome="failed",
            ended_at=_now(),
            progress=0.0,
            blocked_reason=reason,
        )
        packet = self._write_evidence(mission_id, sealed=False)
        self._update_issue(
            mission_id,
            status="blocked",
            comment=f"Gate denied ({code}): {reason}\nDraft packet: {packet['digest']}",
        )
        emit(
            store.add_event(
                type="mission.blocked",
                actor="gate",
                mission=mission_id,
                fixture=mission.get("fixture"),
                message=f"{code} · {reason}",
                severity="error",
            )
        )

    def _seal(self, mission_id: str, *, outcome: str) -> None:
        mission = store.mission(mission_id)
        if not mission or mission["status"] == "completed":
            return
        if store.get_kv(f"seal:{mission_id}") is None:
            gate = _loads(mission.get("gate_json"), {})
            if mission["status"] != "running" or not (
                outcome == "autonomous" and gate.get("decision") == "allow"
                or outcome == "assisted" and gate.get("decision") == "approved" and mission.get("human_approval") == "operator"
            ):
                raise ValueError("A seal requires a recorded Gate acceptance or human approval")
            final = {**mission, "status": "completed", "outcome": outcome, "ended_at": _now()}
            packet = self._build_evidence(final, sealed=True)
            store.prepare_seal(packet)
        self._resume_seal(mission_id)

    def _resume_seal(self, mission_id: str) -> None:
        packet = store.pending_seal(mission_id)
        if not packet:
            raise ValueError("No prepared seal")
        # Publish first. A crash can leave a prepared seal, never false completion.
        evidence_writer.publish(packet)
        if not store.complete_mission(packet):
            return
        mission = store.mission(mission_id)
        outcome = packet["payload"]["mission"]["outcome"]
        self._update_issue(
            mission_id,
            status="done",
            comment=f"Mission completed ({outcome}). Sealed packet {packet['digest']}.",
        )
        emit(
            store.add_event(
                type="evidence.sealed",
                actor="gate",
                mission=mission_id,
                fixture=mission.get("fixture"),
                message=f"Packet {packet['digest']} sealed · outcome {outcome}.",
                severity="success",
                payload={"packet": packet["id"], "digest": packet["digest"]},
            )
        )
        if _loads(mission.get("contract_json"), {}).get("scenario", {}).get("delivery") == "local":
            self._deliver_local(mission_id)

    def _deliver_local(self, mission_id: str) -> None:
        try:
            receipt = LocalDelivery(store, sandbox).deliver(mission_id)
            store.set_kv(f"delivery_error:{mission_id}", "")
            emit(store.add_event(type="delivery.verified", actor="gate", mission=mission_id, severity="success",
                                 message=f"Verified local product commit {receipt['commit']}.", payload=receipt))
        except Exception as error:
            reason = f"{type(error).__name__}: {error}"
            store.set_kv(f"delivery_error:{mission_id}", reason)
            emit(store.add_event(type="delivery.blocked", actor="gate", mission=mission_id, severity="error", message=reason))

    def _execution_failed(self, mission_id: str, error: Exception) -> None:
        mission = store.mission(mission_id)
        reason = f"{type(error).__name__}: {error}"
        if mission and mission["status"] == "completed":
            # Board / event projections cannot revoke an already committed acceptance.
            emit(store.add_event(type="mission.projection_failed", actor="engine", mission=mission_id, severity="error", message=reason))
        elif store.get_kv(f"seal:{mission_id}") is not None:
            store.update_mission(mission_id, current_step="Seal pending", blocked_reason=reason)
            emit(store.add_event(
                type="evidence.seal_pending", actor="engine", mission=mission_id, severity="error",
                message=f"{reason}. Acceptance intent preserved; restart retries only local publication, without model calls or new approval.",
            ))
        else:
            self._block(mission_id, "EXECUTION_FAILED", reason)

    def retry_review(self, mission_id: str) -> dict[str, Any]:
        """Retry one technical format failure on low-risk, test-only work."""
        with self._lock, store.mission_lock(mission_id):
            mission = store.mission(mission_id)
            if not mission:
                raise ValueError("Unknown mission")
            if store.get_kv(f"review_retry:{mission_id}"):
                if mission["status"] in {"queued", "running", "completed"}:
                    return mission
                raise ValueError("The single review retry was already consumed")
            contract = _loads(mission.get("contract_json"), {})
            scenario = contract.get("scenario", {})
            runs = store.runs(mission_id)
            reviews = [run for run in runs if run["agent"] == "sentinel"]
            authors = [run for run in runs if run["agent"] == "forge" and run["status"] == "completed"]
            # Free availability refusals are retained attempts, not completed reviews.
            # They must not consume the one technical-format retry or hide a semantic rejection.
            substantive = [run for run in reviews if not (
                run["status"] != "completed" and availability_error(run.get("error"))
            )]
            failed = substantive[0] if len(substantive) == 1 and reviews[0] == substantive[0] else {}
            metadata = _loads(failed.get("raw_json"), {})
            if not (
                mission["status"] == "awaiting_human" and mission["human_approval"] is None
                and scenario.get("kind") == "agent" and scenario.get("risk") == "low"
                and scenario.get("source") == "local-product" and scenario.get("protocol") == PATCH_PROTOCOL
                and scenario.get("allowed_paths") == [scenario.get("new_test_file")]
                and failed.get("status") == "failed"
                and (failed.get("error") or "").startswith("Invalid patch protocol response:")
                and metadata.get("finish_reason") == "stop" and metadata.get("steps") == 1
                and metadata.get("tool_calls") == 0 and len(authors) == 1
            ):
                raise ValueError("Only one technical-format review failure on low-risk test-only work is eligible")
            if contract.get("runtime") != self._runtime_contract() or contract.get("policy_digest") != sandbox.policy_digest():
                raise ValueError("The admitted runtime or trusted policy changed")
            context = source_context(Path(mission["sandbox_path"]))
            if _loads(authors[0].get("raw_json"), {}).get("source_digest") != _sha(context):
                raise ValueError("The source context differs from the completed author run")
            diff = sandbox.collect_changes(Path(mission["sandbox_path"]), base_commit=mission["base_commit"])
            if not diff.available or diff.digest != mission["diff_digest"]:
                raise ValueError("The tested patch changed after checkpoint")
            intent = {"contract_json": mission["contract_json"], "diff_digest": diff.digest,
                      "author_run_id": authors[0]["run_id"], "author_model": authors[0]["model"],
                      "source_digest": _sha(context), "failed_review_run_id": failed["run_id"]}
            if store.requeue_review(mission_id, intent):
                self._write_evidence(mission_id, sealed=False)
                emit(store.add_event(type="review.retry_queued", actor="engine", mission=mission_id,
                                    message="One technical review-format retry queued; no operator approval recorded.",
                                    payload={"failed_run": failed["run_id"]}))
                self._wake.set()
            return store.mission(mission_id)

    def _resume_review(self, mission_id: str, scenario: dict[str, Any]) -> None:
        mission = store.mission(mission_id)
        intent = _loads(store.get_kv(f"review_retry:{mission_id}"), {})
        context = source_context(Path(mission["sandbox_path"]))
        diff = sandbox.collect_changes(Path(mission["sandbox_path"]), base_commit=mission["base_commit"])
        author = next((run for run in store.runs(mission_id) if run["run_id"] == intent.get("author_run_id")), {})
        if (
            intent.get("contract_json") != mission["contract_json"] or not diff.available
            or diff.digest != intent.get("diff_digest") or diff.digest != mission["diff_digest"]
            or _sha(context) != intent.get("source_digest") or author.get("status") != "completed"
            or author.get("model") != intent.get("author_model")
        ):
            self._block(mission_id, "REVIEW_RETRY_CHANGED", "The persisted review-retry inputs changed; no model call was made.")
            return
        self._step(mission_id, 6, "gate", "Owner approval", "Technical review resumed; no operator approval was recorded.", "skipped")
        self._verify_and_gate(
            mission_id, scenario, run={"path": mission["sandbox_path"], "base_commit": mission["base_commit"]},
            cost=mission["cost_usd"], tokens_in=mission["tokens_in"], tokens_out=mission["tokens_out"],
            author_model=intent["author_model"], context=context,
        )

    def approve(self, mission_id: str) -> bool:
        with self._lock:
            try:
                with store.mission_lock(mission_id):
                    return self._approve(mission_id)
            except (BlockingIOError, ValueError):
                return False

    def _approve(self, mission_id: str) -> bool:
        mission = store.mission(mission_id)
        if not mission or mission["status"] != "awaiting_human":
            return False
        packet = store.evidence_for(mission_id)
        body = ((packet or {}).get("payload") or {}).get("payload") or {}
        diff = sandbox.collect_changes(Path(mission["sandbox_path"]), base_commit=mission["base_commit"])
        if (
            not diff.available or diff.digest != mission.get("diff_digest")
            or sandbox.policy_digest() != body.get("contract", {}).get("policy_digest")
            or "test_executor" in body.get("contract", {})
            and body["contract"]["test_executor"] != sandbox.executor_contract()
        ):
            self._block(mission_id, "CHECKPOINT_CHANGED", "The patch, trusted policy or test executor changed after verification. Run a new mission.")
            return False
        gate = _loads(mission.get("gate_json"), {})
        gate.update(decision="approved", reason_code="HUMAN_APPROVED", reason="Operator approved the verified checkpoint.", requires_human=False)
        store.update_mission(
            mission_id,
            human_approval="operator",
            status="running",
            current_step="Seal",
            current_agent="gate",
            progress=0.95,
            gate_json=gate,
            gate_decision="approved",
            gate_reason_code="HUMAN_APPROVED",
            gate_reason=gate["reason"],
        )
        emit(
            store.add_event(
                type="gate.approved",
                actor="operator",
                mission=mission_id,
                fixture=mission.get("fixture"),
                message="Operator approved the checkpoint. Authority recorded in the ledger.",
                severity="success",
            )
        )
        try:
            self._seal(mission_id, outcome="assisted")
        except Exception as error:
            self._execution_failed(mission_id, error)
        return True

    # ------------------------------------------------------------------ evidence
    def _write_evidence(self, mission_id: str, *, sealed: bool) -> dict[str, Any]:
        mission = store.mission(mission_id)
        if not mission:
            raise RuntimeError(f"Unknown mission {mission_id}")
        packet = evidence_writer.publish(self._build_evidence(mission, sealed=sealed))
        store.add_evidence(packet)
        return packet

    def _build_evidence(self, mission: dict[str, Any], *, sealed: bool) -> dict[str, Any]:
        mission_id = mission["id"]
        diff = _loads(mission.get("diff_json"), {})
        tests = _loads(mission.get("tests_json"), {})
        gate = _loads(mission.get("gate_json"), {})
        runs = store.runs(mission_id)
        recall = next(
            (item for item in memory_store.recent_recalls(100) if item.get("mission") == mission_id),
            {"items": [], "query": "", "latency_ms": 0.0},
        )
        graph = graph_projection.stats()
        control = _loads(store.get_kv("croa_control"), {})
        return evidence_writer.build(
            mission=mission,
            diff=diff,
            tests=tests,
            gate=gate,
            runs=runs,
            memory={
                "items": recall.get("items", []),
                "query": recall.get("query"),
                "latency_ms": recall.get("latency_ms"),
                "digest": _sha(json.dumps(recall.get("items", []), sort_keys=True))[:16],
            },
            graph={
                "provider": graph.get("provider"),
                "digest": graph.get("digest"),
                "files": graph.get("files"),
                "nodes_total": graph.get("nodes"),
                "edges": graph.get("edges"),
            },
            policy_digest=_loads(mission.get("contract_json"), {}).get("policy_digest") or sandbox.policy_digest(),
            control_audit=control,
            sealed=sealed,
        )

    # ------------------------------------------------------------------- checks
    def sandbox_check(self) -> dict[str, Any]:
        result = self.baseline_check(force=True)
        emit(
            store.add_event(
                type="sandbox.checked",
                actor="probe",
                message=f"Sandbox baseline: {result.get('summary')}",
                severity="success" if result.get("ok") else "error",
                payload={"tests": result.get("tests", {})},
            )
        )
        return result

    def ops_integrity(self) -> dict[str, Any]:
        result = self.ops_check(force=True)
        emit(
            store.add_event(
                type="ops.integrity.checked",
                actor="ops",
                message=f"OPS integrity: {result.get('status', result.get('error', 'unknown'))} · {result.get('files_verified', 0)} files.",
                severity="success" if result.get("status") == "integrity_verified" else "warning",
                payload=result,
            )
        )
        return result

    def croa_audit(self) -> dict[str, Any]:
        result = self.croa_check(force=True)
        emit(
            store.add_event(
                type="croa.audit.checked",
                actor="croa",
                message=f"CROA packet valid={result.get('packet_valid')} · outcome {result.get('outcome')} · no conformance claim.",
                severity="success" if result.get("packet_valid") else "warning",
                payload=result,
            )
        )
        return result

    # ------------------------------------------------------------------ snapshot
    def _shape_steps(self, mission_id: str) -> list[dict[str, Any]]:
        output = []
        for step in store.steps(mission_id):
            output.append(
                {
                    "index": step["idx"],
                    "agent": step["agent"],
                    "label": step["label"],
                    "detail": step["detail"],
                    "status": step["status"],
                    "progress": step["progress"],
                    "started_at": step["started_at"],
                    "ended_at": step["ended_at"],
                    "cost": step["cost_usd"],
                    "tokens_in": step["tokens_in"],
                    "tokens_out": step["tokens_out"],
                }
            )
        return output

    def _shape_mission(self, mission: dict[str, Any]) -> dict[str, Any]:
        steps = self._shape_steps(mission["id"])
        elapsed = 0.0
        if mission.get("started_at"):
            try:
                start = datetime.fromisoformat(mission["started_at"])
                end = datetime.fromisoformat(mission["ended_at"]) if mission.get("ended_at") else datetime.now(timezone.utc)
                elapsed = max(0.0, (end - start).total_seconds())
            except ValueError:
                elapsed = 0.0
        return {
            "id": mission["id"],
            "fixture": mission["fixture"],
            "scenario": mission["scenario"],
            "title": mission["title"],
            "subtitle": mission["subtitle"],
            "risk": mission["risk"],
            "priority": mission["priority"],
            "profile": mission["profile"],
            "status": mission["status"],
            "outcome": mission["outcome"],
            "progress": mission["progress"],
            "current_step": mission["current_step"],
            "current_agent": mission["current_agent"],
            "steps": steps,
            "cost": mission["cost_usd"],
            "tokens_in": mission["tokens_in"],
            "tokens_out": mission["tokens_out"],
            "gate_decision": mission["gate_decision"],
            "gate_reason": mission["gate_reason"],
            "gate_reason_code": mission["gate_reason_code"],
            "human_approval": mission["human_approval"],
            "created_at": mission["created_at"],
            "started_at": mission["started_at"],
            "ended_at": mission["ended_at"],
            "elapsed_seconds": round(elapsed, 2),
            "blocked_reason": mission["blocked_reason"],
            "paperclip_issue_id": mission["paperclip_issue_id"],
            "paperclip_identifier": mission["paperclip_identifier"],
            "paperclip_url": mission["paperclip_url"],
        }

    def _shape_agents(self, missions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        probe = self.agent_probe()
        ready = bool(probe.get("ready"))
        if settings.agent_runtime == "opencode-cli":
            model = str(probe.get("model") or settings.opencode_model)
            family = f"OpenCode · {model_family(model)}" if ready else "OpenCode · no runtime"
            basis = str(probe.get("auth") or "unavailable") if ready else "unavailable"
        else:
            model = settings.agent_model or ("Claude Code CLI" if ready else "unconfigured")
            family = "Claude Code CLI" if ready else "no runtime"
            basis = "subscription" if ready and not settings.anthropic_api_key else "API" if ready else "unavailable"
        output = []
        for agent in AGENTS:
            llm = agent["id"] in {"forge", "sentinel"}
            selected_model = settings.opencode_review_model if agent["id"] == "sentinel" and settings.agent_runtime == "opencode-cli" else model
            local_model = {"atlas": "Python · task contract", "scout": "SQLite FTS5 + Graphify", "probe": "pytest", "gate": "policy engine"}.get(agent["id"], "local")
            active = next(
                (
                    mission
                    for mission in missions
                    if mission["status"] in {"running", "awaiting_human"}
                    and mission.get("current_agent") == agent["id"]
                ),
                None,
            )
            tasks = len([step for mission in missions for step in mission["steps"] if step["agent"] == agent["id"]])
            tokens = sum(
                step["tokens_in"] + step["tokens_out"]
                for mission in missions
                for step in mission["steps"]
                if step["agent"] == agent["id"]
            )
            cost = sum(
                step["cost"] for mission in missions for step in mission["steps"] if step["agent"] == agent["id"]
            )
            status = "idle"
            action = "Standing by"
            progress = 0.0
            mission_id = None
            fixture = None
            if active:
                status = "working" if active["status"] == "running" else "waiting"
                action = active["current_step"]
                progress = active["progress"]
                mission_id = active["id"]
                fixture = active["fixture"]
            output.append(
                {
                    "id": agent["id"],
                    "name": agent["name"],
                    "role": agent["role"],
                    "model": selected_model if llm else local_model,
                    "effort": agent["effort"],
                    "family": f"OpenCode · {model_family(selected_model)}" if llm and settings.agent_runtime == "opencode-cli" else family if llm else "deterministic",
                    "basis": basis if llm else "local",
                    "color": agent["color"],
                    "initials": agent["initials"],
                    "status": status,
                    "action": action,
                    "progress": progress,
                    "mission": mission_id,
                    "fixture": fixture,
                    "tasks": tasks,
                    "tokens": tokens,
                    "cost": round(cost, 6),
                }
            )
        return output

    def _shape_memory(self, graph: dict[str, Any]) -> dict[str, Any]:
        stats = memory_store.stats()
        recalls = []
        for recall in memory_store.recent_recalls(8):
            mission = store.mission(recall["mission"]) if recall["mission"] not in {None, "manual"} else None
            packet = store.evidence_for(recall["mission"]) if mission else None
            recorded_graph = (((packet or {}).get("payload") or {}).get("payload") or {}).get("graph") or {}
            recalls.append(
                {
                    "fixture": mission["fixture"] if mission else "manual",
                    "mission": recall["mission"],
                    "state": "hit" if recall["hit"] else "miss",
                    "query": recall["query"],
                    "nodes": recorded_graph.get("nodes"),
                    "items": recall["items"],
                    "latency_ms": recall["latency_ms"],
                    "status": "complete",
                    "digest": recorded_graph.get("digest"),
                }
            )
        return {
            "mode": "SQLite FTS5 · local",
            "note": "Advisory context only. Memory never grants authority or approves a gate.",
            "items": memory_store.items(12),
            "stats": stats,
            "recalls": recalls,
        }

    def _shape_graph(self) -> dict[str, Any]:
        view = graph_projection.view()
        stats = graph_projection.stats()
        memory_stats = memory_store.stats()
        view["symbols"] = stats.get("symbols", 0)
        view["memory_items"] = memory_stats["total"]
        view["memory_used"] = memory_stats["used_items"]
        view["digest"] = stats.get("digest")
        return view

    def _shape_evidence(self) -> list[dict[str, Any]]:
        output = []
        for packet in store.evidence(30):
            output.append(
                {
                    "id": packet["id"],
                    "mission": packet["mission"],
                    "scope": packet["scope"],
                    "digest": packet["digest"],
                    "checks": packet["checks"],
                    "review": packet["review"],
                    "status": packet["status"],
                    "created_at": packet["created_at"],
                    "path": packet["path"],
                }
            )
        return output

    def _shape_client(self, missions: list[dict[str, Any]]) -> dict[str, Any]:
        mission = next((item for item in missions if item["fixture"] == "PD01"), None)
        if not mission:
            return {
                "mission_id": None,
                "fixture": "PD01",
                "title": "No pricing repair mission yet",
                "status": "idle",
                "progress": 0.0,
                "outcome": None,
                "checks": [],
                "agents": [],
            }
        checks = [
            {
                "label": step["label"],
                "status": "done" if step["status"] == "done" else "running" if step["status"] in {"running", "pending"} else "blocked",
                "agent": step["agent"],
                "detail": step["detail"],
            }
            for step in mission["steps"]
        ]
        agents = [
            {"name": agent["name"], "color": agent["color"], "status": agent["status"]}
            for agent in self._shape_agents(missions)
            if agent["status"] != "idle"
        ]
        return {
            "mission_id": mission["id"],
            "fixture": mission["fixture"],
            "title": mission["title"],
            "status": mission["status"],
            "progress": mission["progress"],
            "outcome": mission["outcome"],
            "checks": checks,
            "agents": agents,
        }

    def _policy(self, missions: list[dict[str, Any]]) -> dict[str, Any]:
        probe = self.agent_probe()
        if settings.agent_runtime == "opencode-cli":
            model = str(probe.get("model") or settings.opencode_model)
            if not probe.get("ready"):
                model += " · blocked"
        else:
            model = settings.agent_model or ("Claude Code CLI" if probe.get("ready") else "Claude Code CLI · blocked")
        return {
            "id": "parceldesk-sandbox-policy/v1",
            "mode": "enforced",
            "quality_floor": "No sealed packet, no completion.",
            "budget": f"${settings.budget_usd:.2f} local envelope",
            "rules": [
                "Unknown, missing or failed evidence means NO.",
                "Protected paths are denied before any other gate result.",
                "Tests must run and pass; a claim without a JUnit result is not evidence.",
                "A memory item, graph edge or score cannot grant authority.",
                "Same-family review cannot establish independence; a human checkpoint is required.",
                "A checked artifact is not an accepted task outcome.",
            ],
            "profiles": [
                {"id": "P-agent", "model": model, "effort": "high", "class": "agent patch"},
                {"id": "P-tests", "model": "pytest", "effort": "deterministic", "class": "local verification"},
                {"id": "P-gate", "model": "policy engine", "effort": "deterministic", "class": "authority"},
                {"id": "P-human", "model": "operator", "effort": "—", "class": "human checkpoint"},
            ],
        }

    def _runtime(self) -> dict[str, Any]:
        probe = self.agent_probe()
        graph = graph_projection.stats()
        mem = memory_store.stats()
        baseline = self._baseline
        ops = self._ops
        croa = self._croa
        health = paperclip.health()
        tests = (baseline or {}).get("tests", {})
        components = {
            "graphify": {
                "name": "Graphify AST",
                "status": "ready" if graph.get("available") else "unavailable",
                "detail": f"{graph.get('nodes', 0)} nodes · {graph.get('edges', 0)} edges · {graph.get('digest', '—')}",
            },
            "memory": {
                "name": "SQLite FTS5",
                "status": "ready",
                "detail": f"{mem['total']} items · {mem['verified']} verified · {mem['stale']} stale",
            },
            "paperclip": {
                "name": "Paperclip",
                "status": "ready" if health.get("available") else "unavailable",
                "detail": (
                    f"{health.get('version', '—')} · {health.get('deployment_mode', '—')}"
                    if health.get("available")
                    else health.get("error", "unreachable")
                ),
            },
            "agent": {
                "name": "OpenCode agent" if settings.agent_runtime == "opencode-cli" else "Claude agent",
                "status": "ready" if probe.get("ready") else "blocked",
                "detail": (
                    f"{probe.get('model') or settings.opencode_model} · {probe.get('auth')} · {probe.get('version', '')}".strip(" ·")
                    if probe.get("ready")
                    else probe.get("reason") or f"{settings.agent_runtime} unavailable"
                ),
            },
            "sandbox": {
                "name": "Sandbox + pytest",
                "status": "ready" if baseline else "checking",
                "detail": (
                    f"{tests.get('total', 0)} tests · {tests.get('failed', 0)} failing baseline"
                    if baseline
                    else "baseline check running"
                ),
            },
            "ops": {
                "name": "OPS integrity",
                "status": "ready" if ops and ops.get("status") == "integrity_verified" else "partial" if ops else "checking",
                "detail": (
                    f"{ops.get('status')} · {ops.get('files_verified', 0)} files"
                    if ops
                    else "checker running"
                ),
            },
            "croa": {
                "name": "CROA CLI",
                "status": "ready" if croa and croa.get("packet_valid") else "partial" if croa else "checking",
                "detail": (
                    f"packet valid · outcome {croa.get('outcome')} · no conformance claim"
                    if croa
                    else "audit running"
                ),
            },
        }
        locations = {
            "graphify": ("On-demand CLI", "runtime/graphify/graphify-out/graph.json"),
            "memory": ("SQLite file + FTS5", "runtime/aeion.db"),
            "paperclip": ("Node + embedded PostgreSQL", settings.paperclip_base_url),
            "agent": ("CLI → external model provider", "runtime/agent-runs/"),
            "sandbox": ("Local git checkout + isolated canonical pytest", f"runtime/sandbox/runs/ · {settings.test_backend} test boundary; native parent"),
            "ops": ("On-demand Python checker", "Æion_AgenticOPS · integrity only"),
            "croa": ("On-demand Python audit", "runtime/croa/ · offline audit kit"),
        }
        for key, (execution, location) in locations.items():
            components[key].update(execution=execution, location=location)
        latest_runs = store.runs(limit=1)
        if latest_runs and latest_runs[0].get("status") in {"failed", "blocked"}:
            latest = latest_runs[0]
            failed_mission = store.mission(latest["mission_id"]) or {}
            components["agent"]["status"] = "blocked"
            retry_at = float(store.get_kv("provider_retry_at", "0"))
            retry = (
                "provider cooldown until " + datetime.fromtimestamp(retry_at, timezone.utc).isoformat(timespec="seconds")
                if retry_at > time.time() else "next bounded attempt permitted; quota unverified"
            )
            components["agent"]["detail"] = f"Last call blocked ({retry}): " + str(failed_mission.get("blocked_reason") or latest.get("error") or "unknown failure")
        components["ledger"] = {
            "name": "Mission ledger", "status": "ready", "detail": "Persistent missions, checks and evidence",
            "execution": "SQLite file", "location": "runtime/aeion.db",
        }
        return components

    def _frameworks(self) -> list[dict[str, Any]]:
        ops = self._ops or {}
        croa = self._croa or {}
        health = paperclip.health()
        return [
            {
                "id": "factory",
                "name": "Æion Factory",
                "role": "Task contract, review rules and acceptance checks.",
                "used": "Consumed AGENTS.md and the review/acceptance contract. No Factory runner was executed.",
                "status": "referenced",
                "color": "#a3e635",
                "path": "../Æion_Factory",
            },
            {
                "id": "ops",
                "name": "Æion Agentic OPS",
                "role": "Deny precedence, protected paths and the integrity checker.",
                "used": f"check_security_audit_upstream.py --integrity-only · {ops.get('status', 'not run')} · {ops.get('files_verified', 0)} files.",
                "status": "active" if ops.get("status") == "integrity_verified" else "partial",
                "color": "#fb923c",
                "path": "../Æion_AgenticOPS",
            },
            {
                "id": "croa",
                "name": "Æion CROA",
                "role": "Offline evidence packet validation.",
                "used": f"audit.py init/validate/report · outcome {croa.get('outcome', 'not run')} · no conformance claim.",
                "status": "partial",
                "color": "#f472b6",
                "path": "../Æion_CROA",
            },
            {
                "id": "observability",
                "name": "Æion Observability",
                "role": "Task-level cost, time and outcome contract.",
                "used": "OTel-shaped JSONL + SQLite metrics. No collector is running.",
                "status": "partial",
                "color": "#22d3ee",
                "path": "../Æion_Observability",
            },
            {
                "id": "spaceship",
                "name": "Æion SpaceShip",
                "role": "G0–G7 delivery gates.",
                "used": "Documentation only. This repository does not start an application.",
                "status": "docs-only",
                "color": "#818cf8",
                "path": "../AEion_SpaceShip",
            },
            {
                "id": "paperclip",
                "name": "Paperclip",
                "role": "Local workboard and issue ledger.",
                "used": (
                    f"{health.get('version', '—')} · {health.get('deployment_mode', '—')} · company ParcelDesk."
                    if health.get("available")
                    else f"Unavailable: {health.get('error')}"
                ),
                "status": "active" if health.get("available") else "partial",
                "color": "#facc15",
                "path": "vendor/paperclip",
            },
        ]

    def _ops_controls(self) -> list[dict[str, Any]]:
        if settings.agent_runtime == "opencode-cli":
            independent = model_family(settings.opencode_model) != model_family(settings.opencode_review_model)
            credential = "operator-owned OpenCode credential"
        else:
            independent = False
            credential = "operator-owned Claude CLI session"
        review_evidence = (
            "two model families; same OpenCode provider; human checkpoint still required for high risk"
            if independent
            else "same model family; human checkpoint"
        )
        return [
            {"id": "OPS-01", "name": "Filesystem scope", "status": "partial", "evidence": f"canonical pytest has a mandatory {settings.test_backend} boundary; parent and model CLI remain native"},
            {"id": "OPS-02", "name": "Egress allowlist", "status": "disabled", "evidence": "agent CLI reaches its provider; no firewall"},
            {"id": "OPS-03", "name": "Protected paths", "status": "enforced", "evidence": "policy/**, .github/** denied by the gate and the OpenCode edit wall"},
            {"id": "OPS-04", "name": "Diff bound", "status": "enforced", "evidence": "6 files / 200 lines"},
            {"id": "OPS-05", "name": "Test requirement", "status": "enforced", "evidence": "real pytest JUnit result required"},
            {"id": "OPS-06", "name": "Independent review", "status": "partial", "evidence": review_evidence},
            {"id": "OPS-07", "name": "Human approval", "status": "enforced", "evidence": "high/critical and app/pricing.py"},
            {"id": "OPS-08", "name": "Budget", "status": "partial", "evidence": "reported usage checked before each routed call; per-mission Gate after usage; no upstream billing cap"},
            {"id": "OPS-09", "name": "Audit trail", "status": "partial", "evidence": "SQLite ledger + JSONL; local files are operator-writable"},
            {"id": "OPS-10", "name": "Evidence digest", "status": "enforced", "evidence": "SHA-256 mission packet"},
            {"id": "OPS-11", "name": "Secret isolation", "status": "partial", "evidence": "env allowlist; .env outside the sandbox; OpenCode read rules deny .env"},
            {"id": "OPS-12", "name": "Network isolation", "status": "partial", "evidence": "canonical tests deny network; model CLI egress remains native"},
            {"id": "OPS-13", "name": "Credential scope", "status": "partial", "evidence": credential},
            {"id": "OPS-14", "name": "Rollback", "status": "disabled", "evidence": "no automatic rollback"},
            {
                "id": "OPS-15",
                "name": "Model independence",
                "status": "partial" if independent else "disabled",
                "evidence": "two model families, one provider/runtime" if independent else "one runtime/model family",
            },
            {"id": "OPS-16", "name": "Production access", "status": "disabled", "evidence": "none provisioned"},
        ]

    def _routines(self) -> list[dict[str, Any]]:
        baseline = self._baseline
        ops = self._ops
        croa = self._croa
        health = paperclip.health()
        tests = (baseline or {}).get("tests", {})
        return [
            {
                "name": "Sandbox baseline",
                "schedule": "on app start",
                "last": (baseline or {}).get("summary", "not run"),
                "status": "healthy" if baseline and tests.get("available") else "warning",
                "next": "on restart",
            },
            {
                "name": "OPS integrity",
                "schedule": "manual / on start",
                "last": (ops or {}).get("status", "not run"),
                "status": "healthy" if ops and ops.get("status") == "integrity_verified" else "warning",
                "next": "manual",
            },
            {
                "name": "CROA structure audit",
                "schedule": "manual / on start",
                "last": f"{croa.get('outcome', 'not run')} · no conformance claim" if croa else "not run",
                "status": "healthy" if croa and croa.get("packet_valid") else "warning",
                "next": "manual",
            },
            {
                "name": "Paperclip heartbeat",
                "schedule": "service 30 s",
                "last": health.get("status", "unreachable") if health.get("available") else "unreachable",
                "status": "healthy" if health.get("available") else "warning",
                "next": "continuous",
            },
        ]

    def snapshot(self) -> dict[str, Any]:
        missions = [self._shape_mission(mission) for mission in store.missions(40)]
        agents = self._shape_agents(missions)
        graph = self._shape_graph()
        memory = self._shape_memory(graph)
        evidence = self._shape_evidence()
        runs = store.runs(limit=200)
        policy = self._policy(missions)
        kpis = build_kpis(agents, settings.budget_usd)
        charts = build_charts([store.mission(mission["id"]) or mission for mission in missions], runs)
        health = paperclip.health()
        issues = self.paperclip_issues()
        return {
            "meta": {
                "mode": "REAL",
                "version": VERSION,
                "generated_at": _now(),
                "tick": store.count_events(),
                "tick_seconds": 1,
                "engine": "real-local",
                "execution": "native-host",
                "max_active_missions": settings.max_active_missions,
                "note": (
                    "Real local components: Graphify AST, SQLite FTS5, Paperclip API, "
                    "git sandbox, pytest, OpenCode CLI (strict sandboxed permissions), "
                    "CROA CLI and OPS checker."
                ),
            },
            "kpis": kpis,
            "agents": agents,
            "missions": missions,
            "events": store.events(120),
            "charts": charts,
            "policy": policy,
            "frameworks": self._frameworks(),
            "ops_controls": self._ops_controls(),
            "routines": self._routines(),
            "memory": memory,
            "graph": graph,
            "evidence_packets": evidence,
            "routing": [
                {
                    "mission": mission["id"],
                    "fixture": mission["fixture"],
                    "risk": mission["risk"],
                    "profile": mission["profile"],
                    "gate": mission["gate_decision"] or "pending",
                    "outcome": mission["outcome"] or "pending",
                    "cost": mission["cost"],
                }
                for mission in missions[:20]
            ],
            "client": self._shape_client(missions),
            "runtime": self._runtime(),
            "scenarios": [{"id": key, "title": value["title"], "kind": value["kind"]} for key, value in SCENARIOS.items()],
            "paperclip": {
                "health": health,
                "company": issues.get("company"),
                "issues": issues.get("issues", []),
                "error": issues.get("error"),
                "available": issues.get("available", False),
            },
        }

    def reset(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": (
                "The ledger is append-only. Reset is disabled; delete runtime/aeion.db "
                "manually only if you intend to destroy the audit trail."
            ),
        }

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "mode": "real",
            "version": VERSION,
            "components": self._runtime(),
            "paperclip": paperclip.health(),
            "agent": self.agent_probe(),
        }


engine = RealEngine()
