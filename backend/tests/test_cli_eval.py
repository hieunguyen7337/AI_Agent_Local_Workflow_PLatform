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
    assert captured["max_concurrency"] == 50


def test_eval_max_concurrency_passes_through(monkeypatch):
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
    result = runner.invoke(cli_main.app, ["eval", "coder_tester", "--max-concurrency", "3"])
    assert result.exit_code == 0
    assert captured["max_concurrency"] == 3


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


def test_eval_dataset_command_uses_config(monkeypatch, tmp_path):
    captured: dict = {}
    config_path = tmp_path / "dataset_eval.yaml"
    config_path.write_text("dataset_path: data.yaml\n", encoding="utf-8")

    def _fake_run_dataset_eval(**kwargs):
        captured.update(kwargs)
        return {
            "status": "ok",
            "overall": {},
            "output_path": "runs/dataset_eval_demo/eval.json",
        }

    monkeypatch.setattr(cli_main, "run_dataset_eval", _fake_run_dataset_eval)
    runner = CliRunner()
    result = runner.invoke(
        cli_main.app,
        ["eval-dataset", "coder_tester", "--config", str(config_path)],
    )
    assert result.exit_code == 0
    assert captured["workflow"] == "coder_tester"
    assert captured["config_path"] == config_path
    assert captured["max_concurrency"] is None
