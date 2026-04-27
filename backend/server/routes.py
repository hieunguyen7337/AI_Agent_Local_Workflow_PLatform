"""FastAPI routes for topology, specs, proposals, and runs."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError

from backend.approvals import (
    ApprovalAlreadyDecidedError,
    ApprovalDecisionError,
    ApprovalNotFoundError,
    decide_approval,
)
from backend.builder.api import GraphMetadata
from backend.builder.nodes import ApprovalNodeConfig, GateNodeConfig, RouterNodeConfig, SubgraphNodeConfig
from backend.evals.harness import EVALS_ROOT, run_eval_for_metadata
from backend.graphspec import (
    GraphSpec,
    apply_graph_spec_proposal,
    copy_workflow_template,
    graph_spec_to_metadata,
    list_workflow_ids,
    load_graph_spec,
    load_graph_spec_source,
    load_workflow_metadata,
    list_rollback_snapshots,
    optimize_proposals,
    preview_rollback_snapshot,
    propose_mutation,
    restore_rollback_snapshot,
)
from backend.graphspec.loader import WORKFLOW_SPECS_ROOT
from backend.providers.base import ProviderError
from backend.runtime.artifacts import artifact_paths, iter_run_dirs, resolve_run_dir
from backend.runtime.executor import run_graph
from backend.server.node_metrics import compute_node_metrics

router = APIRouter()

RUNS_ROOT = Path("runs")
SPEC_AUDIT_ROOT = RUNS_ROOT / "spec_audit"


class MutationProposalRequest(BaseModel):
    goal: str = Field(min_length=1)
    constraints: str | None = None
    max_proposals: int = Field(1, ge=1, le=1)


class ProposalEvaluationRequest(BaseModel):
    proposed_yaml: str = Field(min_length=1)
    n_per_fixture: int = Field(1, ge=1, le=8)
    max_cost_usd: float | None = Field(2.0, gt=0)


class ApplyProposalRequest(BaseModel):
    proposed_yaml: str = Field(min_length=1)
    proposal_summary: str | None = None
    evaluation_artifact: str | None = None
    accepted_by: str | None = None


class OptimizeProposalsRequest(BaseModel):
    goal: str = Field(min_length=1)
    constraints: str | None = None
    candidate_count: int = Field(3, ge=2, le=5)
    n_per_fixture: int = Field(1, ge=1, le=8)
    max_cost_usd: float = Field(5.0, gt=0)


class RollbackRestoreRequest(BaseModel):
    accepted_by: str | None = None


class StartRunRequest(BaseModel):
    input: str = Field(min_length=1)
    expected: str | None = None


class ApprovalDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    reviewer: str | None = None
    comment: str | None = None


class CopyTemplateRequest(BaseModel):
    new_workflow_id: str = Field(min_length=1)
    name: str | None = None
    description: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    accepted_by: str | None = None


def load_workflow(name: str) -> GraphMetadata:
    try:
        return load_workflow_metadata(name, specs_root=WORKFLOW_SPECS_ROOT)
    except FileNotFoundError:
        raise HTTPException(404, f"workflow {name!r} not found")


def _topology_dict(metadata: GraphMetadata) -> dict:
    nodes = []
    for nid, cfg in metadata.nodes.items():
        nodes.append(
            {
                "id": nid,
                "kind": cfg.kind,
                "name": cfg.name or nid,
                "description": cfg.description,
                "metadata": cfg.model_dump(),
            }
        )
    edges: list[dict] = []
    for s, t in metadata.edges:
        # Skip edges from conditional nodes — they are modeled separately.
        src_cfg = metadata.nodes.get(s)
        if isinstance(src_cfg, (ApprovalNodeConfig, GateNodeConfig, RouterNodeConfig)):
            continue
        edges.append({"source": s, "target": t, "kind": "normal"})
    for nid, cfg in metadata.nodes.items():
        if isinstance(cfg, GateNodeConfig):
            edges.append(
                {"source": nid, "target": cfg.pass_target, "kind": "conditional", "label": "pass"}
            )
            edges.append(
                {"source": nid, "target": cfg.fail_target, "kind": "conditional", "label": "fail"}
            )
        if isinstance(cfg, RouterNodeConfig):
            for label, target in cfg.routes.items():
                edges.append(
                    {"source": nid, "target": target, "kind": "conditional", "label": label}
                )
            if cfg.default_target is not None:
                edges.append(
                    {
                        "source": nid,
                        "target": cfg.default_target,
                        "kind": "conditional",
                        "label": "default",
                    }
                )
        if isinstance(cfg, ApprovalNodeConfig):
            edges.append(
                {
                    "source": nid,
                    "target": cfg.approved_target,
                    "kind": "conditional",
                    "label": "approved",
                }
            )
            edges.append(
                {
                    "source": nid,
                    "target": cfg.rejected_target,
                    "kind": "conditional",
                    "label": "rejected",
                }
            )
    loops = [
        {
            "loop_id": lp.loop_id,
            "from": lp.back_edge_from,
            "to": lp.back_edge_to,
            "max_iterations": lp.max_iterations,
        }
        for lp in metadata.loops
    ]
    return {
        "name": metadata.name,
        "entry": metadata.entry,
        "cost_budget_usd": metadata.cost_budget_usd,
        "latency_budget_ms": metadata.latency_budget_ms,
        "nodes": nodes,
        "edges": edges,
        "loops": loops,
    }


@router.get("/api/workflows")
def list_workflows() -> list[dict]:
    ids = list_workflow_ids(specs_root=WORKFLOW_SPECS_ROOT)
    result = []
    for wid in ids:
        source_path = WORKFLOW_SPECS_ROOT / f"{wid}.yaml"
        try:
            spec, _yaml_text, source_path = load_graph_spec_source(
                wid,
                specs_root=WORKFLOW_SPECS_ROOT,
            )
            result.append(
                {
                    "id": wid,
                    "name": spec.name,
                    "description": spec.description,
                    "category": spec.category,
                    "tags": spec.tags,
                    "template": spec.template,
                    "validation_status": "valid",
                    "validation_errors": [],
                    "source_path": source_path.as_posix(),
                    "facts": _workflow_facts(spec),
                }
            )
        except Exception as exc:
            result.append(
                {
                    "id": wid,
                    "name": wid,
                    "description": "",
                    "category": "general",
                    "tags": [],
                    "template": False,
                    "validation_status": "invalid",
                    "validation_errors": [str(exc)],
                    "source_path": source_path.as_posix(),
                    "facts": _empty_workflow_facts(),
                }
            )
    return result


@router.post("/api/workflows/{workflow}/copy-template")
def post_copy_workflow_template(workflow: str, request: CopyTemplateRequest) -> dict:
    try:
        copied = copy_workflow_template(
            workflow=workflow,
            new_workflow_id=request.new_workflow_id,
            name=request.name,
            description=request.description,
            category=request.category,
            tags=request.tags,
            accepted_by=request.accepted_by,
            specs_root=WORKFLOW_SPECS_ROOT,
            audit_root=SPEC_AUDIT_ROOT,
        )
    except FileNotFoundError:
        raise HTTPException(404, f"workflow template {workflow!r} not found")
    except FileExistsError as exc:
        raise HTTPException(409, str(exc))
    except (ValueError, ValidationError, yaml.YAMLError) as exc:
        raise HTTPException(400, str(exc))
    except OSError as exc:
        raise HTTPException(500, f"failed to copy workflow template: {exc}")
    return {
        "workflow": copied.workflow,
        "source_template": copied.source_template,
        "status": copied.status,
        "source_path": copied.source_path.as_posix(),
        "audit_path": copied.audit_path.as_posix(),
        "spec": copied.spec.model_dump(mode="json"),
        "yaml": copied.yaml_text,
    }


def _empty_workflow_facts() -> dict[str, int]:
    return {
        "node_count": 0,
        "edge_count": 0,
        "loop_count": 0,
        "approval_node_count": 0,
        "subgraph_node_count": 0,
    }


def _workflow_facts(spec: GraphSpec) -> dict[str, int]:
    return {
        "node_count": len(spec.nodes),
        "edge_count": len(spec.edges),
        "loop_count": len(spec.loops),
        "approval_node_count": sum(isinstance(node, ApprovalNodeConfig) for node in spec.nodes),
        "subgraph_node_count": sum(isinstance(node, SubgraphNodeConfig) for node in spec.nodes),
    }


@router.get("/api/graph/{workflow}")
def get_graph(workflow: str) -> dict:
    metadata = load_workflow(workflow)
    return _topology_dict(metadata)


@router.get("/api/spec/{workflow}")
def get_spec(workflow: str) -> dict:
    try:
        spec, yaml_text, source_path = load_graph_spec_source(workflow, specs_root=WORKFLOW_SPECS_ROOT)
    except FileNotFoundError:
        raise HTTPException(404, f"workflow spec {workflow!r} not found")
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, f"workflow spec {workflow!r} is invalid: {exc}")
    return {
        "workflow": workflow,
        "spec": spec.model_dump(mode="json"),
        "yaml": yaml_text,
        "source_path": source_path.as_posix(),
    }


@router.post("/api/spec/{workflow}/propose-mutation")
def post_spec_mutation(workflow: str, request: MutationProposalRequest) -> dict:
    try:
        proposal = propose_mutation(
            workflow=workflow,
            goal=request.goal,
            constraints=request.constraints,
            max_proposals=request.max_proposals,
        )
    except FileNotFoundError:
        raise HTTPException(404, f"workflow spec {workflow!r} not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except ProviderError as exc:
        raise HTTPException(503, f"mutation provider error: {exc}")
    return {
        "workflow": proposal.workflow,
        "status": proposal.status,
        "summary": proposal.summary,
        "original_yaml": proposal.original_yaml,
        "proposed_yaml": proposal.proposed_yaml,
        "diff": proposal.diff,
        "validation_errors": proposal.validation_errors,
    }


@router.post("/api/spec/{workflow}/apply-proposal")
def post_spec_apply(workflow: str, request: ApplyProposalRequest) -> dict:
    try:
        applied = apply_graph_spec_proposal(
            workflow=workflow,
            proposed_yaml=request.proposed_yaml,
            proposal_summary=request.proposal_summary,
            evaluation_artifact=request.evaluation_artifact,
            accepted_by=request.accepted_by,
            specs_root=WORKFLOW_SPECS_ROOT,
            audit_root=SPEC_AUDIT_ROOT,
        )
    except FileNotFoundError:
        raise HTTPException(404, f"workflow spec {workflow!r} not found")
    except (ValueError, ValidationError, yaml.YAMLError) as exc:
        raise HTTPException(400, str(exc))
    except OSError as exc:
        raise HTTPException(500, f"failed to apply workflow spec proposal: {exc}")
    return {
        "workflow": applied.workflow,
        "status": applied.status,
        "source_path": applied.source_path.as_posix(),
        "audit_path": applied.audit_path.as_posix(),
        "rollback_path": applied.rollback_path.as_posix(),
        "diff": applied.diff,
        "spec": applied.spec.model_dump(mode="json"),
        "yaml": applied.yaml_text,
    }


@router.get("/api/spec/{workflow}/rollback-snapshots")
def get_rollback_snapshots(workflow: str) -> dict:
    try:
        snapshots = list_rollback_snapshots(
            workflow,
            specs_root=WORKFLOW_SPECS_ROOT,
            audit_root=SPEC_AUDIT_ROOT,
        )
    except FileNotFoundError:
        raise HTTPException(404, f"workflow spec {workflow!r} not found")
    return {
        "workflow": workflow,
        "snapshots": [
            {
                "snapshot_id": snapshot.snapshot_id,
                "audit_path": snapshot.audit_path.as_posix(),
                "rollback_path": snapshot.rollback_path.as_posix(),
                "created_at": snapshot.created_at,
                "accepted_by": snapshot.accepted_by,
                "proposal_summary": snapshot.proposal_summary,
                "original_sha256": snapshot.original_sha256,
                "proposed_sha256": snapshot.proposed_sha256,
            }
            for snapshot in snapshots
        ],
    }


@router.get("/api/spec/{workflow}/rollback-snapshots/{snapshot_id}/preview")
def get_rollback_snapshot_preview(workflow: str, snapshot_id: str) -> dict:
    try:
        preview = preview_rollback_snapshot(
            workflow,
            snapshot_id,
            specs_root=WORKFLOW_SPECS_ROOT,
            audit_root=SPEC_AUDIT_ROOT,
        )
    except FileNotFoundError as exc:
        message = str(exc)
        if "rollback snapshot" in message:
            raise HTTPException(404, message)
        raise HTTPException(404, f"workflow spec {workflow!r} not found")
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, str(exc))
    return {
        "workflow": preview.workflow,
        "snapshot_id": preview.snapshot_id,
        "current_yaml": preview.current_yaml,
        "rollback_yaml": preview.rollback_yaml,
        "diff": preview.diff,
    }


@router.post("/api/spec/{workflow}/rollback-snapshots/{snapshot_id}/restore")
def post_rollback_snapshot_restore(
    workflow: str,
    snapshot_id: str,
    request: RollbackRestoreRequest | None = None,
) -> dict:
    try:
        restored = restore_rollback_snapshot(
            workflow,
            snapshot_id,
            accepted_by=request.accepted_by if request else None,
            specs_root=WORKFLOW_SPECS_ROOT,
            audit_root=SPEC_AUDIT_ROOT,
        )
    except FileNotFoundError as exc:
        message = str(exc)
        if "rollback snapshot" in message:
            raise HTTPException(404, message)
        raise HTTPException(404, f"workflow spec {workflow!r} not found")
    except (ValueError, ValidationError, yaml.YAMLError) as exc:
        raise HTTPException(400, str(exc))
    except OSError as exc:
        raise HTTPException(500, f"failed to restore rollback snapshot: {exc}")
    return {
        "workflow": restored.workflow,
        "status": restored.status,
        "source_path": restored.source_path.as_posix(),
        "snapshot_id": restored.snapshot_id,
        "audit_path": restored.audit_path.as_posix(),
        "rollback_path": restored.rollback_path.as_posix(),
        "diff": restored.diff,
        "spec": restored.spec.model_dump(mode="json"),
        "yaml": restored.yaml_text,
    }


@router.post("/api/spec/{workflow}/evaluate-proposal")
def post_spec_proposal_eval(workflow: str, request: ProposalEvaluationRequest) -> dict:
    try:
        load_graph_spec_source(workflow, specs_root=WORKFLOW_SPECS_ROOT)
    except FileNotFoundError:
        raise HTTPException(404, f"workflow spec {workflow!r} not found")
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, f"workflow spec {workflow!r} is invalid: {exc}")

    try:
        payload = yaml.safe_load(request.proposed_yaml)
        if not isinstance(payload, dict):
            raise ValueError("proposed YAML must contain a mapping")
        spec = GraphSpec.model_validate(payload)
        metadata = graph_spec_to_metadata(spec)
    except (ValueError, ValidationError, yaml.YAMLError) as exc:
        return {
            "workflow": workflow,
            "status": "invalid",
            "validation_errors": [str(exc)],
            "eval": None,
            "run_artifact": None,
        }

    fixtures_path = EVALS_ROOT / workflow / "fixtures.yaml"
    if not fixtures_path.exists():
        raise HTTPException(404, f"eval fixtures for workflow {workflow!r} not found")

    proposal_root = RUNS_ROOT / f"proposal_eval_{workflow}_{int(time.time())}"
    output_path = proposal_root / "eval.json"
    out = run_eval_for_metadata(
        workflow=workflow,
        metadata=metadata,
        n_per_fixture=request.n_per_fixture,
        fixtures_path=fixtures_path,
        runs_root=proposal_root,
        output_path=output_path,
        baseline_path=EVALS_ROOT / workflow / "baseline.json",
        max_cost_usd=request.max_cost_usd,
    )
    return {
        "workflow": workflow,
        "status": out.get("status", "ok"),
        "validation_errors": [],
        "eval": {
            "completed_run_count": out.get("completed_run_count", 0),
            "completed_fixture_count": out.get("completed_fixture_count", 0),
            "overall": out.get("overall", {}),
            "overall_ci": out.get("overall_ci", {}),
            "baseline_comparison": out.get("baseline_comparison", {}),
        },
        "run_artifact": output_path.as_posix(),
    }


@router.post("/api/spec/{workflow}/optimize-proposals")
def post_spec_optimize_proposals(workflow: str, request: OptimizeProposalsRequest) -> dict:
    try:
        report = optimize_proposals(
            workflow=workflow,
            goal=request.goal,
            constraints=request.constraints,
            candidate_count=request.candidate_count,
            n_per_fixture=request.n_per_fixture,
            max_cost_usd=request.max_cost_usd,
            runs_root=RUNS_ROOT,
            evals_root=EVALS_ROOT,
        )
    except FileNotFoundError as exc:
        message = str(exc)
        if "eval fixtures" in message:
            raise HTTPException(400, message)
        raise HTTPException(404, f"workflow spec {workflow!r} not found")
    except (ValueError, ValidationError, yaml.YAMLError) as exc:
        raise HTTPException(400, str(exc))
    except ProviderError as exc:
        raise HTTPException(503, f"optimization provider error: {exc}")
    return {
        "workflow": report.workflow,
        "status": report.status,
        "recommended_candidate_id": report.recommended_candidate_id,
        "report_artifact": report.report_artifact,
        "original_yaml": report.original_yaml,
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "status": candidate.status,
                "summary": candidate.summary,
                "proposed_yaml": candidate.proposed_yaml,
                "diff": candidate.diff,
                "validation_errors": candidate.validation_errors,
                "eval": candidate.eval,
                "run_artifact": candidate.run_artifact,
                "rank": candidate.rank,
                "recommended": candidate.recommended,
            }
            for candidate in report.candidates
        ],
    }


@router.get("/api/graph/{workflow}/node-metrics")
def get_graph_node_metrics(workflow: str, limit: int = 50) -> dict:
    if limit <= 0:
        raise HTTPException(400, "limit must be > 0")
    metadata = load_workflow(workflow)
    metrics = compute_node_metrics(
        runs_root=RUNS_ROOT,
        workflow=workflow,
        node_ids=set(metadata.nodes.keys()),
        limit=min(limit, 500),
    )
    runs_considered = max((m["runs_considered"] for m in metrics.values()), default=0)
    return {
        "workflow": workflow,
        "limit": min(limit, 500),
        "runs_considered": runs_considered,
        "metrics": metrics,
    }


@router.get("/api/runs")
def list_runs(limit: int = 50) -> list[dict]:
    return list_runs_data(runs_root=RUNS_ROOT, limit=limit)


@router.post("/api/runs")
def start_run(request: StartRunRequest, workflow: str) -> dict:
    metadata = load_workflow(workflow)
    result = run_graph(
        metadata,
        user_input=request.input,
        expected=request.expected,
        runs_root=RUNS_ROOT,
    )
    return {
        "run_id": result.run_id,
        "graph_name": result.graph_name,
        "status": result.status,
        "cost_usd": result.cost_usd,
        "latency_ms": result.latency_ms,
        "error": result.error,
        **artifact_paths(result.run_dir),
        "final_state": result.final_state,
    }


@router.get("/api/approvals")
def list_approvals(status: str = "pending") -> list[dict]:
    if status not in {"pending", "decided", "all"}:
        raise HTTPException(400, "status must be one of: pending, decided, all")
    return list_approval_data(runs_root=RUNS_ROOT, status=status)


@router.post("/api/approvals/{run_id}/decision")
def post_approval_decision(run_id: str, request: ApprovalDecisionRequest) -> dict:
    try:
        result = decide_approval(
            run_id=run_id,
            decision=request.decision,  # type: ignore[arg-type]
            reviewer=request.reviewer,
            comment=request.comment,
            runs_root=RUNS_ROOT,
        )
    except ApprovalNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except ApprovalAlreadyDecidedError as exc:
        raise HTTPException(409, str(exc))
    except ApprovalDecisionError as exc:
        raise HTTPException(400, str(exc))
    except OSError as exc:
        raise HTTPException(500, f"failed to record approval decision: {exc}")
    response: dict[str, Any] = {
        "source_run_id": result.source_run_id,
        "status": result.status,
        "decision": result.decision,
        "decision_artifact": result.decision_artifact.as_posix(),
        "continuation_run_id": result.continuation_run_id,
        "continuation_status": result.continuation_status,
        "continuation_run_dir": result.continuation_run_dir.as_posix(),
        "continuation_error": result.continuation_error,
    }
    if result.parent_continuation_run_id is not None:
        response["parent_run_id"] = result.parent_run_id
        response["parent_workflow"] = result.parent_workflow
        response["parent_subgraph_node_id"] = result.parent_subgraph_node_id
        response["parent_continuation_run_id"] = result.parent_continuation_run_id
        response["parent_continuation_status"] = result.parent_continuation_status
        response["parent_continuation_run_dir"] = (
            result.parent_continuation_run_dir.as_posix()
            if result.parent_continuation_run_dir else None
        )
        response["parent_continuation_error"] = result.parent_continuation_error
    return response


def list_approval_data(*, runs_root: Path, status: str = "pending") -> list[dict[str, Any]]:
    if not runs_root.exists():
        return []
    approvals: list[dict[str, Any]] = []
    for run_dir in iter_run_dirs(runs_root):
        path = run_dir / "approval.json"
        if not path.exists():
            continue
        try:
            approval = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        approval["artifact_path"] = path.as_posix()
        decision_path = path.parent / "approval_decision.json"
        if decision_path.exists():
            try:
                decision = json.loads(decision_path.read_text(encoding="utf-8"))
                decision["artifact_path"] = decision_path.as_posix()
                approval["approval_decision"] = decision
                approval["status"] = "decided"
            except (OSError, json.JSONDecodeError):
                approval["status"] = "pending"
        else:
            approval["status"] = "pending"
        if status != "all" and approval["status"] != status:
            continue
        approvals.append(approval)
    approvals.sort(key=lambda item: item.get("created_ns") or 0, reverse=True)
    return approvals


def list_runs_data(*, runs_root: Path, limit: int = 50) -> list[dict[str, Any]]:
    """Aggregate all runs across per-run SQLite databases under runs/."""
    if not runs_root.exists():
        return []
    out: list[dict[str, Any]] = []
    for d in iter_run_dirs(runs_root):
        db = d / "telemetry.db"
        if not db.exists():
            continue
        try:
            with sqlite3.connect(db) as con:
                rows = con.execute(
                    "SELECT run_id, graph_name, started_ns, ended_ns, status, cost_usd, latency_ms, error "
                    "FROM runs"
                ).fetchall()
                for r in rows:
                    out.append(
                        {
                            "run_id": r[0],
                            "graph_name": r[1],
                            "started_ns": r[2],
                            "ended_ns": r[3],
                            "status": r[4],
                            "cost_usd": r[5],
                            "latency_ms": r[6],
                            "error": r[7],
                            **artifact_paths(d),
                            **_approval_lifecycle(d),
                        }
                    )
        except sqlite3.OperationalError:
            continue
    out.sort(key=lambda r: r.get("started_ns") or 0, reverse=True)
    return out[:limit]


@router.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = get_run_data(runs_root=RUNS_ROOT, run_id=run_id)
    if run is None:
        raise HTTPException(404, f"run {run_id!r} not found")
    return run


def get_run_data(*, runs_root: Path, run_id: str) -> dict[str, Any] | None:
    try:
        run_dir = resolve_run_dir(runs_root, run_id)
    except FileNotFoundError:
        return None
    db = run_dir / "telemetry.db"
    if not db.exists():
        return None
    with sqlite3.connect(db) as con:
        run_row = con.execute(
            "SELECT run_id, graph_name, started_ns, ended_ns, status, cost_usd, latency_ms, error "
            "FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if not run_row:
            return None
        span_rows = con.execute(
            "SELECT span_id, name, start_ns, end_ns, duration_ms, status, node_id, node_kind, "
            "iteration, model, input_tokens, output_tokens, cost_usd, attributes_json "
            "FROM spans WHERE run_id=? ORDER BY start_ns ASC",
            (run_id,),
        ).fetchall()

    spans = []
    for s in span_rows:
        try:
            attrs = json.loads(s[13])
        except Exception:
            attrs = {}
        spans.append(
            {
                "span_id": s[0],
                "name": s[1],
                "start_ns": s[2],
                "end_ns": s[3],
                "duration_ms": s[4],
                "status": s[5],
                "node_id": s[6],
                "node_kind": s[7],
                "iteration": s[8],
                "model": s[9],
                "input_tokens": s[10],
                "output_tokens": s[11],
                "cost_usd": s[12],
                "attributes": attrs,
            }
        )

    out = {
        "run_id": run_row[0],
        "graph_name": run_row[1],
        "started_ns": run_row[2],
        "ended_ns": run_row[3],
        "status": run_row[4],
        "cost_usd": run_row[5],
        "latency_ms": run_row[6],
        "error": run_row[7],
        "spans": spans,
        **artifact_paths(run_dir),
    }
    approval_path = run_dir / "approval.json"
    if approval_path.exists():
        try:
            approval = json.loads(approval_path.read_text(encoding="utf-8"))
            approval["artifact_path"] = approval_path.as_posix()
            out["approval"] = approval
        except (OSError, json.JSONDecodeError):
            out["approval"] = None
    decision_path = run_dir / "approval_decision.json"
    if decision_path.exists():
        try:
            decision = json.loads(decision_path.read_text(encoding="utf-8"))
            decision["artifact_path"] = decision_path.as_posix()
            out["approval_decision"] = decision
        except (OSError, json.JSONDecodeError):
            out["approval_decision"] = None
    resume_path = run_dir / "approval_resume.json"
    if resume_path.exists():
        try:
            resume = json.loads(resume_path.read_text(encoding="utf-8"))
            resume["artifact_path"] = resume_path.as_posix()
            out["approval_resume"] = resume
        except (OSError, json.JSONDecodeError):
            out["approval_resume"] = None
    subgraph_dir = run_dir / "subgraphs"
    subgraphs: list[dict[str, Any]] = []
    if subgraph_dir.exists():
        for path in sorted(subgraph_dir.glob("*.json")):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(item, dict):
                    item["artifact_path"] = path.as_posix()
                    subgraphs.append(item)
            except (OSError, json.JSONDecodeError):
                continue
    if subgraphs:
        out["subgraphs"] = subgraphs
    parent_path = run_dir / "parent_run.json"
    if parent_path.exists():
        try:
            parent = json.loads(parent_path.read_text(encoding="utf-8"))
            parent["artifact_path"] = parent_path.as_posix()
            out["parent_run"] = parent
        except (OSError, json.JSONDecodeError):
            out["parent_run"] = None

    # Nested approval subgraph artifacts (M5.8).
    pending_subgraph_path = run_dir / "pending_subgraph_approval.json"
    if pending_subgraph_path.exists():
        try:
            psa = json.loads(pending_subgraph_path.read_text(encoding="utf-8"))
            psa["artifact_path"] = pending_subgraph_path.as_posix()
            out["pending_subgraph_approval"] = psa
        except (OSError, json.JSONDecodeError):
            out["pending_subgraph_approval"] = None

    subgraph_decision_path = run_dir / "subgraph_decision.json"
    if subgraph_decision_path.exists():
        try:
            sd = json.loads(subgraph_decision_path.read_text(encoding="utf-8"))
            sd["artifact_path"] = subgraph_decision_path.as_posix()
            out["subgraph_decision"] = sd
        except (OSError, json.JSONDecodeError):
            out["subgraph_decision"] = None

    subgraph_resume_path = run_dir / "subgraph_resume.json"
    if subgraph_resume_path.exists():
        try:
            sr = json.loads(subgraph_resume_path.read_text(encoding="utf-8"))
            sr["artifact_path"] = subgraph_resume_path.as_posix()
            out["subgraph_resume"] = sr
        except (OSError, json.JSONDecodeError):
            out["subgraph_resume"] = None

    out.update(_approval_lifecycle(run_dir))
    return out


def _approval_lifecycle(run_dir: Path) -> dict[str, Any]:
    # Runs with their own approval.json use the standard approval lifecycle.
    approval_path = run_dir / "approval.json"
    if approval_path.exists():
        base: dict[str, Any] = {
            "approval_status": "pending",
            "display_status": "pending_approval",
        }
        decision_path = run_dir / "approval_decision.json"
        if not decision_path.exists():
            return base

        try:
            decision = json.loads(decision_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return base

        decision_value = str(decision.get("decision") or "decided")
        continuation_status = str(decision.get("continuation_status") or "")
        base.update(
            {
                "approval_status": decision_value,
                "display_status": f"{decision_value}_continued" if continuation_status else decision_value,
                "continuation_run_id": decision.get("continuation_run_id"),
                "continuation_status": continuation_status or None,
            }
        )
        return base

    # Parent runs whose child subgraph is paused on an approval node.
    pending_subgraph_path = run_dir / "pending_subgraph_approval.json"
    if pending_subgraph_path.exists():
        subgraph_decision_path = run_dir / "subgraph_decision.json"
        if not subgraph_decision_path.exists():
            try:
                psa = json.loads(pending_subgraph_path.read_text(encoding="utf-8"))
                return {
                    "display_status": "pending_child_approval",
                    "pending_child_run_id": psa.get("child_run_id"),
                }
            except (OSError, json.JSONDecodeError):
                return {"display_status": "pending_child_approval"}

        try:
            sd = json.loads(subgraph_decision_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"display_status": "pending_child_approval"}

        parent_continuation_status = str(sd.get("parent_continuation_status") or "")
        if parent_continuation_status == "ok":
            return {
                "display_status": "child_decided_continued",
                "parent_continuation_run_id": sd.get("parent_continuation_run_id"),
                "parent_continuation_status": parent_continuation_status,
            }
        if sd.get("decision") == "rejected":
            return {"display_status": "child_rejected"}
        return {"display_status": "child_failed"}

    return {}
