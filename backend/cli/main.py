"""Typer CLI: run, replay, eval, export-mermaid, serve."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import signal

import typer
from dotenv import load_dotenv

from backend.checkpointing.replay import parse_set_arg, replay as replay_run
from backend.evals.harness import run_eval
from backend.runtime.cancellation import CancellationController
from backend.runtime.executor import run_graph

app = typer.Typer(add_completion=False, no_args_is_help=True)

load_dotenv()


def _load_workflow_metadata(workflow: str):
    module = importlib.import_module(f"backend.workflows.{workflow}")
    return module.build_compiled()


class _CancelHandler:
    def __init__(self, controller: CancellationController):
        self._controller = controller
        self._count = 0
        self._previous = None

    def __enter__(self):
        self._previous = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._handle)
        return self

    def __exit__(self, exc_type, exc, tb):
        signal.signal(signal.SIGINT, self._previous)
        return False

    def _handle(self, signum, frame):
        self._count += 1
        if self._count == 1:
            self._controller.cancel()
            typer.echo("Cancellation requested. Press Ctrl+C again to exit immediately.", err=True)
            return
        os._exit(130)


@app.command()
def run(
    workflow: str = typer.Argument(..., help="workflow module name under backend.workflows"),
    input: str = typer.Option(..., "--input", "-i", help="user input for the workflow"),
    expected: str = typer.Option(
        "The code should satisfy the task with correct, well-tested logic.",
        "--expected",
        help="expected outcome (consumed by tester-judge)",
    ),
    test_code_file: Path | None = typer.Option(
        None,
        "--test-code-file",
        help="path to Python assertions/script used by tester sandbox",
    ),
) -> None:
    """Run a workflow once."""
    metadata = _load_workflow_metadata(workflow)
    cancellation = CancellationController()
    initial_overrides = None
    if test_code_file is not None:
        initial_overrides = {"_test_code": test_code_file.read_text(encoding="utf-8")}
    with _CancelHandler(cancellation):
        result = run_graph(
            metadata,
            user_input=input,
            expected=expected,
            initial_state_overrides=initial_overrides,
            cancellation=cancellation,
        )
    typer.echo(
        json.dumps(
            {
                "run_id": result.run_id,
                "status": result.status,
                "error": result.error,
                "cost_usd": result.cost_usd,
                "latency_ms": result.latency_ms,
                "tester_verdict": result.final_state.get("tester_verdict"),
                "tester_mode": result.final_state.get("tester_mode"),
                "run_dir": str(result.run_dir),
            },
            indent=2,
        )
    )
    if result.status == "cancelled":
        raise typer.Exit(code=130)


@app.command()
def replay(
    run_id: str = typer.Argument(..., help="existing run id"),
    workflow: str = typer.Option(..., "--workflow", "-w"),
    input: str | None = typer.Option(None, "--input", "-i"),
    expected: str | None = typer.Option(None, "--expected"),
    at: str | None = typer.Option(None, "--at", help="node to replay from"),
    set_: list[str] = typer.Option([], "--set", help="node.field=value overrides"),
) -> None:
    """Replay an existing run with optional node-config overrides."""
    overrides = parse_set_arg(set_) if set_ else {}
    cancellation = CancellationController()
    with _CancelHandler(cancellation):
        result = replay_run(
            workflow=workflow,
            run_id=run_id,
            user_input=input,
            expected=expected,
            at=at,
            overrides=overrides,
            cancellation=cancellation,
        )
    typer.echo(
        json.dumps(
            {
                "source_run_id": result.source_run_id,
                "replay_run_id": result.replay_run_id,
                "run_id": result.replay_run_id,
                "status": result.status,
                "error": result.error,
                "cost_usd": result.cost_usd,
                "latency_ms": result.latency_ms,
                "tester_verdict": result.final_state.get("tester_verdict"),
                "tester_mode": result.final_state.get("tester_mode"),
                "source_checkpoint_id": result.source_checkpoint_id,
                "replay_from_node": result.replay_from_node,
                "run_dir": str(result.run_dir),
                "overrides": overrides,
            },
            indent=2,
        )
    )
    if result.status == "cancelled":
        raise typer.Exit(code=130)


@app.command()
def eval(
    workflow: str = typer.Argument(..., help="workflow module name"),
    n: int = typer.Option(4, "--n", help="runs per fixture"),
    baseline_path: str | None = typer.Option(
        None,
        "--baseline-path",
        help="override baseline JSON path (default: evals/<workflow>/baseline.json)",
    ),
    update_baseline: bool = typer.Option(
        False,
        "--update-baseline",
        help="write current summary as baseline before comparison",
    ),
    fail_on_regression: bool = typer.Option(
        False,
        "--fail-on-regression",
        help="exit non-zero if regression is detected versus baseline",
    ),
) -> None:
    """Run an eval harness against a workflow's fixtures."""
    cancellation = CancellationController()
    with _CancelHandler(cancellation):
        out = run_eval(
            workflow=workflow,
            n_per_fixture=n,
            baseline_path=Path(baseline_path) if baseline_path else None,
            update_baseline=update_baseline,
            cancellation=cancellation,
        )
    typer.echo(json.dumps({"status": out.get("status", "ok")}, indent=2))
    typer.echo(json.dumps(out["overall"], indent=2))
    typer.echo(json.dumps(out.get("overall_ci", {}), indent=2))
    typer.echo(json.dumps(out.get("baseline_comparison", {}), indent=2))
    typer.echo(f"Full results written to runs/eval_{workflow}.json")
    if out.get("status") == "cancelled":
        raise typer.Exit(code=130)
    if fail_on_regression and out.get("regression_detected"):
        raise typer.Exit(code=2)


@app.command("export-mermaid")
def export_mermaid(
    workflow: str = typer.Argument(..., help="workflow module name"),
) -> None:
    """Print a Mermaid flowchart for a workflow."""
    metadata = _load_workflow_metadata(workflow)
    typer.echo(metadata.mermaid())


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    """Start the FastAPI read-only server."""
    import uvicorn

    uvicorn.run("backend.server.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
