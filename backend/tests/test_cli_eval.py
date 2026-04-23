"""CLI eval defaults and regression-exit behavior."""
from __future__ import annotations

from typer.testing import CliRunner

import backend.cli.main as cli_main


def test_eval_default_n_is_4(monkeypatch):
    captured: dict = {}

    def _fake_run_eval(**kwargs):
        captured.update(kwargs)
        return {
            "status": "ok",
            "overall": {},
            "overall_ci": {},
            "baseline_comparison": {},
            "regression_detected": False,
        }

    monkeypatch.setattr(cli_main, "run_eval", _fake_run_eval)
    runner = CliRunner()
    result = runner.invoke(cli_main.app, ["eval", "coder_tester"])
    assert result.exit_code == 0
    assert captured["n_per_fixture"] == 4


def test_eval_fail_on_regression_exits_non_zero(monkeypatch):
    def _fake_run_eval(**kwargs):
        return {
            "status": "ok",
            "overall": {},
            "overall_ci": {},
            "baseline_comparison": {},
            "regression_detected": True,
        }

    monkeypatch.setattr(cli_main, "run_eval", _fake_run_eval)
    runner = CliRunner()
    result = runner.invoke(cli_main.app, ["eval", "coder_tester", "--fail-on-regression"])
    assert result.exit_code == 2


def test_eval_cancelled_exits_130(monkeypatch):
    def _fake_run_eval(**kwargs):
        return {
            "status": "cancelled",
            "overall": {},
            "overall_ci": {},
            "baseline_comparison": {"status": "cancelled"},
            "regression_detected": False,
        }

    monkeypatch.setattr(cli_main, "run_eval", _fake_run_eval)
    runner = CliRunner()
    result = runner.invoke(cli_main.app, ["eval", "coder_tester"])
    assert result.exit_code == 130
