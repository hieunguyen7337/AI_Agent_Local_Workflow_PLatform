"""CLI replay command tests for forked snapshot replay output."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import backend.cli.main as cli_main
from backend.checkpointing.replay import ReplayResult
from backend.runtime.executor import RunResult


def test_replay_uses_optional_input_and_prints_lineage(monkeypatch):
    captured: dict = {}

    def _fake_replay_run(**kwargs):
        captured.update(kwargs)
        return ReplayResult(
            run=RunResult(
                run_id="replay_1",
                graph_name="coder_tester",
                final_state={"tester_verdict": True, "tester_mode": "llm_judge"},
                status="ok",
                error=None,
                cost_usd=0.1,
                latency_ms=12.0,
                run_dir=Path("runs") / "replay_1",
            ),
            source_run_id="source_1",
            replay_run_id="replay_1",
            replay_from_node="coder",
            source_checkpoint_id="cp_123",
        )

    monkeypatch.setattr(cli_main, "replay_run", _fake_replay_run)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.app,
        [
            "replay",
            "source_1",
            "--workflow",
            "coder_tester",
            "--at",
            "coder",
        ],
    )
    assert result.exit_code == 0
    assert captured["user_input"] is None
    payload = json.loads(result.stdout)
    assert payload["source_run_id"] == "source_1"
    assert payload["replay_run_id"] == "replay_1"
    assert payload["replay_from_node"] == "coder"
    assert payload["source_checkpoint_id"] == "cp_123"


def test_replay_cancelled_exits_130(monkeypatch):
    def _fake_replay_run(**kwargs):
        return ReplayResult(
            run=RunResult(
                run_id="replay_1",
                graph_name="coder_tester",
                final_state={"tester_verdict": False, "tester_mode": "llm_judge"},
                status="cancelled",
                error="user_cancelled",
                cost_usd=0.0,
                latency_ms=1.0,
                run_dir=Path("runs") / "replay_1",
            ),
            source_run_id="source_1",
            replay_run_id="replay_1",
            replay_from_node="coder",
            source_checkpoint_id="cp_123",
        )

    monkeypatch.setattr(cli_main, "replay_run", _fake_replay_run)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.app,
        ["replay", "source_1", "--workflow", "coder_tester", "--at", "coder"],
    )
    assert result.exit_code == 130
