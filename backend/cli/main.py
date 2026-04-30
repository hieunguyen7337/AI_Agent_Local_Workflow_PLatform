"""Typer CLI: run, replay, eval, export-mermaid, serve."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal

import typer
from dotenv import load_dotenv

from backend.checkpointing.replay import parse_set_arg, replay as replay_run
from backend.evals.dataset import run_dataset_eval
from backend.evals.harness import run_eval
from backend.graphspec import load_workflow_metadata
from backend.runtime.cancellation import CancellationController
from backend.runtime.executor import run_graph
from backend.runtime.functions import DEFAULT_MAX_CONCURRENCY, run_workflow_function

app = typer.Typer(add_completion=False, no_args_is_help=True)

load_dotenv()


def _load_workflow_metadata(workflow: str):
    return load_workflow_metadata(workflow)


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
    workflow: str = typer.Argument(..., help="workflow id under workflows/*.yaml"),
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
    cancellation = CancellationController()
    initial_overrides = None
    if test_code_file is not None:
        initial_overrides = {"_test_code": test_code_file.read_text(encoding="utf-8")}
    with _CancelHandler(cancellation):
        input_state = {"user_input": input, **(initial_overrides or {})}
        result = run_workflow_function(
            workflow,
            input_state,
            expected=expected,
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
    workflow: str = typer.Argument(..., help="workflow id under workflows/*.yaml"),
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
    max_concurrency: int = typer.Option(
        DEFAULT_MAX_CONCURRENCY,
        "--max-concurrency",
        min=1,
        help="parallel workflow calls for fixture evals",
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
            max_concurrency=max_concurrency,
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


@app.command("eval-dataset")
def eval_dataset(
    workflow: str = typer.Argument(..., help="workflow id under workflows/*.yaml"),
    config: Path = typer.Option(
        ...,
        "--config",
        "-c",
        help="dataset eval config YAML, usually evals/<workflow>/dataset_eval.yaml",
    ),
    max_concurrency: int | None = typer.Option(
        None,
        "--max-concurrency",
        min=1,
        help="override dataset_eval.yaml max_concurrency",
    ),
) -> None:
    """Run a generalized dataset eval against a workflow."""
    cancellation = CancellationController()
    with _CancelHandler(cancellation):
        out = run_dataset_eval(
            workflow=workflow,
            config_path=config,
            max_concurrency=max_concurrency,
            cancellation=cancellation,
        )
    typer.echo(json.dumps({"status": out.get("status", "ok")}, indent=2))
    typer.echo(json.dumps(out["overall"], indent=2))
    typer.echo(f"Full results written to {out['output_path']}")
    if out.get("status") == "cancelled":
        raise typer.Exit(code=130)


@app.command("export-mermaid")
def export_mermaid(
    workflow: str = typer.Argument(..., help="workflow id under workflows/*.yaml"),
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
