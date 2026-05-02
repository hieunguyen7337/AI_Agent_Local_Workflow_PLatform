# Run Artifacts

Execution runs are stored under:

```text
runs/workflows/<workflow>/<YYYYMMDD>/<run_id>/
```

Run ids are intentionally readable and sortable:

```text
run_YYYY_MM_DD_HHhMMmSSs_AEST_<workflow>_<8hex>
```

Example:

```text
run_2026_04_25_19h12m30s_AEST_coder_tester_a1b2c3d4
```

## Files

- `run_manifest.json` - start here. It summarizes workflow, run id, timestamps, status, error, and key artifact paths.
- `audit.json` - human-readable final state summary for a normal workflow run.
- `node_events.jsonl` - human-readable node inputs/outputs, prompts, model responses, tool kwargs, retriever outputs, timing, and errors.
- `spans.jsonl` - human-readable span stream, one JSON object per line. Use a text editor for quick scanning.
- `telemetry.db` - SQLite database for run summary and span rows. This is the main inspection database.
- `checkpoints.db` - internal SQLite database used by LangGraph checkpointing for replay/resume boundaries. Prefer `audit.json`, `node_events.jsonl`, the app, or replay CLI over manually reading it.
- `approval.json` - pending approval prompt, targets, and review state snapshot.
- `approval_decision.json` - reviewer decision, comment, timestamp, and continuation run id.
- `approval_resume.json` - continuation lineage after an approval decision.
- `parent_run.json` - child subgraph run pointer back to its parent run.
- `subgraphs/*.json` - parent run pointers to child subgraph runs.
- `pending_subgraph_approval.json` - written in the parent run dir when a child subgraph pauses on an approval node; links to the child pending run and carries the parent state snapshot needed for continuation.
- `subgraph_decision.json` - written in the source parent run dir after the child approval is decided; links to the child continuation run and the forked parent continuation run.
- `subgraph_resume.json` - written in the parent continuation run dir after the parent resumes; links back to the source parent run and child decision.

Do not edit `.db` files by hand unless intentionally repairing local artifacts.

Dataset evals are stored as one compact audit folder:

```text
runs/evals/<workflow>/2026_04_30_09h32m43s_AEST_<workflow>_100q_500g/
```

Eval folders contain `eval_summary.json`, `rows.jsonl`, `failed_rows.jsonl`, `node_events.jsonl`, `manifest.json`, and `telemetry.db`. Dataset eval rows do not create per-row `checkpoints.db` files.

## Reading SQLite Files

Recommended options:

- GUI: DB Browser for SQLite.
- CLI: `sqlite3` if installed.
- Python fallback: built-in `sqlite3`, no extra package needed.

List tables:

```powershell
sqlite3 .\runs\workflows\coder_tester\20260425\<run_id>\telemetry.db ".tables"
```

View run summary:

```powershell
sqlite3 .\runs\workflows\coder_tester\20260425\<run_id>\telemetry.db "select run_id, graph_name, status, cost_usd, latency_ms, error from runs;"
```

View spans:

```powershell
sqlite3 .\runs\workflows\coder_tester\20260425\<run_id>\telemetry.db "select node_id, node_kind, status, duration_ms, model, cost_usd from spans order by start_ns;"
```

Python fallback:

```powershell
.\.venv\Scripts\python -c "import sqlite3; con=sqlite3.connect(r'<path>\telemetry.db'); print(con.execute('select run_id, graph_name, status, cost_usd, latency_ms, error from runs').fetchall())"
```

`telemetry.db` is intended for human inspection and UI metrics. `checkpoints.db` is runtime state for replay/resume and is usually only useful through the replay command.
