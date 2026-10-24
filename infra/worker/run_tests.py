"""Bounded wire report from the disposable, untrusted test namespace.

This is transport, not acceptance authority. The controller parses the report and
separately owns the acceptance checks and Gate.
"""
import base64
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time

MAX_OUTPUT = 1024 * 1024
MAX_REPORT = 2 * 1024 * 1024


def bounded_file(path, limit):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Worker output/report exceeds its regular-file bound")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Worker output/report grew beyond its bound")
        return data


def main():
    started = time.monotonic()
    report = Path("/tmp/junit.xml")
    error, exit_code, stdout, stderr, junit = None, None, b"", b"", b""
    environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "TMPDIR": "/tmp",
                   "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    try:
        with open("/tmp/stdout", "wb") as out, open("/tmp/stderr", "wb") as err:
            child = subprocess.Popen([sys.executable, "-m", "pytest", "-q", "--capture=no", "-p", "no:cacheprovider",
                                      "--junitxml", str(report)], cwd="/work", env=environment,
                                     stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                     close_fds=True, start_new_session=True)
            try:
                exit_code = child.wait(timeout=max(.1, float(sys.argv[1]) - 1))
            except subprocess.TimeoutExpired:
                error = "Worker pytest exceeded its deadline"
            finally:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
        stdout, stderr = bounded_file("/tmp/stdout", MAX_OUTPUT), bounded_file("/tmp/stderr", MAX_OUTPUT)
        if len(stdout) + len(stderr) > MAX_OUTPUT:
            raise ValueError("Combined worker output exceeds 1 MiB")
        if error is None:
            junit = bounded_file(report, MAX_REPORT)
    except (OSError, ValueError) as failure:
        error = str(failure)
    payload = {"schema": "aeion.test-wire/v1", "exit_code": exit_code, "error": error,
               "stdout": base64.b64encode(stdout).decode(), "stderr": base64.b64encode(stderr).decode(),
               "report": base64.b64encode(junit).decode(), "duration_ms": int((time.monotonic() - started) * 1000)}
    print(json.dumps(payload), flush=True)


if __name__ == "__main__":
    main()
