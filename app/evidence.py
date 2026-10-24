"""Mission evidence packets.

A packet binds one mission's contract, diff, test run, gate decision, agent
runs and advisory context into a single SHA-256-sealed JSON file. It is a local
evidence bundle, not a CROA certification.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings

PACKET_DIR = settings.poc_dir / "runtime" / "croa" / "packets"


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EvidenceWriter:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or PACKET_DIR
        self.directory.mkdir(parents=True, exist_ok=True)

    def build(
        self,
        *,
        mission: dict[str, Any],
        diff: dict[str, Any],
        tests: dict[str, Any],
        gate: dict[str, Any],
        runs: list[dict[str, Any]],
        memory: dict[str, Any],
        graph: dict[str, Any],
        policy_digest: str,
        control_audit: dict[str, Any] | None,
        sealed: bool,
    ) -> dict[str, Any]:
        review = "NOT RUN"
        for run in runs:
            if run.get("agent") == "sentinel":
                review = str(run.get("verdict") or "UNPARSED") if run.get("status") == "completed" else "FAILED"
                break
        try:
            admission = json.loads(mission.get("contract_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            admission = {}
        if not isinstance(admission, dict):
            admission = {}
        scenario = admission.get("scenario")
        body = {
            "schema": "aeion.mission.evidence/v1",
            "mission": {
                "id": mission["id"],
                "scenario": mission.get("scenario"),
                "fixture": mission.get("fixture"),
                "title": mission.get("title"),
                "execution_source": mission.get("profile"),
                "risk": mission.get("risk"),
                "priority": mission.get("priority"),
                "status": mission.get("status"),
                "outcome": mission.get("outcome"),
                "human_approval": mission.get("human_approval"),
                "created_at": mission.get("created_at"),
                "started_at": mission.get("started_at"),
                "ended_at": mission.get("ended_at"),
            },
            "contract": {
                "base_commit": mission.get("base_commit"),
                "policy_digest": policy_digest,
                "sandbox": mission.get("sandbox_path"),
                "request_id": mission.get("request_id"),
                "admission_digest": _digest(admission) if admission else None,
                "template_digest": admission.get("template_digest"),
                "runtime": admission.get("runtime"),
                "source_release": admission.get("source_release"),
                "task": scenario.get("task") if isinstance(scenario, dict) else None,
                "patch_protocol": scenario.get("protocol") if isinstance(scenario, dict) else None,
                "routing": admission.get("routing"),
                **({"test_executor": admission["test_executor"]} if "test_executor" in admission else {}),
            },
            "diff": {
                "digest": diff.get("digest"),
                "files": diff.get("files"),
                "lines_added": diff.get("lines_added"),
                "lines_removed": diff.get("lines_removed"),
                "patch": diff.get("patch"),
            },
            "tests": tests,
            "gate": gate,
            "agent_runs": runs,
            "memory": {
                "items": memory.get("items", []),
                "query": memory.get("query"),
                "latency_ms": memory.get("latency_ms"),
                "digest": memory.get("digest"),
            },
            "graph": {
                "provider": graph.get("provider"),
                "digest": graph.get("digest"),
                "files": graph.get("files"),
                "nodes": graph.get("nodes_total"),
                "edges": graph.get("edges"),
            },
            "croa_control_audit": control_audit or {"available": False},
            "review": review,
            "sealed": sealed,
            "created_at": _now(),
            "limitation": (
                "Local evidence bundle. Structure and file digests are verifiable; "
                "this packet is not a CROA conformance claim, a security certification "
                "or a production readiness statement."
            ),
        }
        digest = _digest(body)
        body["digest"] = "sha256:" + digest
        identifier = f"CROA-{mission['id']}"
        path = self.directory / f"{identifier}.json"
        markdown_path = self.directory / f"{identifier}.md"

        return {
            "id": identifier,
            "mission": mission["id"],
            "scope": f"ParcelDesk / {mission.get('fixture')}",
            "digest": body["digest"],
            "checks": int(tests.get("total") or 0) + len(gate.get("checks") or []),
            "review": review,
            "status": "sealed" if sealed else "draft",
            "path": str(path),
            "markdown_path": str(markdown_path),
            "created_at": body["created_at"],
            "payload": body,
        }

    def write(self, **fields: Any) -> dict[str, Any]:
        return self.publish(self.build(**fields))

    def publish(self, packet: dict[str, Any]) -> dict[str, Any]:
        """Publish complete files; a sealed JSON packet can never be replaced."""
        body = packet["payload"]
        unsigned = {key: value for key, value in body.items() if key != "digest"}
        if body.get("digest") != "sha256:" + _digest(unsigned) or packet["digest"] != body["digest"]:
            raise ValueError("Evidence packet digest mismatch")
        identifier = f"CROA-{body['mission']['id']}"
        path = self.directory / f"{identifier}.json"
        markdown_path = self.directory / f"{identifier}.md"
        if packet["id"] != identifier or packet["path"] != str(path) or packet["markdown_path"] != str(markdown_path):
            raise ValueError("Evidence packet identity/path mismatch")
        if path.exists():
            previous = json.loads(path.read_text())
            if not isinstance(previous, dict):
                raise ValueError("Existing evidence packet is malformed")
            if previous.get("sealed"):
                if previous != body:
                    raise ValueError("Sealed evidence is immutable")
                return packet  # A replay leaves the original bytes untouched.
        # JSON is the commit marker. Markdown is only its readable projection.
        self._atomic_write(markdown_path, self._markdown(body))
        self._atomic_write(path, json.dumps(body, indent=2, ensure_ascii=False))
        return packet

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
                temporary = Path(output.name)
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _markdown(body: dict[str, Any]) -> str:
        mission = body["mission"]
        tests = body["tests"]
        gate = body["gate"]
        lines = [
            f"# {body['digest']}",
            "",
            f"**Mission:** {mission['id']} · {mission['title']}",
            f"**Fixture:** {mission['fixture']} · risk {mission['risk']}",
            f"**Status:** {mission['status']} · outcome {mission.get('outcome') or 'pending'}",
            f"**Base commit:** {body['contract']['base_commit']}",
            f"**Diff digest:** {body['diff']['digest']}",
            "",
            "## Tests",
            "",
            f"- {tests.get('total', 0)} total · {tests.get('passed', 0)} passed · "
            f"{tests.get('failed', 0)} failed · {tests.get('errors', 0)} errors",
            "",
            "## Gate",
            "",
            f"- Decision: **{gate.get('decision')}** ({gate.get('reason_code')})",
            f"- Reason: {gate.get('reason')}",
            "",
            "| Check | Status | Detail |",
            "| --- | --- | --- |",
        ]
        for check in gate.get("checks") or []:
            lines.append(f"| {check['label']} | {check['status']} | {check['detail']} |")
        lines.extend(
            [
                "",
                "## Limitation",
                "",
                body["limitation"],
                "",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def verify(path: Path, expected_digest: str | None = None) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text())
            claimed = payload.pop("digest", "")
            actual = "sha256:" + _digest(payload)
        except (OSError, ValueError, TypeError, AttributeError) as error:
            return {"path": str(path), "valid": False, "error": f"{type(error).__name__}: {error}"}
        return {
            "path": str(path),
            "claimed": claimed,
            "actual": actual,
            "valid": claimed == actual and (expected_digest is None or claimed == expected_digest),
        }


evidence_writer = EvidenceWriter()
