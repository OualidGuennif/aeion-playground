#!/usr/bin/env python3
"""Connect the trusted Factory controller to Paperclip; never print a credential."""
import json
from pathlib import Path
import sys
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.paperclip_auth import connect, export_plain_token

try:
    def announce(url):
        if not webbrowser.open(url):
            raise RuntimeError("Could not open local authorization page")
        print(json.dumps({"awaiting_browser_approval": True, "client": "AEion Factory"}), flush=True)
    result = connect("http://127.0.0.1:3101", announce=announce)
    if result.get("connected"):
        path = export_plain_token(
            "http://127.0.0.1:3101",
            ROOT / "runtime/secrets/paperclip_api_token",
        )
        result = {**result, "container_token_file": str(path)}
    print(json.dumps(result), flush=True)
except (RuntimeError, ValueError, OSError):
    print("Paperclip connection was not completed; no credential was displayed.", file=sys.stderr)
    raise SystemExit(1)
