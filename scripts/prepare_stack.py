#!/usr/bin/env python3
"""Create local service secrets once; never display them or replace existing values."""
import os
from pathlib import Path
import secrets
import stat

def prepare(root: Path) -> None:
    directory = root / "runtime/secrets"
    if directory.parent.is_symlink() or directory.is_symlink():
        raise ValueError("Secret directories cannot be symlinks")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.stat().st_uid != os.getuid():
        raise ValueError("Secret directory must belong to the current operator")
    directory.chmod(0o700)
    for name in ("postgres_password", "paperclip_auth", "paperclip_actions", "gateway_admin"):
        target = directory / name
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            info = target.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError("Existing secret must be a regular owner-only file")
            continue
        with os.fdopen(descriptor, "w") as stream:
            stream.write(secrets.token_hex(32) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    gateway = root / "runtime/gateway"
    if gateway.is_symlink():
        raise ValueError("Gateway state cannot be a symlink")
    gateway.mkdir(mode=0o700, exist_ok=True)
    gateway.chmod(0o700)


if __name__ == "__main__":
    prepare(Path(__file__).resolve().parents[1])
    print("Local service secret files are ready; existing values were preserved.")
