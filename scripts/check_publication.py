#!/usr/bin/env python3
"""Reject private/generated state and symlinks from the public Git index."""
from pathlib import PurePosixPath
import subprocess


def violation(mode: str, name: str) -> bool:
    path = PurePosixPath(name)
    blocked = {"runtime", "references", "secrets", ".venv", "node_modules", "__pycache__"}
    vendored_public_asset = path.parts[:3] == ("app", "static", "vendor")
    return (
        mode not in {"100644", "100755"}
        or "vendor" in path.parts and not vendored_public_asset
        or bool(set(path.parts) & blocked)
        or path.name == "auth.json"
        or path.suffix in {".db", ".pem", ".key", ".pyc"}
        or path.name.startswith(".env") and name != ".env.example"
        or name == "config/model-routing.json"
    )


def main() -> int:
    index = subprocess.run(["git", "ls-files", "--stage", "-z"], check=True, capture_output=True).stdout
    rejected = []
    for entry in index.split(b"\0"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        mode, _, stage = metadata.decode().split()
        name = raw_name.decode("utf-8")
        if stage != "0" or violation(mode, name):
            rejected.append(name)
    if rejected:
        print("Public index rejected: " + ", ".join(rejected))
        return 1
    print("Public index excludes private state, local authorization and symlinks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
