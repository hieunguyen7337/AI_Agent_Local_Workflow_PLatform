"""Compile-time graph validation."""
from __future__ import annotations

from backend.runtime.errors import BuilderValidationError


def assert_no_cycle(edges: list[tuple[str, str]], source: str, target: str) -> None:
    """Raise if adding source->target would create a cycle (target reaches source)."""
    adj: dict[str, list[str]] = {}
    for s, t in edges:
        adj.setdefault(s, []).append(t)
    adj.setdefault(source, []).append(target)

    seen: set[str] = set()
    stack = [target]
    while stack:
        cur = stack.pop()
        if cur == source:
            raise BuilderValidationError(
                f"add_edge({source!r}, {target!r}) would create a cycle. "
                "Use add_loop() with max_iterations to create back-edges."
            )
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(adj.get(cur, []))


def assert_node_exists(node_ids: set[str], node_id: str, context: str) -> None:
    if node_id not in node_ids:
        raise BuilderValidationError(f"{context}: unknown node id {node_id!r}")
