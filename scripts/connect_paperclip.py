#!/usr/bin/env python3
"""Connect the trusted Factory controller to Paperclip; never print a credential."""
import json
from pathlib import Path
import sys
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.paperclip_auth import connect

try:
    def announce(url):
        if not webbrowser.open(url):
            raise RuntimeError("Could not open local authorization page")
        print(json.dumps({"awaiting_browser_approval": True, "client": "AEion Factory"}), flush=True)
    result = connect("http://127.0.0.1:3101", announce=announce)
    print(json.dumps(result), flush=True)
except (RuntimeError, ValueError, OSError):
    print("Paperclip connection was not completed; no credential was displayed.", file=sys.stderr)
    raise SystemExit(1)
