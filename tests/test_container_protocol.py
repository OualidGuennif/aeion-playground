"""Untrusted Docker transport never supplies a partial successful result."""
import base64
import json
import subprocess

import pytest

from app.container_executor import WorkerReportError, decode_result


def wire(**changes):
    value = {"schema": "aeion.test-wire/v1", "exit_code": 0, "error": None,
             "stdout": "", "stderr": "", "duration_ms": 1,
             "report": base64.b64encode(b'<testsuite><testcase name="case"/></testsuite>').decode()}
    value.update(changes)
    return json.dumps(value)


@pytest.mark.parametrize("text", [
    "[]", "not JSON", wire(exit_code=True), wire(report="not!base64"),
    wire(error="Worker pytest exceeded its deadline"), wire()[:-1] + ',"exit_code":1}',
])
def test_bad_transport_cannot_publish_junit(tmp_path, text):
    report = tmp_path / "junit.xml"
    with pytest.raises(WorkerReportError):
        decode_result(subprocess.CompletedProcess(["docker"], 0, text, ""), report, output_limit=1024, report_limit=4096)
    assert not report.exists()


def test_container_runtime_failure_is_distinct_from_missing_backend(tmp_path):
    for code, failure in ((125, OSError), (137, WorkerReportError)):
        with pytest.raises(failure):
            decode_result(subprocess.CompletedProcess(["docker"], code, "", ""), tmp_path/"junit.xml", output_limit=1024, report_limit=4096)
