"""Local coding-agent tool registry for the visible Claude-style workflow."""
from __future__ import annotations

import difflib
import fnmatch
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CodingTool:
    name: str
    bucket: str
    read_only: bool
    writes_files: bool = False
    shell: bool = False
    executable: bool = True
    reversible: str | bool = True
    is_concurrency_safe: bool = True
    permission_required: bool = False
    checkpoint_before_write: bool = False
    windows_fallback: str | None = None


TOOLS: dict[str, CodingTool] = {
    "read_file": CodingTool("read_file", "fs_tools", read_only=True),
    "glob": CodingTool("glob", "fs_tools", read_only=True),
    "search": CodingTool("search", "fs_tools", read_only=True),
    "write_file": CodingTool(
        "write_file",
        "fs_tools",
        read_only=False,
        writes_files=True,
        permission_required=True,
        checkpoint_before_write=True,
    ),
    "shell": CodingTool(
        "shell",
        "shell_tools",
        read_only=False,
        shell=True,
        reversible=False,
        is_concurrency_safe=False,
        permission_required=True,
        windows_fallback="powershell",
    ),
    "subagent": CodingTool(
        "subagent",
        "agent_tools",
        read_only=False,
        executable=False,
        reversible="depends",
        permission_required=False,
    ),
    "mcp_call": CodingTool(
        "mcp_call",
        "mcp_tools",
        read_only=False,
        executable=False,
        reversible="depends_on_server",
        permission_required=True,
    ),
}

RISKY_SHELL_TOKENS = {
    "del",
    "erase",
    "format",
    "git reset",
    "rd",
    "remove-item",
    "rm",
    "rmdir",
    "shutdown",
    "sudo",
}


def normalize_tool_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    name = str(value.get("name") or value.get("tool") or value.get("tool_name") or "").strip()
    raw_args = value.get("args") or value.get("arguments") or {}
    args: dict[str, Any] = dict(raw_args) if isinstance(raw_args, dict) else {}
    if name in {"read_file", "write_file", "search"}:
        _relativize_workspace_path_arg(args, "path")
    return {"name": name, "args": args}


def _relativize_workspace_path_arg(args: dict[str, Any], key: str) -> None:
    raw = str(args.get(key, "") or "").strip()
    if not raw:
        return
    path = Path(raw)
    if not path.is_absolute():
        return
    cwd = Path.cwd().resolve()
    try:
        resolved = path.resolve()
        rel = resolved.relative_to(cwd)
        args[key] = rel.as_posix()
    except (ValueError, OSError):
        return


def tool_metadata(name: str) -> CodingTool | None:
    return TOOLS.get(name)


def shell_looks_irreversible(command: str) -> bool:
    lowered = command.strip().lower()
    return any(token in lowered for token in RISKY_SHELL_TOKENS)


def path_within_cwd(path_value: str) -> bool:
    try:
        _resolve_cwd_path(path_value)
        return True
    except PermissionError:
        return False


def execute_tool(request: dict[str, Any], *, run_dir: Path, allowed_tools: list[str]) -> dict[str, Any]:
    normalized = normalize_tool_request(request)
    name = normalized["name"]
    args = normalized["args"]
    if name not in allowed_tools:
        return _result(name, "denied", error=f"tool {name!r} is not allowed by this node")
    if name == "read_file":
        return _read_file(args)
    if name == "glob":
        return _glob(args)
    if name == "search":
        return _search(args)
    if name == "write_file":
        return _write_file(args, run_dir=run_dir)
    if name == "shell":
        return _shell(args)
    if name == "subagent":
        return _result(name, "requested", output="Subagent request captured for orchestrator.")
    if name == "mcp_call":
        return _result(name, "unavailable", output="No healthy MCP server is configured for local execution.")
    return _result(name, "error", error=f"unknown tool {name!r}")


def _read_file(args: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_cwd_path(str(args.get("path", "")))
    max_chars = int(args.get("max_chars") or 12000)
    text = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > max_chars
    return _result(
        "read_file",
        "ok",
        output=text[:max_chars],
        path=path.as_posix(),
        truncated=truncated,
    )


def _glob(args: dict[str, Any]) -> dict[str, Any]:
    pattern = str(args.get("pattern", "**/*"))
    max_results = int(args.get("max_results") or 100)
    cwd = Path.cwd().resolve()
    matches: list[str] = []
    for path in cwd.glob(pattern):
        try:
            resolved = path.resolve()
            resolved.relative_to(cwd)
        except (OSError, ValueError):
            continue
        matches.append(resolved.relative_to(cwd).as_posix())
        if len(matches) >= max_results:
            break
    return _result("glob", "ok", output=matches, pattern=pattern)


def _search(args: dict[str, Any]) -> dict[str, Any]:
    pattern = str(args.get("pattern", ""))
    root = _resolve_cwd_path(str(args.get("path", ".")))
    include = str(args.get("include", "*"))
    max_results = int(args.get("max_results") or 100)
    if not pattern:
        return _result("search", "error", error="search pattern is required")
    paths = [root] if root.is_file() else root.rglob("*")
    matches: list[dict[str, Any]] = []
    for path in paths:
        if not path.is_file() or any(part in {".git", ".venv", "__pycache__"} for part in path.parts):
            continue
        if include and not fnmatch.fnmatch(path.name, include):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            if pattern.lower() in line.lower():
                matches.append({"path": path.relative_to(Path.cwd()).as_posix(), "line": line_no, "text": line[:500]})
                if len(matches) >= max_results:
                    return _result("search", "ok", output=matches, pattern=pattern)
    return _result("search", "ok", output=matches, pattern=pattern)


def _write_file(args: dict[str, Any], *, run_dir: Path) -> dict[str, Any]:
    path = _resolve_cwd_path(str(args.get("path", "")))
    content = str(args.get("content", ""))
    before = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    snapshot_dir = run_dir / "file_snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_name = f"{int(time.time_ns())}_{path.name}.bak"
    snapshot_path = snapshot_dir / snapshot_name
    snapshot_path.write_text(before, encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    diff = "\n".join(
        difflib.unified_diff(
            before.splitlines(),
            content.splitlines(),
            fromfile=f"a/{path.relative_to(Path.cwd()).as_posix()}",
            tofile=f"b/{path.relative_to(Path.cwd()).as_posix()}",
            lineterm="",
        )
    )
    diff_path = snapshot_dir / f"{int(time.time_ns())}_{path.name}.diff"
    diff_path.write_text(diff, encoding="utf-8")
    return _result(
        "write_file",
        "ok",
        output=f"wrote {path.relative_to(Path.cwd()).as_posix()}",
        path=path.as_posix(),
        snapshot_path=snapshot_path.as_posix(),
        diff_path=diff_path.as_posix(),
    )


def _shell(args: dict[str, Any]) -> dict[str, Any]:
    command = str(args.get("command", ""))
    timeout_s = float(args.get("timeout_s") or 60.0)
    if not command.strip():
        return _result("shell", "error", error="shell command is required")
    completed = subprocess.run(
        command,
        cwd=Path.cwd(),
        shell=True,
        text=True,
        capture_output=True,
        timeout=timeout_s,
    )
    return _result(
        "shell",
        "ok" if completed.returncode == 0 else "error",
        output={
            "stdout": completed.stdout[-12000:],
            "stderr": completed.stderr[-12000:],
            "exit_code": completed.returncode,
        },
        command=command,
    )


def _resolve_cwd_path(path_value: str) -> Path:
    if not path_value.strip():
        raise PermissionError("path is required")
    cwd = Path.cwd().resolve()
    path = Path(path_value)
    if not path.is_absolute():
        path = cwd / path
    resolved = path.resolve()
    try:
        resolved.relative_to(cwd)
    except ValueError as exc:
        raise PermissionError(f"path {path_value!r} is outside the current workspace") from exc
    return resolved


def _result(tool: str, status: str, **extra: Any) -> dict[str, Any]:
    return _jsonable({"tool": tool, "status": status, **extra})


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_jsonable(item) for item in value]
        return str(value)
