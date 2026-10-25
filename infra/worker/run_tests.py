"""Bounded wire report from the disposable, untrusted test namespace.

Transport, not acceptance authority. The worker copies the read-only checkout to
`/tmp`, runs the suites selected by the admitted profile, and returns every JUnit
case. The controller parses the report and the Gate separately re-runs it.

Profiles:
- ``legacy``: the Python pricing contract only.
- ``product``: the Quarkus/Maven backend and the Deno frontend. The Python pricing
  slice is a separate release unit and is not run here.
"""
import base64
import glob
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

MAX_OUTPUT = 1024 * 1024
MAX_REPORT = 2 * 1024 * 1024
BUILD = Path("/tmp/build")


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


def run_suite(name, argv, *, cwd, env, deadline, log_path):
    """Run one suite, bounded by the remaining deadline, and return its outcome."""
    started = time.monotonic()
    outcome = {"name": name, "exit": 0, "error": None, "duration_ms": 0}
    with open(log_path, "wb") as log:
        try:
            child = subprocess.Popen(
                argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
        except OSError as error:
            outcome.update(exit=127, error=f"{type(error).__name__}: {error}")
            return outcome
        try:
            outcome["exit"] = child.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            outcome.update(exit=124, error="Worker suite exceeded its deadline")
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
        finally:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    outcome["duration_ms"] = int((time.monotonic() - started) * 1000)
    return outcome


def collect(paths):
    suites = []
    for pattern in paths:
        for name in sorted(glob.glob(str(pattern))):
            try:
                root = ET.parse(name).getroot()
            except (ET.ParseError, OSError):
                continue
            if root.tag == "testsuites":
                suites.extend(root.findall("testsuite"))
            elif root.tag == "testsuite":
                suites.append(root)
    return suites


def synthetic(suite, message):
    element = ET.Element("testsuite", {"name": f"aeion.worker.{suite}", "tests": "1", "failures": "1",
                                       "errors": "0", "skipped": "0"})
    case = ET.SubElement(element, "testcase", {"classname": "aeion.worker", "name": suite})
    failure = ET.SubElement(case, "failure", {"message": message[:500]})
    failure.text = message[:2000]
    return element


def main():
    started = time.monotonic()
    timeout = max(0.1, float(sys.argv[1]) - 1)
    deadline = started + timeout
    profile = sys.argv[2] if len(sys.argv) > 2 else "legacy"
    reports = []
    failures = []
    error = None
    exit_code = 0
    summary = []
    try:
        if profile not in {"legacy", "product"}:
            raise ValueError("Unknown test profile")
        source = Path("/work")
        if BUILD.exists():
            shutil.rmtree(BUILD, ignore_errors=True)
        shutil.copytree(
            source, BUILD,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", "target", "node_modules"),
        )

        if profile == "legacy":
            python = run_suite(
                "python",
                [sys.executable, "-m", "pytest", "-q", "--capture=no", "-p", "no:cacheprovider",
                 "--junitxml", "/tmp/python-junit.xml"],
                cwd=BUILD,
                env={"PATH": "/opt/venv/bin:/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "TMPDIR": "/tmp",
                     "LANG": "C.UTF-8", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                     "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
                deadline=deadline, log_path="/tmp/python.log",
            )
            summary.append(python)
            reports.append("/tmp/python-junit.xml")
            if python["exit"] != 0 or not Path("/tmp/python-junit.xml").is_file():
                exit_code = 1
                failures.append(python)

        if profile == "product":
            backend = BUILD / "product/backend"
            frontend = BUILD / "product/frontend"
            maven = run_suite(
                "maven",
                ["mvn", "-o", "-B", "-q", "-Dmaven.repo.local=/opt/m2", "-DskipITs", "test"],
                cwd=backend,
                env={"PATH": "/opt/venv/bin:/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "TMPDIR": "/tmp",
                     "LANG": "C.UTF-8", "MAVEN_OPTS": "-Xmx384m", "JAVA_HOME": "/opt/java/openjdk"},
                deadline=deadline, log_path="/tmp/maven.log",
            )
            summary.append(maven)
            reports.append(str(backend / "target/surefire-reports/TEST-*.xml"))
            if maven["exit"] != 0 or not glob.glob(str(backend / "target/surefire-reports/TEST-*.xml")):
                exit_code = 1
                failures.append(maven)

            deno = run_suite(
                "deno",
                ["deno", "test", "-A", "--cached-only", "--junit-path", "/tmp/deno-junit.xml", "tests/"],
                cwd=frontend,
                env={"PATH": "/opt/venv/bin:/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "TMPDIR": "/tmp",
                     "LANG": "C.UTF-8", "DENO_DIR": "/opt/deno", "NO_COLOR": "1"},
                deadline=deadline, log_path="/tmp/deno.log",
            )
            summary.append(deno)
            reports.append("/tmp/deno-junit.xml")
            if deno["exit"] != 0 or not Path("/tmp/deno-junit.xml").is_file():
                exit_code = 1
                failures.append(deno)

        suites = collect(reports)
        present = {suite.get("name", "") for suite in suites}
        for failure in failures:
            if not any(failure["name"] in name for name in present):
                suites.append(synthetic(failure["name"], failure.get("error")
                                        or f"{failure['name']} exited {failure['exit']} without a report"))
        if not suites:
            suites.append(synthetic("worker", "No suite produced a report"))
            exit_code = 1
        root = ET.Element("testsuites")
        for suite in suites:
            root.append(suite)
        ET.ElementTree(root).write("/tmp/junit.xml", encoding="utf-8", xml_declaration=True)
        output = "\n".join(
            f"{item['name']}: exit {item['exit']} in {item['duration_ms']} ms"
            + (f" ({item['error']})" if item.get("error") else "")
            for item in summary
        ).encode()
        stdout = output[-MAX_OUTPUT:]
        junit = bounded_file("/tmp/junit.xml", MAX_REPORT)
    except (OSError, ValueError) as failure:
        error = str(failure)
        exit_code = 1
        stdout = b""
        root = ET.Element("testsuites")
        root.append(synthetic("worker", error))
        ET.ElementTree(root).write("/tmp/junit.xml", encoding="utf-8", xml_declaration=True)
        junit = bounded_file("/tmp/junit.xml", MAX_REPORT)

    payload = {"schema": "aeion.test-wire/v1", "exit_code": exit_code, "error": error,
               "stdout": base64.b64encode(stdout).decode(),
               "stderr": base64.b64encode(b"").decode(),
               "report": base64.b64encode(junit).decode(),
               "duration_ms": int((time.monotonic() - started) * 1000)}
    print(json.dumps(payload), flush=True)


if __name__ == "__main__":
    main()
