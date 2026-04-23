"""Sandbox tester execution path unit tests."""
from __future__ import annotations

from backend.runtime.nodes.tester import run_sandbox_python


def test_sandbox_pass_case():
    result = run_sandbox_python(
        candidate_code="def add(a, b):\n    return a + b\n",
        test_code="assert add(1, 2) == 3",
        timeout_s=1.0,
        max_output_bytes=4096,
        memory_limit_mb=256,
    )
    assert result.verdict is True
    assert result.status == "PASS"


def test_sandbox_fail_case():
    result = run_sandbox_python(
        candidate_code="def add(a, b):\n    return a + b\n",
        test_code="assert add(1, 2) == 5",
        timeout_s=1.0,
        max_output_bytes=4096,
        memory_limit_mb=256,
    )
    assert result.verdict is False
    assert result.status == "FAIL"


def test_sandbox_runtime_error_case():
    result = run_sandbox_python(
        candidate_code="def boom():\n    raise ValueError('x')\nboom()",
        test_code="assert True",
        timeout_s=1.0,
        max_output_bytes=4096,
        memory_limit_mb=256,
    )
    assert result.verdict is False
    assert result.status == "ERROR"


def test_sandbox_timeout_case():
    result = run_sandbox_python(
        candidate_code="import time\ntime.sleep(1.0)",
        test_code="assert True",
        timeout_s=0.05,
        max_output_bytes=4096,
        memory_limit_mb=256,
    )
    assert result.verdict is False
    assert result.status == "TIMEOUT"


def test_sandbox_output_truncation():
    result = run_sandbox_python(
        candidate_code="print('x' * 5000)\ndef ok():\n    return True\n",
        test_code="assert ok() is True",
        timeout_s=1.0,
        max_output_bytes=200,
        memory_limit_mb=256,
    )
    assert result.truncated is True
    assert len(result.stdout.encode("utf-8")) <= 200


def test_sandbox_accepts_bom_prefixed_test_code():
    result = run_sandbox_python(
        candidate_code="def add(a, b):\n    return a + b\n",
        test_code="\ufeffassert add(1, 2) == 3",
        timeout_s=1.0,
        max_output_bytes=4096,
        memory_limit_mb=256,
    )
    assert result.verdict is True
    assert result.status == "PASS"
