#!/usr/bin/env python3
"""Operate the local Compose stack without reading/displaying provider credentials."""
import argparse
import os
from pathlib import Path
import stat
import subprocess

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--provider-auth", type=Path, help="Explicit existing OpenCode API auth store; mounted only into the trusted gateway")
parser.add_argument("command", choices=("up", "down", "ps", "restart"))
args = parser.parse_args()
if os.getuid() == 0:
    raise SystemExit("Run as the local non-root operator")
env = {**os.environ, "AEION_OPERATOR_UID": str(os.getuid()), "AEION_OPERATOR_GID": str(os.getgid())}
command = ["docker", "compose", "-f", "compose.yaml"]
if args.provider_auth:
    source = args.provider_auth.absolute()
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise SystemExit("Provider store must be a regular operator-owned private file")
    env["OPENCODE_AUTH_SOURCE"] = str(source)
    command += ["-f", "compose.provider.yaml"]
command += [args.command]
if args.command == "up":
    command += ["-d", "--wait", "--wait-timeout", "180"]
raise SystemExit(subprocess.run(command, cwd=root, env=env).returncode)
