"""Read-only GraphSpec mutation proposal service."""
from __future__ import annotations

import difflib
from dataclasses import dataclass

import yaml
from pydantic import ValidationError

from backend.providers import call_provider
from backend.providers.base import ProviderError
from backend.providers.pricing import DEFAULT_OPENAI_MODEL

from .loader import load_graph_spec_source
from .models import GraphSpec


@dataclass
class MutationProposal:
    workflow: str
    status: str
    summary: str
    original_yaml: str
    proposed_yaml: str
    diff: str
    validation_errors: list[str]


def propose_mutation(
    *,
    workflow: str,
    goal: str,
    constraints: str | None = None,
    max_proposals: int = 1,
    provider: str = "openai",
    model: str = DEFAULT_OPENAI_MODEL,
) -> MutationProposal:
    if not goal.strip():
        raise ValueError("goal is required")
    if max_proposals != 1:
        raise ValueError("only max_proposals=1 is supported for this read-only proposal loop")

    _spec, original_yaml, _source_path = load_graph_spec_source(workflow)
    messages = _proposal_messages(
        workflow=workflow,
        original_yaml=original_yaml,
        goal=goal,
        constraints=constraints,
    )
    try:
        response = call_provider(
            provider=provider,
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=6000,
            max_retries=2,
        )
    except ProviderError:
        raise

    proposed_yaml = _extract_yaml(response.text)
    summary = _extract_summary(response.text)
    validation_errors: list[str] = []
    status = "valid"
    try:
        payload = yaml.safe_load(proposed_yaml)
        if not isinstance(payload, dict):
            raise ValueError("proposed YAML must contain a mapping")
        GraphSpec.model_validate(payload)
    except (ValueError, ValidationError, yaml.YAMLError) as exc:
        status = "invalid"
        validation_errors.append(str(exc))

    return MutationProposal(
        workflow=workflow,
        status=status,
        summary=summary,
        original_yaml=original_yaml,
        proposed_yaml=proposed_yaml,
        diff=_diff_yaml(original_yaml, proposed_yaml),
        validation_errors=validation_errors,
    )


def _proposal_messages(
    *,
    workflow: str,
    original_yaml: str,
    goal: str,
    constraints: str | None,
) -> list[dict]:
    constraint_text = constraints.strip() if constraints and constraints.strip() else "No extra constraints."
    return [
        {
            "role": "system",
            "content": (
                "You propose read-only mutations to YAML workflow GraphSpec files. "
                "Return a complete revised YAML document, not a patch. "
                "Preserve schema_version, valid node ids, valid edge targets, and GraphSpec compatibility. "
                "Do not include markdown fences unless unavoidable."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Workflow: {workflow}\n\n"
                f"Goal:\n{goal.strip()}\n\n"
                f"Constraints:\n{constraint_text}\n\n"
                "Current YAML:\n"
                f"{original_yaml}\n\n"
                "Respond with the complete revised YAML. Optionally add a one-line summary before the YAML "
                "using the prefix 'Summary:'."
            ),
        },
    ]


def _extract_yaml(text: str) -> str:
    stripped = text.strip()
    if "```" not in stripped:
        lines = stripped.splitlines()
        if lines and lines[0].lower().startswith("summary:"):
            return "\n".join(lines[1:]).strip()
        return stripped

    parts = stripped.split("```")
    for part in parts:
        candidate = part.strip()
        if candidate.lower().startswith("yaml"):
            candidate = candidate[4:].strip()
        if candidate.startswith("schema_version:") or "\nschema_version:" in candidate:
            return candidate.strip()
    return stripped


def _extract_summary(text: str) -> str:
    for line in text.strip().splitlines():
        if line.lower().startswith("summary:"):
            return line.split(":", 1)[1].strip()
    return "Mutation proposal generated."


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
