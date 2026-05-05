"""Allowlisted helpers for visible Claude-style YAML agent workflows."""
from __future__ import annotations

import json
from typing import Any


def tool_history_digest(
    tool_result_history: Any = None,
    *,
    max_items: int = 12,
    max_line_chars: int = 220,
    max_total_chars: int = 3800,
) -> str:
    """Compact tool_result_history into short lines for LLM prompts (no raw file bodies).

    Each line: tool, status, optional path/pattern, output length or truncated preview.
    """
    if not tool_result_history:
        return "(no tool results yet)"
    if not isinstance(tool_result_history, list):
        return "(invalid tool_result_history)"

    lines: list[str] = []
    tail = tool_result_history[-max_items:] if len(tool_result_history) > max_items else tool_result_history
    for i, item in enumerate(tail):
        if not isinstance(item, dict):
            lines.append(f"- [{i}] (non-dict entry)")
            continue
        tool = str(item.get("tool") or item.get("name") or "?")
        status = str(item.get("status", "?"))
        extra_parts: list[str] = []
        if item.get("path"):
            extra_parts.append(f"path={item.get('path')}")
        if item.get("pattern"):
            extra_parts.append(f"pattern={item.get('pattern')}")
        out = item.get("output")
        if isinstance(out, str):
            extra_parts.append(f"out_len={len(out)}")
            preview = out.replace("\n", " ")[:80]
            if preview:
                extra_parts.append(f"preview={preview!r}")
        elif isinstance(out, (dict, list)):
            blob = json.dumps(out, ensure_ascii=False)
            extra_parts.append(f"out_len={len(blob)}")
        summary = ", ".join(extra_parts) if extra_parts else ""
        line = f"- [{i}] {tool} status={status}" + (f" | {summary}" if summary else "")
        if len(line) > max_line_chars:
            line = line[: max_line_chars - 12] + "...<truncated>"
        lines.append(line)

    text = "\n".join(lines)
    if len(text) > max_total_chars:
        text = text[: max_total_chars - 20] + "\n...<digest_truncated>"
    return text
