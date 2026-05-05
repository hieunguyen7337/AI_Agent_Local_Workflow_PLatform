#!/usr/bin/env python3
"""Live stress run for Claude Code YAML-style workflows (real LLM + tools).

Runs with **current working directory** = ``evals/claude_code_yaml_workflow/workspace/`` so
``read_file`` / ``write_file`` apply to the greenfield sandbox (not the monorepo root).

Artifacts for workflows whose ids start with ``claude_code`` are written under
``evals/claude_code_yaml_workflow/runs/workflows/<workflow>/<date>/<run_id>/`` (override with
env ``CLAUDE_CODE_RUNS_ROOT``). Other workflows still use ``runs/``.

Uses ``run_workflow_function`` with optional OpenRouter keys from the repo ``.env``.

The CI workflow YAML caps the agent loop at ``max_iterations`` on the loop edge (see workflow file);
LangGraph also needs ``--recursion-limit`` large enough (many nodes run per tool turn).

Default: ``claude_code_yaml_workflow_ci`` (skips human ``plan_approval``). For the canonical
workflow with approvals bypassed for automation, pass ``--workflow claude_code_yaml_workflow
--approve-all``.

Run from repository root::

    .\\.venv\\Scripts\\python evals/claude_code_yaml_workflow/run_stress.py
    .\\.venv\\Scripts\\python evals/claude_code_yaml_workflow/run_stress.py -i "Your task..."
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EVAL_DIR = Path(__file__).resolve().parent
_WORKSPACE = _EVAL_DIR / "workspace"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_TASK = (
    "Work ONLY in this workspace (current directory). Implement the greenfield EventLedger project "
    "exactly as specified in AGENTS.md (that spec is already included in assembled context). "
    "Create the required files under src/event_ledger/ and tests/ using write_file; optionally run "
    "`python -m pytest tests/ -q` via shell when appropriate. End with final_answer summarizing "
    "files created, key APIs, and test results or blockers."
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-w",
        "--workflow",
        default="claude_code_yaml_workflow_ci",
        help="Workflow id under workflows/*.yaml (default: CI variant without plan approval).",
    )
    parser.add_argument(
        "-i",
        "--input",
        dest="user_input",
        default=DEFAULT_TASK,
        help="User task string for the agent.",
    )
    parser.add_argument(
        "--approve-all",
        action="store_true",
        help="Pre-seed plan_approval_decision and tool_approval_decision (canonical workflow only).",
    )
    parser.add_argument(
        "--recursion-limit",
        type=int,
        default=448,
        help=(
            "LangGraph recursion_limit (default 448). Each agent turn walks many nodes "
            "(model, parser, permission, tools, digest, gates), so this must exceed the "
            "effective steps for multi-file write + test runs."
        ),
    )
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
    except ImportError:
        load_dotenv = None  # type: ignore[assignment,misc]

    if not _WORKSPACE.is_dir():
        print(json.dumps({"error": f"workspace missing: {_WORKSPACE}"}), file=sys.stderr)
        return 2

    os.chdir(_WORKSPACE)
    if load_dotenv is not None:
        load_dotenv(_REPO_ROOT / ".env")

    from backend.runtime.functions import run_workflow_function

    state: dict = {"user_input": args.user_input}
    if args.approve_all:
        state["plan_approval_decision"] = "approved"
        state["tool_approval_decision"] = "approved"

    result = run_workflow_function(
        args.workflow,
        state,
        recursion_limit=args.recursion_limit,
        workflow_specs_root=_REPO_ROOT / "workflows",
    )

    fa = result.final_state.get("final_answer")
    out = {
        "workspace": str(_WORKSPACE.resolve()),
        "workflow": args.workflow,
        "status": result.status,
        "error": result.error,
        "run_id": result.run_id,
        "run_dir": str(result.run_dir),
        "cost_usd": result.cost_usd,
        "latency_ms": result.latency_ms,
        "final_answer_preview": (str(fa)[:2000] if fa else None),
        "pending_approval": result.final_state.get("pending_approval"),
    }
    print(json.dumps(out, indent=2))
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
