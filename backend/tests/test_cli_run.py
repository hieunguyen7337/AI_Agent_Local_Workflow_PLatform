"""CLI run command tests for test-code injection and tester mode output."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import backend.cli.main as cli_main
from backend.runtime.executor import RunResult


def test_run_passes_test_code_file_and_prints_tester_mode(monkeypatch, tmp_path: Path):
    captured: dict = {}
    test_file = tmp_path / "tests.py"
    test_file.write_text("assert add(1,2)==3", encoding="utf-8")

    def _fake_load(workflow: str):
        return object()

    def _fake_run_graph(metadata, **kwargs):
        captured.update(kwargs)
        return RunResult(
            run_id="r1",
            graph_name="g",
            final_state={"tester_verdict": True, "tester_mode": "sandbox"},
            status="ok",
            error=None,
            cost_usd=0.0,
            latency_ms=1.0,
            run_dir=Path("runs") / "r1",
        )

    monkeypatch.setattr(cli_main, "_load_workflow_metadata", _fake_load)
    monkeypatch.setattr(cli_main, "run_graph", _fake_run_graph)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.app,
        [
            "run",
            "coder_tester",
            "--input",
            "write add",
            "--test-code-file",
            str(test_file),
        ],
    )
    assert result.exit_code == 0
    assert captured["initial_state_overrides"]["_test_code"] == "assert add(1,2)==3"
    payload = json.loads(result.stdout)
    assert payload["tester_mode"] == "sandbox"


def test_run_cancelled_exits_130(monkeypatch):
    def _fake_load(workflow: str):
        return object()

    def _fake_run_graph(metadata, **kwargs):
        return RunResult(
            run_id="r1",
            graph_name="g",
            final_state={"tester_verdict": False, "tester_mode": "llm_judge"},
            status="cancelled",
            error="user_cancelled",
            cost_usd=0.0,
            latency_ms=1.0,
            run_dir=Path("runs") / "r1",
        )

    monkeypatch.setattr(cli_main, "_load_workflow_metadata", _fake_load)
    monkeypatch.setattr(cli_main, "run_graph", _fake_run_graph)

    runner = CliRunner()
    result = runner.invoke(cli_main.app, ["run", "coder_tester", "--input", "write add"])
    assert result.exit_code == 130
