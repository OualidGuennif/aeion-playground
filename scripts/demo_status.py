#!/usr/bin/env python3
"""One-screen health and evidence view for the local ParcelDesk demo.

Reads only loopback HTTP endpoints and the private Paperclip bearer file. It never
prints the bearer token and never asks a model to do anything.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.paperclip_auth import load_api_token_file

COCKPIT = os.environ.get("AEION_COCKPIT_URL", "http://127.0.0.1:8124").rstrip("/")
WEB = os.environ.get("AEION_PRODUCT_WEB_URL", "http://127.0.0.1:8125").rstrip("/")
API = os.environ.get("AEION_PRODUCT_API_URL", "http://127.0.0.1:8081").rstrip("/")
PAPERCLIP = os.environ.get("AEION_PAPERCLIP_URL", "http://127.0.0.1:3101").rstrip("/")


def fetch(url: str, token: str = "") -> tuple[int | None, object]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            body = response.read(2_000_000)
            try:
                return response.status, json.loads(body)
            except ValueError:
                return response.status, body.decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read(2_000_000))
        except ValueError:
            body = {}
        return error.code, body
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return None, str(error)


def paperclip_view() -> dict:
    status, health = fetch(PAPERCLIP + "/api/health")
    view = {"health": status, "health_body": health, "board": None, "issues": 0, "error": ""}
    token_path = Path(os.environ.get("PAPERCLIP_API_TOKEN_FILE") or ROOT / "runtime/secrets/paperclip_api_token")
    try:
        token = load_api_token_file(token_path) if token_path.exists() else ""
    except (OSError, ValueError) as error:
        view["error"] = str(error)
        return view
    if not token:
        view["board"] = "missing"
        return view
    status, companies = fetch(PAPERCLIP + "/api/companies", token)
    view["board"] = status
    if status != 200 or not isinstance(companies, list) or not companies:
        return view
    company = companies[0]
    company_id = company.get("id")
    status, issues_body = fetch(PAPERCLIP + f"/api/companies/{company_id}/issues?limit=200", token)
    if status != 200:
        view["error"] = f"issues HTTP {status}"
        return view
    issues = issues_body.get("issues") if isinstance(issues_body, dict) else issues_body
    view["issues"] = len(issues) if isinstance(issues, list) else 0
    view["company"] = company.get("name")
    return view


def main() -> int:
    cockpit_status, cockpit = fetch(COCKPIT + "/api/health")
    snapshot_status, snapshot = fetch(COCKPIT + "/api/snapshot")
    product_status, product_health = fetch(API + "/api/health")
    summary_status, summary = fetch(API + "/api/summary")
    web_status, web = fetch(WEB + "/")
    paperclip = paperclip_view()

    components = cockpit.get("components", {}) if isinstance(cockpit, dict) else {}
    agent = components.get("agent", {})
    sandbox = components.get("sandbox", {})
    kpis = snapshot.get("kpis", {}) if isinstance(snapshot, dict) else {}
    product = summary if isinstance(summary, dict) else {}
    web_text = web if isinstance(web, str) else ""
    refund_note = "40.00 refunded" if "40.00 refunded" in web_text else ("refund note missing" if web_status == 200 else "web unavailable")

    print("Æion / ParcelDesk — local status")
    print(f"  cockpit    {COCKPIT}  HTTP {cockpit_status}  agent={agent.get('status', '?')}  sandbox={sandbox.get('status', '?')}")
    print(f"  ledger     {kpis.get('events_total', '?')} events · {len(snapshot.get('missions', [])) if isinstance(snapshot, dict) else '?'} missions · "
          f"{kpis.get('completed', '?')} completed · {kpis.get('failed', '?')} failed")
    print(f"  budget     reported ${float(kpis.get('cost_total', 0)):.6f} · exposure ${float(kpis.get('budget_used', 0)) + float(kpis.get('budget_reconciled', 0)) + float(kpis.get('budget_held', 0)):.6f} · "
          f"remaining ${float(kpis.get('budget_remaining', 0)):.6f} / ${float(kpis.get('budget_total', 0)):.2f}")
    if isinstance(cockpit, dict) and agent.get("detail"):
        print(f"  agent      {agent['detail']}")
    print(f"  paperclip  {PAPERCLIP}  health={paperclip['health']}  board={paperclip['board']}  issues={paperclip['issues']}"
          + (f"  ({paperclip['error']})" if paperclip["error"] else ""))
    print(f"  product    web HTTP {web_status} · api HTTP {product_status} · {product.get('orders', '?')} orders · "
          f"{product.get('refunds', '?')} refunded · {refund_note}")
    print(f"  links      {COCKPIT}/  ·  {WEB}/  ·  {PAPERCLIP}/")
    return 0 if cockpit_status == 200 and product_status == 200 and web_status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
