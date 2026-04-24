"""Apply validated GraphSpec mutation proposals to canonical YAML files."""
from __future__ import annotations

import difflib
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from .loader import WORKFLOW_SPECS_ROOT, load_graph_spec_source
from .models import GraphSpec


@dataclass
class AppliedProposal:
    workflow: str
    status: str
    source_path: Path
    audit_path: Path
    rollback_path: Path
    diff: str
    spec: GraphSpec
    yaml_text: str


def apply_graph_spec_proposal(
    *,
    workflow: str,
    proposed_yaml: str,
    proposal_summary: str | None = None,
    evaluation_artifact: str | None = None,
    accepted_by: str | None = None,
    specs_root: Path = WORKFLOW_SPECS_ROOT,
    audit_root: Path = Path("runs") / "spec_audit",
) -> AppliedProposal:
    current_spec, original_yaml, source_path = load_graph_spec_source(workflow, specs_root=specs_root)
    spec = _validate_proposed_yaml(workflow=workflow, proposed_yaml=proposed_yaml)
    if spec.name != current_spec.name:
        raise ValueError(
            f"proposed spec name {spec.name!r} does not match current workflow name {current_spec.name!r}"
        )

    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    audit_path = audit_root / workflow / f"{timestamp}_{time.time_ns()}"
    rollback_path = audit_path / "original.yaml"
    audit_path.mkdir(parents=True, exist_ok=False)
    rollback_path.write_text(original_yaml, encoding="utf-8")

    diff = _diff_yaml(original_yaml, proposed_yaml)
    audit = {
        "workflow": workflow,
        "source_path": source_path.as_posix(),
        "timestamp": timestamp,
        "accepted_by": accepted_by or "local-user",
        "proposal_summary": proposal_summary or "",
        "evaluation_artifact": evaluation_artifact,
        "validation_status": "valid",
        "original_sha256": _sha256(original_yaml),
        "proposed_sha256": _sha256(proposed_yaml),
        "diff": diff,
    }
    (audit_path / "audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    source_path.write_text(proposed_yaml, encoding="utf-8")

    return AppliedProposal(
        workflow=workflow,
        status="applied",
        source_path=source_path,
        audit_path=audit_path,
        rollback_path=rollback_path,
        diff=diff,
        spec=spec,
        yaml_text=proposed_yaml,
    )


def _validate_proposed_yaml(*, workflow: str, proposed_yaml: str) -> GraphSpec:
    try:
        payload = yaml.safe_load(proposed_yaml)
        if not isinstance(payload, dict):
            raise ValueError("proposed YAML must contain a mapping")
        spec = GraphSpec.model_validate(payload)
    except (ValueError, ValidationError, yaml.YAMLError):
        raise
    if spec.name != workflow:
        raise ValueError(f"proposed spec name {spec.name!r} does not match workflow {workflow!r}")
    return spec


def _diff_yaml(original_yaml: str, proposed_yaml: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            original_yaml.splitlines(),
            proposed_yaml.splitlines(),
            fromfile="original.yaml",
            tofile="proposed.yaml",
            lineterm="",
        )
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
