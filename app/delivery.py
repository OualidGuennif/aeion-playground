"""Reconstruct an accepted patch as a verified, local Git release.

This writes only runtime artifacts. It never changes the demo, a source template,
existing mission checkouts, a remote repository or an external deployment.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .evidence import EvidenceWriter
from .executor import ExecutorClient, InProcessExecutor
from .sandbox import SandboxManager
from .store import Store


class LocalDelivery:
    def __init__(self, ledger: Store, workspace: SandboxManager, directory: Path | None = None,
                 executor: ExecutorClient | None = None):
        self.ledger = ledger
        self.workspace = workspace
        self.executor: ExecutorClient = executor or InProcessExecutor(workspace)
        self.directory = directory or workspace.runs_dir.parent / "releases"

    def deliver(self, mission_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"PD-\d{3,}", mission_id):
            raise ValueError("Invalid mission identity")
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / ".delivery.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._deliver(mission_id)

    def _deliver(self, mission_id: str) -> dict[str, Any]:
        mission = self.ledger.mission(mission_id)
        indexed = self.ledger.evidence_for(mission_id)
        if not mission or mission["status"] != "completed" or not indexed or indexed["status"] != "sealed":
            raise ValueError("Local delivery requires a completed mission and sealed evidence")
        packet = indexed["payload"]["payload"]
        path = Path(indexed["path"])
        if not EvidenceWriter.verify(path, indexed["digest"])["valid"] or json.loads(path.read_text()) != packet:
            raise ValueError("The sealed file does not match the accepted ledger evidence")
        decision = packet["gate"].get("decision")
        if not packet["sealed"] or packet["mission"]["status"] != "completed" or not (
            decision == "allow" and packet["mission"]["outcome"] == "autonomous"
            or decision == "approved" and packet["mission"].get("human_approval") == "operator" and packet["mission"]["outcome"] == "assisted"
        ):
            raise ValueError("No recorded acceptance authority for delivery")
        if self.workspace.policy_digest() != packet["contract"]["policy_digest"]:
            raise ValueError("Trusted policy changed since acceptance")
        destination = self.directory / mission_id
        if destination.exists():
            receipt = self.verify(mission_id, indexed["digest"])
            self._record(receipt)
            return receipt

        expected_executor = packet["contract"].get("test_executor")
        if "test_executor" in packet["contract"] and expected_executor != self.executor.probe():
            raise ValueError("Canonical test executor changed since acceptance; no new delivery started")

        staging = Path(tempfile.mkdtemp(prefix=f".{mission_id}-", dir=self.directory))
        repo = staging / "repo"
        try:
            base = packet["contract"]["base_commit"]
            if not re.fullmatch(r"[0-9a-f]{40,64}", base or ""):
                raise ValueError("Invalid recorded Git base")
            source = Path(mission["sandbox_path"])
            self._git(staging, "clone", "--quiet", "--no-checkout", "--no-hardlinks", "--local", "--", str(source), str(repo))
            for key, value in (("user.name", "Æion POC"), ("user.email", "poc@local"), ("commit.gpgsign", "false"), ("core.hooksPath", os.devnull)):
                self._git(repo, "config", key, value)
            self._git(repo, "remote", "remove", "origin")
            self._git(repo, "checkout", "--quiet", "--detach", base)
            patch = staging / "accepted.patch"
            patch.write_text(packet["diff"]["patch"])
            self._git(repo, "apply", "--index", "--", str(patch))
            before = self.workspace.collect_changes(repo, base_commit=base)
            if not before.available or before.digest != packet["diff"]["digest"]:
                raise ValueError("Reconstructed patch differs from the accepted diff")
            policy = hashlib.sha256((repo / "policy/policy.yaml").read_bytes()).hexdigest()
            if policy != packet["contract"]["policy_digest"]:
                raise ValueError("Reconstructed policy differs from the trusted policy")
            profile = packet["contract"].get("scenario", {}).get("test_profile", "legacy")
            tests = self.executor.run_tests(repo, expected_executor=expected_executor, profile=profile)
            (staging / "tests.json").write_text(json.dumps(tests.to_dict(), indent=2, ensure_ascii=False))
            if not tests.available or tests.error or tests.exit_code != 0 or not tests.total or tests.passed != tests.total or tests.failed or tests.errors or tests.skipped:
                raise ValueError(f"Local release tests did not pass completely: {tests.summary()}")
            if expected_executor is not None and tests.executor != expected_executor:
                raise ValueError("Release test identity differs from accepted executor")
            after = self.workspace.collect_changes(repo, base_commit=base)
            if not after.available or after.digest != before.digest:
                raise ValueError("Release tests changed the accepted patch")
            self._git(repo, "commit", "--quiet", "-m", f"Accept {mission_id}\n\nEvidence: {indexed['digest']}")
            # Diff hashing retains the raw output, including its final newline.
            committed = self.workspace._git(repo, "diff", "--no-renames", "--binary", base, "HEAD", "--")
            if committed.returncode or hashlib.sha256(committed.stdout.encode()).hexdigest() != packet["diff"]["digest"] or self._git(repo, "rev-parse", "HEAD^") != base:
                raise ValueError("Release commit differs from the accepted patch or base")
            receipt = {
                "schema": "aeion.local-delivery/v1", "mission": mission_id,
                "packet_digest": indexed["digest"], "base_commit": base,
                "parent_release": packet["contract"].get("source_release"),
                "commit": self._git(repo, "rev-parse", "HEAD"),
                "tree_digest": self.workspace.template_digest(repo),
                "path": str(destination / "repo"),
                "tests": {"total": tests.total, "passed": tests.passed, "exit_code": tests.exit_code},
                "test_isolation": tests.isolation,
                **({"test_executor": tests.executor} if expected_executor is not None else {}),
                "delivered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "scope": "local Git artifact only; no demo change, push or deployment",
            }
            EvidenceWriter._atomic_write(staging / "receipt.json", json.dumps(receipt, indent=2, ensure_ascii=False))
            os.rename(staging, destination)
            directory = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self._record(receipt)
            return receipt
        except Exception as error:
            retained = destination if destination.exists() else staging
            raise RuntimeError(f"Local delivery failed; retained artifacts at {retained}: {error}") from error

    def verify(self, mission_id: str, packet_digest: str) -> dict[str, Any]:
        if not re.fullmatch(r"PD-\d{3,}", mission_id):
            raise ValueError("Invalid release identity")
        receipt = json.loads((self.directory / mission_id / "receipt.json").read_text())
        repo = self.directory / mission_id / "repo"
        recorded = self.ledger.get_kv(f"delivery:{mission_id}")
        if (
            receipt.get("mission") != mission_id or receipt.get("packet_digest") != packet_digest
            or receipt.get("path") != str(repo)
            or recorded is not None and json.loads(recorded) != receipt
            or self._git(repo, "rev-parse", "HEAD") != receipt.get("commit")
            or self._git(repo, "status", "--porcelain")
            or self.workspace.template_digest(repo) != receipt.get("tree_digest")
        ):
            raise ValueError("Local release changed or belongs to different evidence")
        return receipt

    def _record(self, receipt: dict[str, Any]) -> None:
        if self.ledger.record_delivery(receipt):
            self.ledger.add_event(type="delivery.local", actor="gate", mission=receipt["mission"], severity="success",
                                  message=f"Local Git release {receipt['commit']} from sealed packet {receipt['packet_digest']}.", payload=receipt)

    def _git(self, path: Path, *args: str) -> str:
        result = self.workspace._git(path, "-c", f"core.hooksPath={os.devnull}", "-c", "commit.gpgsign=false", *args)
        if result.returncode:
            raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()}")
        return result.stdout.strip()
