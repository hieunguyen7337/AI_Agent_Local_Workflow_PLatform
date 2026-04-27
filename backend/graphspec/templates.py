"""Reusable workflow template copy helpers."""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from .loader import WORKFLOW_SPECS_ROOT, graph_spec_path, load_graph_spec_source
from .models import GraphSpec

_WORKFLOW_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass
class CopiedTemplate:
    workflow: str
    source_template: str
    status: str
    source_path: Path
    audit_path: Path
    spec: GraphSpec
    yaml_text: str


def copy_workflow_template(
    *,
    workflow: str,
    new_workflow_id: str,
    name: str | None = None,
    description: str | None = None,
    category: str | None = None,
    tags: list[str] | None = None,
    accepted_by: str | None = None,
    specs_root: Path = WORKFLOW_SPECS_ROOT,
    audit_root: Path = Path("runs") / "spec_audit",
) -> CopiedTemplate:
    if not _WORKFLOW_ID_RE.fullmatch(new_workflow_id):
        raise ValueError("new_workflow_id must be lowercase snake_case and start with a letter")

    source_spec, _source_yaml, source_path = load_graph_spec_source(workflow, specs_root=specs_root)
    if not source_spec.template:
        raise ValueError(f"workflow {workflow!r} is not a template")

    target_path = graph_spec_path(new_workflow_id, specs_root=specs_root)
    if target_path.exists():
        raise FileExistsError(f"workflow spec {target_path} already exists")

    payload = source_spec.model_dump(mode="json", by_alias=True)
    payload["name"] = name if name is not None else new_workflow_id
    if description is not None:
        payload["description"] = description
    if category is not None:
        payload["category"] = category
    if tags is not None:
        payload["tags"] = tags
    payload["template"] = False
    payload["template_parameters"] = []

    copied_spec = GraphSpec.model_validate(payload)
    yaml_text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)

    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    audit_path = audit_root / new_workflow_id / f"{timestamp}_{time.time_ns()}"
    audit_path.mkdir(parents=True, exist_ok=False)
    try:
        target_path.write_text(yaml_text, encoding="utf-8")
    except OSError:
        # Leave no partial audit claiming a copy succeeded if the canonical write fails.
        raise

    audit = {
        "action": "template_copy",
        "workflow": new_workflow_id,
        "source_template": workflow,
        "source_path": source_path.as_posix(),
        "target_path": target_path.as_posix(),
        "timestamp": timestamp,
        "accepted_by": accepted_by or "local-user",
        "written_sha256": _sha256(yaml_text),
    }
    (audit_path / "audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")

    return CopiedTemplate(
        workflow=new_workflow_id,
        source_template=workflow,
        status="copied",
        source_path=target_path,
        audit_path=audit_path,
        spec=copied_spec,
        yaml_text=yaml_text,
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
