"""List, preview, and restore GraphSpec rollback snapshots."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from .apply import _diff_yaml, _sha256
from .loader import WORKFLOW_SPECS_ROOT, graph_spec_path, load_graph_spec_source
from .models import GraphSpec


@dataclass
class RollbackSnapshot:
    snapshot_id: str
    audit_path: Path
    rollback_path: Path
    created_at: str
    accepted_by: str
    proposal_summary: str
    original_sha256: str
    proposed_sha256: str


@dataclass
class RollbackPreview:
    workflow: str
    snapshot_id: str
    current_yaml: str
    rollback_yaml: str
    diff: str


@dataclass
class RestoredRollback:
    workflow: str
    status: str
    source_path: Path
    snapshot_id: str
    audit_path: Path
    rollback_path: Path
    diff: str
    spec: GraphSpec
    yaml_text: str


def list_rollback_snapshots(
    workflow: str,
    *,
    specs_root: Path = WORKFLOW_SPECS_ROOT,
    audit_root: Path = Path("runs") / "spec_audit",
) -> list[RollbackSnapshot]:
    if not graph_spec_path(workflow, specs_root=specs_root).exists():
        raise FileNotFoundError(f"workflow spec {workflow!r} not found")
    workflow_root = audit_root / workflow
    if not workflow_root.exists():
        return []
    snapshots: list[RollbackSnapshot] = []
    for audit_path in sorted(workflow_root.iterdir(), reverse=True):
        rollback_path = audit_path / "original.yaml"
        if not audit_path.is_dir() or not rollback_path.exists():
            continue
        audit = _read_audit(audit_path / "audit.json")
        snapshots.append(
            RollbackSnapshot(
                snapshot_id=audit_path.name,
                audit_path=audit_path,
                rollback_path=rollback_path,
                created_at=str(audit.get("timestamp") or audit_path.name.split("_", 1)[0]),
                accepted_by=str(audit.get("accepted_by") or "local-user"),
                proposal_summary=str(
                    audit.get("proposal_summary") or audit.get("restore_summary") or ""
                ),
                original_sha256=str(audit.get("original_sha256") or _sha256(rollback_path.read_text(encoding="utf-8"))),
                proposed_sha256=str(audit.get("proposed_sha256") or ""),
            )
        )
    return snapshots


def preview_rollback_snapshot(
    workflow: str,
    snapshot_id: str,
    *,
    specs_root: Path = WORKFLOW_SPECS_ROOT,
    audit_root: Path = Path("runs") / "spec_audit",
) -> RollbackPreview:
    _spec, current_yaml, _source_path = load_graph_spec_source(workflow, specs_root=specs_root)
    rollback_yaml = _snapshot_yaml(workflow, snapshot_id, audit_root=audit_root)
    return RollbackPreview(
        workflow=workflow,
        snapshot_id=snapshot_id,
        current_yaml=current_yaml,
        rollback_yaml=rollback_yaml,
        diff=_diff_yaml(current_yaml, rollback_yaml),
    )


def restore_rollback_snapshot(
    workflow: str,
    snapshot_id: str,
    *,
    accepted_by: str | None = None,
    specs_root: Path = WORKFLOW_SPECS_ROOT,
    audit_root: Path = Path("runs") / "spec_audit",
) -> RestoredRollback:
    current_spec, current_yaml, source_path = load_graph_spec_source(workflow, specs_root=specs_root)
    rollback_yaml = _snapshot_yaml(workflow, snapshot_id, audit_root=audit_root)
    spec = _validate_rollback_yaml(workflow=workflow, rollback_yaml=rollback_yaml)
    if spec.name != current_spec.name:
        raise ValueError(
            f"rollback spec name {spec.name!r} does not match current workflow name {current_spec.name!r}"
        )

    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    audit_path = audit_root / workflow / f"{timestamp}_{time.time_ns()}_restore"
    rollback_path = audit_path / "original.yaml"
    audit_path.mkdir(parents=True, exist_ok=False)
    rollback_path.write_text(current_yaml, encoding="utf-8")
    diff = _diff_yaml(current_yaml, rollback_yaml)
    audit = {
        "workflow": workflow,
        "source_path": source_path.as_posix(),
        "timestamp": timestamp,
        "accepted_by": accepted_by or "local-user",
        "action": "rollback_restore",
        "snapshot_id": snapshot_id,
        "restore_summary": f"Restored rollback snapshot {snapshot_id}.",
        "validation_status": "valid",
        "original_sha256": _sha256(current_yaml),
        "proposed_sha256": _sha256(rollback_yaml),
        "diff": diff,
    }
    (audit_path / "audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    source_path.write_text(rollback_yaml, encoding="utf-8")
    return RestoredRollback(
        workflow=workflow,
        status="restored",
        source_path=source_path,
        snapshot_id=snapshot_id,
        audit_path=audit_path,
        rollback_path=rollback_path,
        diff=diff,
        spec=spec,
        yaml_text=rollback_yaml,
    )


def _snapshot_yaml(workflow: str, snapshot_id: str, *, audit_root: Path) -> str:
    if "/" in snapshot_id or "\\" in snapshot_id or ".." in snapshot_id:
        raise FileNotFoundError(f"rollback snapshot {snapshot_id!r} not found")
    rollback_path = audit_root / workflow / snapshot_id / "original.yaml"
    if not rollback_path.exists():
        raise FileNotFoundError(f"rollback snapshot {snapshot_id!r} not found")
    return rollback_path.read_text(encoding="utf-8")


def _validate_rollback_yaml(*, workflow: str, rollback_yaml: str) -> GraphSpec:
    try:
        payload = yaml.safe_load(rollback_yaml)
        if not isinstance(payload, dict):
            raise ValueError("rollback YAML must contain a mapping")
        spec = GraphSpec.model_validate(payload)
    except (ValueError, ValidationError, yaml.YAMLError):
        raise
    if spec.name != workflow:
        raise ValueError(f"rollback spec name {spec.name!r} does not match workflow {workflow!r}")
    return spec


def _read_audit(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
