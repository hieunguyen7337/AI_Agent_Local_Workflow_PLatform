# Claude Code YAML Workflow Handover

## Purpose

This handover summarizes the session work on `claude_code_yaml_workflow` and records the target architecture we are aiming to model: a Claude Code-style local coding agent represented as an inspectable YAML `GraphSpec`.

The product constraint remains unchanged: YAML is the canonical workflow authoring surface. Do not add Python workflow definitions, JSON workflow formats, decorators, or another DSL.

## Target Architecture: Full Claude Code Mental Model

Claude Code is best understood as a small ReAct-style agent loop surrounded by a large deterministic runtime shell. The core loop is:

1. Assemble context.
2. Call the model.
3. Parse either final answer or one tool request.
4. Route the tool request.
5. Apply permission policy.
6. Run hooks.
7. Execute the tool.
8. Compact context if needed.
9. Decide whether to stop or continue.

The surrounding architecture is where most of the real system complexity lives:

- **Startup/bootstrap**: pre-initialization checks and config loading happen before context assembly. In Claude Code this includes policy/config reads, auth/keychain checks, API preconnect, feature flags, migrations, and doctor checks.
- **Context assembly**: project instructions, global memory, session state, history, runtime config, and tool results are assembled before model calls.
- **Plan mode**: optional planning step pauses before execution and requires user approval.
- **Agent model loop**: the LLM emits either a final response or a structured tool request. The model should not directly execute work.
- **Tool routing**: tool requests are parsed and routed to filesystem, shell, network/MCP, subagent, or internal tools.
- **Permission system**: deny-first, policy-stacked, and mode-dependent. Real Claude Code has multiple permission modes and enterprise/project/user policy precedence.
- **Hooks**: deterministic event interceptors around lifecycle events, especially `PreToolUse` and `PostToolUse`. In Claude Code they are configured outside the graph in settings. Official docs describe `PreToolUse` as running after tool parameters are created but before the tool call is processed, and `PostToolUse` as running immediately after successful tool completion. See:
  - https://docs.claude.com/en/docs/claude-code/hooks
  - https://docs.claude.com/en/docs/claude-code/hooks-guide
- **Tool execution**: tools carry metadata such as bucket, reversibility, concurrency safety, permission requirements, sandbox compatibility, and checkpoint behavior.
- **Subagents**: real subagents are dynamic fan-out/fan-in child agents with isolated context windows. They return summary-only results to avoid parent context blowup.
- **Compaction**: real compaction is a staged pipeline, not just truncation. The conceptual stages are rolling summary, tool-result pruning, message merging, semantic deduplication, and checkpoint anchoring.
- **Memory tiers**: session memory, project memory, and global memory are different. Direct persistent writes should be explicit and auditable in this project.
- **MCP registry**: external tools are discovered through configured MCP servers and health-checked at startup. Tool definitions should not simply be prompt text.
- **Termination**: stop can come from final answer, user interrupt, budget exhaustion, max iterations, hard permission block, or runtime error. Termination should write artifacts.

## Current YAML Workflow Shape

The canonical workflow is:

`workflows/claude_code_yaml_workflow.yaml`

Current graph entry is `startup`, followed by:

```text
startup
  -> context_loader
  -> planner
  -> plan_normalize
  -> plan_approval
  -> agent_model
  -> agent_response_parser
  -> tool_route
  -> permission_gate
  -> permission_route
  -> tool_approval
  -> pre_tool_hooks
  -> tool_executor
  -> post_tool_hooks
  -> tool_history_digest
  -> subagent_gate
  -> subagent_plan
  -> subagent_context
  -> subagent_spawn
  -> subagent_join
  -> subagent_summarize
  -> compaction_gate
  -> context_compactor
  -> loop_gate
```

The visible ReAct loop is:

```text
loop_gate -> agent_model
```

with `max_iterations: 50`.

**Plan linting:** `planner` emits a draft `task_plan`; `plan_normalize` (LLM, temperature 0) rewrites it into numbered Markdown only so bogus pseudo-tool output is less likely to reach `plan_approval` / the agent loop.

**Bounded tool context:** After each tool run, `tool_history_digest` (`python_tool` → `backend.tools.agent_workflow.tool_history_digest`) writes `tool_result_digest` with capped one-line summaries. `agent_model` consumes `{tool_result_digest}` instead of raw `{tool_result_history}`.

**CI / unattended variant:** `workflows/claude_code_yaml_workflow_ci.yaml` is the same graph with `plan_approval` removed (`plan_normalize` → `agent_model`). Use it only for automation or tests; the canonical product path keeps human plan approval.

## Implemented Node Kinds

The session added these node kinds to `GraphSpec`:

- `agent_startup`
- `agent_context`
- `agent_model`
- `agent_response_parser`
- `permission_gate`
- `hook_runner`
- `tool_executor`
- `context_compactor`
- `subagent_orchestrator` (legacy; optional single-node stub)
- `subagent_plan`
- `subagent_context`
- `subagent_spawn`
- `subagent_join`
- `subagent_summarize`
- `memory_writer`
- `python_tool` (used here for deterministic `tool_history_digest`)

The frontend type/display layer was updated so these node kinds render in the graph workbench.

## Implemented Runtime Behavior

Runtime implementation lives primarily in:

- `backend/runtime/nodes/agent.py`
- `backend/runtime/agent_tools.py`
- `backend/runtime/executor.py`
- `backend/runtime/state.py`

Implemented behavior:

- `agent_startup` writes `runtime_config` and `startup_checks.json`.
- `agent_context` consumes `runtime_config`, reads configured files, and can include memory source text.
- `agent_model` calls the configured provider/model and writes `agent_messages.jsonl`.
- `agent_response_parser` parses strict JSON into `agent_route`, `tool_request`, `final_answer`, and `agent_should_stop`.
- `permission_gate` supports deterministic policy modes and writes `permission_decisions.jsonl`.
- `hook_runner` runs configured shell hooks, with blocking failure support.
- `tool_executor` supports local tools:
  - `read_file`
  - `glob`
  - `search`
  - `write_file`
  - `shell`
  - `subagent`
  - `mcp_call`
- `write_file` snapshots prior content and writes diffs under run artifacts.
- `mcp_call` is recognized but returns a structured unavailable result unless future MCP execution is added.
- `context_compactor` implements deterministic staged compaction for pruning, message merging, and checkpoint anchoring. LLM rolling summary and semantic dedup are represented but skipped unless configured later.
- Subagent pipeline (`subagent_plan` → `subagent_context` → `subagent_spawn` → `subagent_join` → `subagent_summarize`) normalizes the `subagent` tool request, builds isolated child inputs, runs `claude_code_subagent_workflow` via nested `run_graph` with `run_dir_override` under the parent run’s `subagent_runs/`, joins child status and answer excerpts, and appends a **summary-only** record to `subagent_results` and `tool_result_history`. Artifacts: `subagent_lineage.jsonl`, `subagent_summary.json`, and per-child `parent_run.json` in each child run directory.
- `subagent_orchestrator` remains in the schema as a backward-compatible stub (unsupported summary) if older YAML references it.
- `memory_writer` writes memory suggestions as artifacts by default. Direct global writes are possible in code but should be guarded by explicit approval in any workflow that uses them.
- `termination.json` records termination status/reason.
- `run_graph` calls `load_dotenv()` once per run (no-op if `python-dotenv` is missing) so raw `python -c` / library callers pick up `.env` the same way as the CLI.

## Optional real-API stress (manual / nightly)

Use the canonical workflow in the UI so you can approve the plan, or use the CI spec only in trusted environments.

```powershell
cd C:\Users\Admin\Documents\AI_club_projects\AI_Agent_Local_Workflow_PLatform
.\.venv\Scripts\python -m backend.cli.main serve --host 127.0.0.1 --port 8000
```

In another shell:

```powershell
cd C:\Users\Admin\Documents\AI_club_projects\AI_Agent_Local_Workflow_PLatform\frontend
npm run dev
```

Open `http://127.0.0.1:5173`, select `claude_code_yaml_workflow`, run with a challenging task, approve the plan when prompted, then inspect `runs/workflows/claude_code_yaml_workflow/<date>/<run_id>/agent_messages.jsonl` and `termination.json`. For API-backed runs, keep keys in a repo-root `.env`; `run_graph` / CLI load it automatically.

## Important Bug Found and Fixed

While smoke-testing approvals, `plan_approval` repeatedly paused because `plan_approval_decision` was not declared in `WorkflowState`. LangGraph dropped the state key during continuation.

Fix applied:

- Added `plan_approval_decision` to `backend/runtime/state.py`.

After this fix:

- Normal run paused at `plan_approval`.
- Approval continuation completed successfully.
- Tool approval path paused at `tool_approval`.
- Approval continuation executed a safe `write_file` and completed successfully.

## Current Subagent Reality

The canonical parent workflow uses an explicit subagent pipeline (see graph shape above).

Current behavior:

- If the model requests `subagent`, `tool_executor` still records a `requested` capture in `tool_calls.jsonl` (same as other tools).
- `subagent_gate` routes to `subagent_plan` when `subagent_requested` is true.
- `subagent_spawn` loads `workflows/claude_code_subagent_workflow.yaml` and runs each delegated task as a **nested** `run_graph` call. Child run directories live under `runs/.../<parent_run_id>/subagent_runs/<child_run_id>/` (via `run_dir_override`), with `parent_run.json` in each child dir pointing back to the parent run.
- `subagent_summarize` writes compact aggregate text for the parent loop; full child transcripts stay in the child run artifacts.
- `SubagentSpawnNodeConfig.execute_children: false` skips nested runs and marks tasks as `planned` (useful for dry-run or policy-off modes).

Child workflow: `workflows/claude_code_subagent_workflow.yaml` — same coding-agent primitives as the parent but **no** plan-approval gate, **`permission_gate` mode `auto_approve`**, and **no** `subagent` / `mcp_call` tools on the child tool list (avoids nested delegation and MCP until wired).

Remaining gaps / future work:

- True fan-out parallelism (`max_parallel`) and richer task specs from the parent model.
- Per-task `model_override` plumbed into child `agent_model` (today overrides are visible in the delegated briefing text only).
- Custom subagent registry (Claude Code-style typed subagents) as YAML-driven metadata.

## Hooks: Meaning and Applicability

`pre_tool_hooks` and `post_tool_hooks` are intentionally present.

In this YAML workflow:

- `pre_tool_hooks` runs after permission approval and before tool execution.
- `post_tool_hooks` runs after the tool completes.

They are meant for deterministic checks that should not depend on LLM compliance:

- secret scanning before writes
- protected branch checks
- file size guards
- command validation
- formatters after writes
- lint/tests after edits
- prompt-injection scanning on tool output
- audit or notification scripts

Claude Code implements hooks as cross-cutting event interceptors configured in settings. This project makes them visible graph nodes because graph inspectability is more important here than exact hidden-interceptor fidelity.

## Permission System Status

Current permission modes:

- `default`
- `plan`
- `ask`
- `auto_edit`
- `auto_approve`
- `sandbox`
- `project_rules`
- `enterprise`

Implemented deterministic behavior:

- read tools usually pass
- write/shell/MCP tools may ask or deny depending on mode
- local `.claude/agent_policy.json` can provide simple `allow_tools` / `deny_tools`
- `enterprise` hard-blocks permission-required tools unless future local policy support is expanded
- `escalate_on` can force approval for conditions like `network_egress` or `shell_with_sudo`

Not implemented:

- real MDM policy integration
- ML classifier
- real sandbox isolation
- full CLAUDE.md allow/deny parsing

These are represented as schema/runtime-config concepts only.

## Tool Inventory Status

The workflow now uses structured tool metadata rather than only tool names.

Metadata includes:

- `bucket`
- `reversible`
- `is_concurrency_safe`
- `permission_required`
- `checkpoint_before_write`
- `windows_fallback`

The actual local tool registry is in:

`backend/runtime/agent_tools.py`

## MCP Status

MCP is represented but not actually connected.

Current behavior:

- `agent_startup` includes an `mcp_registry`.
- Startup records configured server metadata and marks servers not connected.
- `mcp_call` is a known tool.
- `mcp_call` returns `unavailable` unless future MCP execution is implemented.

Future implementation should:

- load local MCP server config
- health-check servers at startup
- route `mcp_call` through the registry
- write MCP request/response artifacts
- preserve permission gating around network or external-service calls

## Compaction Status

Current deterministic compaction:

- preserves last N tool results
- truncates verbose tool outputs
- merges consecutive same-role messages
- replaces write-file output with diff/snapshot references
- records skipped stages for model-based rolling summary and semantic dedup

Future implementation:

- optional cheap-model rolling summary
- optional semantic dedup with local embeddings or configured provider
- clearer UI display of compacted context and dropped material

## Memory Status

Current memory behavior:

- `agent_context` can read configured memory sources.
- `memory_writer` writes suggestions to `memory_suggestions.json`.
- Direct writes are not used in the canonical workflow.

Design decision:

- Do not auto-write global memory by default.
- If persistent memory writes are needed, route through approval before `memory_writer` with `write_mode: direct`.

## Verification Performed

After implementation:

- `.\.venv\Scripts\python -m pytest backend\tests -q`
  - Passed: `250 passed` (includes subagent pipeline tests)
- `cd frontend; npm run build`
  - Passed

Manual smoke tests:

- Ran `claude_code_yaml_workflow`.
- Approved `plan_approval`.
- Continuation completed `ok`.
- Started at `tool_approval` with a safe local write request.
- Approved `tool_approval`.
- Continuation completed `ok`.
- Safe write succeeded and produced snapshot/diff artifacts.

## Recommended Next Steps

1. Add MCP config loading and health-checking.
2. Add approved direct memory-write workflow path.
3. Add frontend run-detail panels for:
   - `startup_checks.json`
   - `permission_decisions.jsonl`
   - `tool_calls.jsonl`
   - `context_compactions.jsonl`
   - `termination.json`
   - subagent child run summaries

## Files Added or Touched in This Session

Important files:

- `workflows/claude_code_yaml_workflow.yaml`
- `workflows/claude_code_yaml_workflow_ci.yaml`
- `workflows/claude_code_subagent_workflow.yaml`
- `backend/tools/agent_workflow.py`
- `backend/builder/nodes.py`
- `backend/graphspec/models.py`
- `backend/graphspec/__init__.py`
- `backend/runtime/nodes/agent.py`
- `backend/runtime/agent_tools.py`
- `backend/runtime/executor.py`
- `backend/runtime/state.py`
- `backend/tests/test_claude_code_yaml_workflow.py`
- `frontend/src/types.ts`
- `frontend/src/components/GraphView.tsx`

## Handoff Warning

The current workflow is runnable and approval-tested, but it is not yet a full Claude Code clone. Nested subagent execution is implemented as summary-oriented child YAML runs; parallelism, custom subagent types, and model override plumbing are still simplified compared to Claude Code.
