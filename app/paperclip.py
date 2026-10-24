"""Real Paperclip API client.

Paperclip runs locally at PAPERCLIP_BASE_URL. Every mission is a real issue in
the ParcelDesk company. If the service is down, the caller gets an explicit
unavailable state; no local issue is invented to replace it.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from .config import settings
from .paperclip_auth import load_board_token
from .store import store

COMPANY_NAME = "ParcelDesk"


class PaperclipClient:
    def __init__(self) -> None:
        self.base_url = settings.paperclip_base_url
        self._health_cache: tuple[float, dict[str, Any]] | None = None

    # ------------------------------------------------------------------ basics
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        try:
            token = settings.paperclip_api_token or load_board_token(self.base_url)
        except (OSError, ValueError):
            # A containerised cockpit reaches the workboard by service name, not
            # loopback; without an explicit token it stays unauthenticated and the
            # caller reports Paperclip as unavailable instead of crashing.
            token = ""
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = self.base_url + path
        try:
            response = httpx.request(
                method,
                url,
                headers=self._headers(),
                timeout=8.0,
                trust_env=False,
                follow_redirects=False,
                **kwargs,
            )
        except httpx.HTTPError as error:
            return {"ok": False, "error": f"Paperclip unreachable: {error}", "status": None}
        except (ValueError, OSError):
            return {"ok": False, "error": "Paperclip local authorization unavailable; reconnect the controller", "status": None}
        if response.status_code >= 300:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text[:400]
            return {
                "ok": False,
                "error": f"Paperclip HTTP {response.status_code}: {detail}",
                "status": response.status_code,
            }
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        return {"ok": True, "status": response.status_code, "data": payload}

    # ------------------------------------------------------------------ health
    def health(self, force: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force and self._health_cache and now - self._health_cache[0] < 5:
            return self._health_cache[1]
        result = self._request("GET", "/api/health")
        if not result["ok"]:
            payload = {
                "available": False,
                "url": self.base_url,
                "error": result["error"],
            }
        else:
            data = result["data"]
            payload = {
                "available": True,
                "url": self.base_url,
                "status": data.get("status"),
                "version": data.get("version"),
                "deployment_mode": data.get("deploymentMode"),
                "commit": data.get("commit"),
                "auth_ready": data.get("authReady"),
                "bootstrap": data.get("bootstrapStatus"),
                "warnings": data.get("warnings", []),
            }
        self._health_cache = (now, payload)
        return payload

    # ----------------------------------------------------------------- company
    def ensure_company(self) -> dict[str, Any]:
        configured = settings.paperclip_company_id or store.get_kv("paperclip_company_id")
        companies = self._request("GET", "/api/companies")
        if not companies["ok"]:
            return {"available": False, "error": companies["error"]}
        items = companies["data"]
        if not isinstance(items, list):
            items = []
        if configured:
            for item in items:
                if item.get("id") == configured:
                    return {"available": True, "company": item}
        for item in items:
            if str(item.get("name", "")).strip().lower() == COMPANY_NAME.lower():
                store.set_kv("paperclip_company_id", str(item["id"]))
                return {"available": True, "company": item}
        created = self._request(
            "POST",
            "/api/companies",
            json={
                "name": COMPANY_NAME,
                "description": "Local ParcelDesk POC company. Issues are created by the Æion engine.",
                "budgetMonthlyCents": 5000,
            },
        )
        if not created["ok"]:
            return {"available": False, "error": created["error"]}
        company = created["data"]
        store.set_kv("paperclip_company_id", str(company["id"]))
        return {"available": True, "company": company, "created": True}

    # ------------------------------------------------------------------- issues
    def create_issue(self, mission: dict[str, Any]) -> dict[str, Any]:
        company = self.ensure_company()
        if not company.get("available"):
            return {"available": False, "error": company.get("error")}
        company_id = company["company"]["id"]
        priority = mission.get("priority", "medium").lower()
        if priority not in {"critical", "high", "medium", "low"}:
            priority = "medium"
        result = self._request(
            "POST",
            f"/api/companies/{company_id}/issues",
            json={
                "title": f"{mission['id']} · {mission['title']}",
                "description": mission.get("subtitle") or "",
                "status": "todo",
                "priority": priority,
                "idempotencyKey": mission["id"],
            },
        )
        if not result["ok"]:
            return {"available": True, "created": False, "error": result["error"]}
        issue = result["data"]
        prefix = company["company"].get("issuePrefix") or "PAR"
        return {
            "available": True,
            "created": True,
            "company_id": company_id,
            "company_name": company["company"].get("name"),
            "issue_id": issue.get("id"),
            "identifier": issue.get("identifier"),
            "status": issue.get("status"),
            "url": f"{self.base_url}/{prefix}/issues/{issue.get('identifier')}",
        }

    def update_issue(
        self,
        issue_id: str,
        *,
        status: str | None = None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if status:
            body["status"] = status
        if status == "blocked":
            # Paperclip 0.3.1 requires an explicit owner and recovery action for
            # blocked work without an upstream blocker or pending approval.
            body["unblockDescriptor"] = {
                "owner": "board",
                "action": "Resolve the gate denial or runtime error, then start a new mission. " + (comment or "Review the mission evidence.")[:1800],
            }
        if comment:
            body["comment"] = comment
        if not body:
            return {"ok": True, "skipped": True}
        result = self._request("PATCH", f"/api/issues/{issue_id}", json=body)
        return result

    def list_issues(self, limit: int = 50) -> dict[str, Any]:
        company = self.ensure_company()
        if not company.get("available"):
            return {"available": False, "error": company.get("error"), "issues": []}
        company_id = company["company"]["id"]
        result = self._request(
            "GET",
            f"/api/companies/{company_id}/issues",
            params={"limit": limit},
        )
        if not result["ok"]:
            return {"available": True, "error": result["error"], "issues": []}
        payload = result["data"]
        issues = payload.get("issues") if isinstance(payload, dict) else payload
        if not isinstance(issues, list):
            issues = []
        return {
            "available": True,
            "company": company["company"],
            "issues": issues,
        }


paperclip = PaperclipClient()
