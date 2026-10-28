"""Host-side operator delivery of a sealed product release to GitHub.

This is deliberately outside the cockpit: the Factory produces a sealed local
release, and an operator command turns it into a branch, an issue, a pull
request and a CI-gated squash merge. No provider credential, GitHub token or
Docker socket enters a model role or the cockpit; the operator's own ``gh``
authentication is used on the host.

The tool refuses to deliver unless the receipt, packet, release commit, clean
working tree and product paths all agree. It never rewrites the release or the
sealed packet. Re-running it reuses the recorded issue and pull request.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .evidence import EvidenceWriter, _digest

PRODUCT_PREFIX = "sandbox/parceldesk/product"
PRODUCT_RELEASE_SCHEMA = "aeion.local-delivery/v1"
DELIVERY_SCHEMA = "aeion.github-delivery/v1"
MISSION_PATTERN = re.compile(r"PD-\d{3,}")
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
REQUIRED_CHECK = "product-tests"


class DeliveryError(RuntimeError):
    """A refused or failed operator delivery; nothing is published."""


@dataclass
class Release:
    mission: str
    receipt: dict[str, Any]
    packet: dict[str, Any]
    ticket: dict[str, Any] | None
    repo: Path
    product: Path
    head: str
    template_tree: str
    parent_tree: str | None
    head_tree: str


@dataclass
class Plan:
    mission: str
    branch: str
    checkout: Path
    commit: str
    changed_files: list[str]
    issue_body: str
    pr_body: str
    issue_title: str
    pr_title: str
    release: Release
    lines_added: int = 0
    lines_removed: int = 0
    notes: list[str] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run(argv: list[str], cwd: Path, *, input_text: str | None = None,
         check: bool = True, timeout: float = 180) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, cwd=cwd, input=input_text, text=True,
                            capture_output=True, timeout=timeout)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:800]
        raise DeliveryError(f"{' '.join(argv[:3])} failed: {detail}")
    return result


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null",
                 "-c", "commit.gpgsign=false", *args], repo, check=check)


def _json(result: subprocess.CompletedProcess[str], action: str) -> Any:
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise DeliveryError(f"{action} returned invalid JSON: {error}") from None


def _ticket(runtime_root: Path, mission: str) -> dict[str, Any] | None:
    database = runtime_root / "parceldesk.db"
    if not database.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "SELECT * FROM tickets WHERE mission_id = ? ORDER BY created_at DESC LIMIT 1",
                (mission,),
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise DeliveryError(f"Ticket ledger cannot be read: {error}") from None
    return dict(row) if row else None


def load_release(runtime_root: Path, mission: str) -> Release:
    """Verify a sealed local product release and return its immutable facts."""
    runtime_root = Path(runtime_root).resolve()
    if not MISSION_PATTERN.fullmatch(mission):
        raise DeliveryError("Invalid mission identity")
    release_root = runtime_root / "sandbox" / "releases" / mission
    receipt_path = release_root / "receipt.json"
    try:
        receipt = json.loads(receipt_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise DeliveryError(f"Release receipt cannot be read: {error}") from None
    if (not isinstance(receipt, dict) or receipt.get("schema") != PRODUCT_RELEASE_SCHEMA
            or receipt.get("mission") != mission or receipt.get("product") != "product"):
        raise DeliveryError("A sealed product release receipt is required")
    if not isinstance(receipt.get("packet_digest"), str) or not receipt["packet_digest"].startswith("sha256:"):
        raise DeliveryError("Release receipt has no sealed packet digest")
    packet_path = runtime_root / "croa" / "packets" / f"CROA-{mission}.json"
    verified = EvidenceWriter.verify(packet_path, receipt["packet_digest"])
    if not verified.get("valid"):
        raise DeliveryError("Sealed evidence packet changed or does not match the receipt")
    try:
        packet = json.loads(packet_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise DeliveryError(f"Sealed evidence packet cannot be read: {error}") from None
    body = packet if isinstance(packet, dict) else {}
    mission_body = body.get("mission") if isinstance(body.get("mission"), dict) else {}
    if (not body.get("sealed") or mission_body.get("id") != mission
            or mission_body.get("status") != "completed"):
        raise DeliveryError("A completed mission with sealed evidence is required")
    gate = body.get("gate") if isinstance(body.get("gate"), dict) else {}
    decision = gate.get("decision")
    if decision == "approved":
        if mission_body.get("human_approval") != "operator" or mission_body.get("outcome") != "assisted":
            raise DeliveryError("An approved gate requires recorded operator approval")
    elif decision != "allow" or mission_body.get("outcome") != "autonomous":
        raise DeliveryError("No recorded acceptance authority for delivery")

    repo = release_root / "repo"
    if not (repo / ".git").exists() or not (repo / "product").is_dir():
        raise DeliveryError("Release checkout is missing or has no product directory")
    status = _git(repo, "status", "--porcelain")
    if status.stdout.strip():
        raise DeliveryError("Release checkout is not clean")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    if head != receipt.get("commit"):
        raise DeliveryError("Release HEAD differs from the recorded commit")
    roots = _git(repo, "rev-list", "--max-parents=0", "HEAD").stdout.split()
    if len(roots) != 1:
        raise DeliveryError("Release history must have exactly one root")
    template_tree = _git(repo, "rev-parse", f"{roots[0]}:product").stdout.strip()
    head_tree = _git(repo, "rev-parse", "HEAD:product").stdout.strip()
    parent_tree = None
    parent = receipt.get("parent_release")
    if isinstance(parent, dict) and isinstance(parent.get("commit"), str):
        parent_commit = parent["commit"]
        if not re.fullmatch(r"[0-9a-f]{40,64}", parent_commit):
            raise DeliveryError("Parent release commit is invalid")
        present = _git(repo, "cat-file", "-e", f"{parent_commit}^{{commit}}", check=False)
        if present.returncode == 0:
            parent_tree = _git(repo, "rev-parse", f"{parent_commit}:product").stdout.strip()
    if not head_tree or not template_tree:
        raise DeliveryError("Release product tree cannot be resolved")
    return Release(
        mission=mission, receipt=receipt, packet=body, ticket=_ticket(runtime_root, mission),
        repo=repo, product=repo / "product", head=head, template_tree=template_tree,
        parent_tree=parent_tree, head_tree=head_tree,
    )


def _issue_body(release: Release) -> tuple[str, str]:
    ticket = release.ticket or {}
    title = str(ticket.get("title") or release.packet.get("mission", {}).get("title") or release.mission)
    identifier = str(ticket.get("id") or "unlinked")
    outcome = str(ticket.get("outcome") or "")
    acceptance = str(ticket.get("acceptance") or "")
    constraints = str(ticket.get("constraints") or "")
    lines = [
        f"Operator delivery of the sealed product release `{release.mission}`.",
        "",
        f"- Ticket: `{identifier}`",
        f"- Risk: `{ticket.get('risk') or release.packet.get('mission', {}).get('risk')}`",
        f"- Release commit: `{release.head}`",
        f"- Sealed packet: `{release.receipt.get('packet_digest')}`",
        "",
        "## Observable outcome",
        "",
        outcome or "See the sealed packet.",
        "",
        "## Acceptance",
        "",
        acceptance or "See the sealed packet.",
    ]
    if constraints:
        lines += ["", "## Constraints", "", constraints]
    lines += [
        "",
        f"<!-- aeion-mission:{release.mission} -->",
        "",
        "Prepared host-side from sealed local evidence. The Factory did not receive a GitHub credential.",
    ]
    return title, "\n".join(lines)


def _pr_body(release: Release, issue_number: int | None) -> tuple[str, str]:
    ticket = release.ticket or {}
    title = f"{release.mission}: {ticket.get('title') or release.packet.get('mission', {}).get('title') or 'product release'}"
    tests = release.packet.get("tests") if isinstance(release.packet.get("tests"), dict) else {}
    gate = release.packet.get("gate") if isinstance(release.packet.get("gate"), dict) else {}
    review = release.packet.get("review")
    lines = [
        f"Delivers the sealed product release `{release.mission}` from the local Factory.",
        "",
        f"- Ticket: `{ticket.get('id') or 'unlinked'}` ({ticket.get('risk') or 'unknown'} risk)",
        f"- Release commit: `{release.head}`",
        f"- Parent release: `{release.receipt.get('parent_release', {}).get('mission') or 'root'}`",
        f"- Sealed packet: `{release.receipt.get('packet_digest')}`",
        f"- Worker tests: **{tests.get('passed', 0)}/{tests.get('total', 0)}**",
        f"- Gate: **{gate.get('decision') or 'unknown'}** · `{gate.get('reason_code') or 'unknown'}`",
        f"- Independent review: **{review or 'unknown'}**",
        "",
        "## What changed",
        "",
        "The `sandbox/parceldesk/product` tree is replaced with the accepted release tree.",
        "The product backend and frontend suites run in the `product-tests` job.",
        "",
        "## Authority",
        "",
        "This branch was prepared by a host-side operator command from a sealed local",
        "release. The cockpit and the model roles never received a GitHub credential.",
    ]
    if issue_number is not None:
        lines += ["", f"Closes #{issue_number}."]
    lines += ["", f"<!-- aeion-mission:{release.mission} -->"]
    return title, "\n".join(lines)


def _ignore_build_output(_directory: str, names: list[str]) -> set[str]:
    return {
        name for name in names
        if name in {".git", "target", "node_modules", "__pycache__", ".pytest_cache",
                    "junit.xml", ".DS_Store"} or name.endswith(".pyc")
    }


def _scope_error(changed: list[str]) -> str | None:
    for path in changed:
        if path != PRODUCT_PREFIX and not path.startswith(PRODUCT_PREFIX + "/"):
            return f"Change outside {PRODUCT_PREFIX}/: {path}"
    return None


class GitHubDelivery:
    def __init__(self, release: Release, repository: str, *, base: str = "main",
                 work_root: Path | None = None, state_root: Path | None = None,
                 run: Callable[..., subprocess.CompletedProcess[str]] = _run,
                 git_bin: str = "git", gh_bin: str = "gh"):
        if not REPOSITORY_PATTERN.fullmatch(repository):
            raise DeliveryError("Repository must be owner/name")
        if not re.fullmatch(r"[A-Za-z0-9._/-]{1,120}", base) or ".." in base:
            raise DeliveryError("Invalid base branch")
        self.release = release
        self.repository = repository
        self.base = base
        self.work_root = Path(work_root or (Path(__file__).resolve().parents[1] / "runtime/github-delivery/work")).resolve()
        self.state_root = Path(state_root or self.work_root.parent).resolve()
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.run = run
        self.git_bin = git_bin
        self.gh_bin = gh_bin

    # ------------------------------------------------------------------ git
    def _git(self, repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.run([self.git_bin, "-C", str(repo), "-c", "core.hooksPath=/dev/null",
                         "-c", "commit.gpgsign=false", *args], repo, check=check)

    def _gh(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.run([self.gh_bin, *args], self.state_root, check=check)

    def _state_path(self) -> Path:
        return self.state_root / f"{self.release.mission}.json"

    def state(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self._state_path().read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _record(self, **changes: Any) -> dict[str, Any]:
        state = self.state() or {
            "schema": DELIVERY_SCHEMA, "mission": self.release.mission,
            "repository": self.repository, "base": self.base,
            "release": self.release.head, "packet_digest": self.release.receipt.get("packet_digest"),
            "created_at": _now(),
        }
        state.update(changes)
        state["updated_at"] = _now()
        target = self._state_path()
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
        os.replace(temporary, target)
        return state

    # --------------------------------------------------------------- prepare
    def prepare(self, *, force: bool = False, dry_run: bool = False) -> Plan:
        existing = self.state()
        if existing and existing.get("pr", {}).get("state") == "MERGED":
            raise DeliveryError(f"{self.release.mission} is already merged in {existing['pr'].get('url')}")
        branch = f"factory/{self.release.mission.lower()}"
        checkout = Path(tempfile.mkdtemp(prefix=f"{self.release.mission.lower()}-", dir=self.work_root))
        self._gh("repo", "clone", self.repository, str(checkout), "--", "--quiet")
        base_ref = f"origin/{self.base}"
        self._git(checkout, "fetch", "--quiet", "origin", self.base)
        self._git(checkout, "checkout", "-B", branch, base_ref)
        base_tree = self._git(checkout, "rev-parse", f"HEAD:{PRODUCT_PREFIX}").stdout.strip()
        expected = {self.release.template_tree, self.release.parent_tree, self.release.head_tree}
        if base_tree == self.release.head_tree:
            raise DeliveryError("The release product tree is already on the base branch")
        if base_tree not in expected - {None} and not force:
            # The public base can lag several sealed releases. Accept it only when
            # it is a product tree from this release's own ancestry; a diverged
            # tree still requires the explicit operator --force.
            ancestor_trees = set()
            for commit in self._git(self.release.repo, "rev-list", "HEAD").stdout.split():
                tree = self._git(self.release.repo, "rev-parse", f"{commit}:product", check=False)
                if tree.returncode == 0:
                    ancestor_trees.add(tree.stdout.strip())
            if base_tree not in ancestor_trees:
                raise DeliveryError(
                    f"{PRODUCT_PREFIX} on {self.base} does not match the release parent, template "
                    "or any ancestor release; refuse to overwrite diverged product code "
                    "(use --force only after inspection)"
                )
        target = checkout / PRODUCT_PREFIX
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(self.release.product, target, ignore=_ignore_build_output)
        self._git(checkout, "add", "-A")
        changed_result = self._git(checkout, "diff", "--cached", "--name-only")
        changed = [line for line in changed_result.stdout.splitlines() if line]
        if not changed:
            raise DeliveryError("The release produced no product change")
        scope = _scope_error(changed)
        if scope:
            raise DeliveryError(scope)
        numstat = self._git(checkout, "diff", "--cached", "--numstat")
        lines_added = lines_removed = 0
        for line in numstat.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                lines_added += int(parts[0])
                lines_removed += int(parts[1])
        ticket = self.release.ticket or {}
        message = "\n".join([
            f"{self.release.mission}: {ticket.get('title') or self.release.packet.get('mission', {}).get('title')}",
            "",
            f"Ticket: {ticket.get('id') or 'unlinked'}",
            f"Release: {self.release.head}",
            f"Packet: {self.release.receipt.get('packet_digest')}",
            f"Worker tests: {(self.release.packet.get('tests') or {}).get('passed', 0)}/"
            f"{(self.release.packet.get('tests') or {}).get('total', 0)}",
            f"Review: {self.release.packet.get('review') or 'unknown'}",
        ])
        self._git(checkout, "-c", "user.name=Aeion Operator", "-c", "user.email=operator@localhost",
                  "commit", "--quiet", "-m", message)
        commit = self._git(checkout, "rev-parse", "HEAD").stdout.strip()
        issue_title, issue_body = _issue_body(self.release)
        pr_title, pr_body = _pr_body(self.release, None)
        plan = Plan(
            mission=self.release.mission, branch=branch, checkout=checkout, commit=commit,
            changed_files=changed, issue_body=issue_body, pr_body=pr_body,
            issue_title=issue_title, pr_title=pr_title, release=self.release,
            lines_added=lines_added, lines_removed=lines_removed,
        )
        self._record(branch=branch, commit=commit, changed_files=changed,
                     lines_added=lines_added, lines_removed=lines_removed,
                     prepared_at=_now(), dry_run=bool(dry_run))
        return plan

    # --------------------------------------------------------------- publish
    def _find_issue(self) -> dict[str, Any] | None:
        result = self._gh("issue", "list", "--repo", self.repository, "--state", "all",
                          "--limit", "100", "--json", "number,url,state,title,body", check=False)
        if result.returncode != 0:
            raise DeliveryError(f"GitHub issue list failed: {(result.stderr or '').strip()[:400]}")
        marker = f"<!-- aeion-mission:{self.release.mission} -->"
        for issue in _json(result, "GitHub issue list"):
            if marker in str(issue.get("body") or ""):
                return issue
        return None

    def _create_issue(self, plan: Plan) -> dict[str, Any]:
        marker = f"<!-- aeion-mission:{self.release.mission} -->"
        result = self._gh("issue", "create", "--repo", self.repository,
                          "--title", f"{plan.issue_title} [{self.release.mission}]",
                          "--body", plan.issue_body, check=False)
        if result.returncode != 0:
            raise DeliveryError(f"GitHub issue creation failed: {(result.stderr or '').strip()[:400]}")
        url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        number = None
        match = re.search(r"/issues/(\d+)$", url)
        if match:
            number = int(match.group(1))
        if number is None:
            found = self._find_issue()
            if found:
                return found
            raise DeliveryError("GitHub issue creation returned no issue number")
        return {"number": number, "url": url, "state": "OPEN"}

    def _find_pr(self, branch: str) -> dict[str, Any] | None:
        result = self._gh("pr", "list", "--repo", self.repository, "--head", branch,
                          "--state", "all", "--json", "number,url,state,mergeable,baseRefName", check=False)
        if result.returncode != 0:
            raise DeliveryError(f"GitHub pull-request list failed: {(result.stderr or '').strip()[:400]}")
        items = _json(result, "GitHub pull-request list")
        return items[0] if items else None

    def _create_pr(self, plan: Plan, issue_number: int | None) -> dict[str, Any]:
        title, body = _pr_body(self.release, issue_number)
        result = self._gh("pr", "create", "--repo", self.repository, "--base", self.base,
                          "--head", plan.branch, "--title", title, "--body", body, check=False)
        if result.returncode != 0:
            raise DeliveryError(f"GitHub pull-request creation failed: {(result.stderr or '').strip()[:400]}")
        url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        match = re.search(r"/pull/(\d+)$", url)
        if not match:
            found = self._find_pr(plan.branch)
            if found:
                return found
            raise DeliveryError("GitHub pull-request creation returned no number")
        return {"number": int(match.group(1)), "url": url, "state": "OPEN", "mergeable": "UNKNOWN"}

    def _checks(self, number: int) -> list[dict[str, Any]]:
        result = self._gh("pr", "checks", str(number), "--repo", self.repository,
                          "--json", "name,state,bucket", check=False)
        if result.returncode not in (0, 8):
            text = f"{result.stderr or ''}\n{result.stdout or ''}".lower()
            if "no checks reported" in text:
                return []  # The workflow has not registered its checks yet.
            raise DeliveryError(f"GitHub check query failed: {(result.stderr or '').strip()[:400]}")
        return _json(result, "GitHub check query")

    def wait_for_checks(self, number: int, *, timeout: float = 1800) -> dict[str, Any]:
        started = time.monotonic()
        deadline = started + max(30, timeout)
        last: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            checks = self._checks(number)
            last = checks
            names = {str(item.get("name") or "") for item in checks}
            if any(str(item.get("bucket")) == "fail" for item in checks):
                failed = [item.get("name") for item in checks if str(item.get("bucket")) == "fail"]
                raise DeliveryError("Required GitHub checks failed: " + ", ".join(map(str, failed)))
            if REQUIRED_CHECK not in names:
                if time.monotonic() - started < 180:
                    time.sleep(20)
                    continue
                raise DeliveryError(f"The pull request has no {REQUIRED_CHECK} check")
            if checks and all(str(item.get("bucket")) in {"pass", "skipping"} for item in checks):
                return {"checks": checks, "ok": True}
            time.sleep(20)
        raise DeliveryError(f"Timed out waiting for GitHub checks; last state: {json.dumps(last)[:800]}")

    def merge_existing(self, *, timeout: float = 1800) -> dict[str, Any]:
        """Wait for and merge the PR recorded by an earlier prepare/publish."""
        state = self.state() or {}
        issue = state.get("issue") if isinstance(state.get("issue"), dict) else None
        pr = state.get("pr") if isinstance(state.get("pr"), dict) else None
        if not pr or pr.get("number") is None:
            raise DeliveryError("No recorded pull request to merge")
        if pr.get("state") == "MERGED":
            return {"ok": True, "issue": issue, "pr": pr, "merged": True,
                    "checks": state.get("checks") or []}
        checks = self.wait_for_checks(int(pr["number"]), timeout=timeout)
        result = self._gh("pr", "merge", str(pr["number"]), "--repo", self.repository,
                          "--squash", "--delete-branch", check=False)
        if result.returncode != 0:
            raise DeliveryError(f"GitHub merge failed: {(result.stderr or '').strip()[:500]}")
        pr = {**pr, "state": "MERGED", "merged_at": _now()}
        self._record(pr=pr, checks=checks.get("checks"), merged_at=_now())
        return {"ok": True, "issue": issue, "pr": pr, "merged": True,
                "checks": checks.get("checks")}

    def publish(self, plan: Plan, *, create_issue: bool = True,
                wait: bool = False, merge: bool = False, timeout: float = 1800) -> dict[str, Any]:
        existing = self.state() or {}
        if existing.get("dry_run"):
            self._record(dry_run=False)
        issue = existing.get("issue") if isinstance(existing.get("issue"), dict) else None
        if create_issue and not issue:
            issue = self._find_issue()
            if issue is None:
                issue = self._create_issue(plan)
            self._record(issue=issue, issue_body=plan.issue_body)
        issue_number = int(issue["number"]) if issue and issue.get("number") is not None else None
        pr = existing.get("pr") if isinstance(existing.get("pr"), dict) else None
        if not pr:
            pr = self._find_pr(plan.branch)
            if pr is None:
                self._git(plan.checkout, "push", "-u", "origin", plan.branch)
                pr = self._create_pr(plan, issue_number)
            self._record(pr=pr, pr_body=plan.pr_body, commit=plan.commit)
        elif self.state().get("commit") != plan.commit:
            # A retry produced a new sealed commit for the same mission; move the
            # existing branch forward only when the remote still has the old one.
            self._git(plan.checkout, "push", "--force-with-lease", "origin", plan.branch)
            self._record(commit=plan.commit)
        if pr.get("state") == "MERGED":
            return {"ok": True, "issue": issue, "pr": pr, "merged": True, "checks": []}
        if pr.get("state") == "OPEN":
            # A retry prepares a new commit on the current base. Move the existing
            # branch forward; --force-with-lease refuses if someone else changed it.
            self._git(plan.checkout, "push", "--force-with-lease", "origin", plan.branch)
            self._record(commit=plan.commit)
        if wait or merge:
            checks = self.wait_for_checks(int(pr["number"]), timeout=timeout)
        else:
            checks = {"checks": [], "ok": False}
        if merge:
            result = self._gh("pr", "merge", str(pr["number"]), "--repo", self.repository,
                              "--squash", "--delete-branch", check=False)
            if result.returncode != 0:
                raise DeliveryError(f"GitHub merge failed: {(result.stderr or '').strip()[:500]}")
            pr = {**pr, "state": "MERGED", "merged_at": _now()}
            self._record(pr=pr, checks=checks.get("checks"), merged_at=_now())
            return {"ok": True, "issue": issue, "pr": pr, "merged": True, "checks": checks.get("checks")}
        self._record(pr=pr, checks=checks.get("checks"))
        return {"ok": True, "issue": issue, "pr": pr, "merged": False, "checks": checks.get("checks")}
