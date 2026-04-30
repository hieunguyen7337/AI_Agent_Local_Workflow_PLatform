# UI Vision Audit

Use this checklist before adding major backend workflow capabilities. The goal is to keep the interface aligned with the project vision: YAML is the source of truth, the graph is the primary human view, LLMs propose YAML mutations, humans review evidence, and accepted changes become executable workflows.

## Scope

- Workflows: `coder_tester`, `linear_rag`, `supervisor_loop`, `dispatch_aggregate`, `approval_review`, `rag_subgraph_wrapper`, `approval_subgraph_wrapper`, `simple_llm_template`
- Frontend: `http://127.0.0.1:5173`
- Backend: `http://127.0.0.1:8000`
- Workbench modes: `Inspect`, `Run`, `Improve`, `Recover`

## Setup

```powershell
# Terminal 1
.\.venv\Scripts\python -m backend.cli.main serve --host 127.0.0.1 --port 8000

# Terminal 2
cd frontend
npm run dev
```

## Pass Criteria

- The graph remains visible and usable while the right workbench changes mode.
- No mode requires users to know hidden backend details before acting.
- YAML, graph metadata, diffs, eval evidence, approval state, rollback snapshots, and artifact paths are readable without horizontal page overflow.
- Empty, loading, error, pending, decided, completed, and failed states explain what happened and what can be done next.
- No UI action mutates `workflows/*.yaml` without explicit human confirmation.

## Inspect

- Select each workflow and verify the graph renders.
- Confirm the workflow selector groups workflows by category and the search box filters by id, name, description, category, and tags.
- Confirm the library summary shows total, valid, and invalid counts, and that the selected workflow shows node/edge/loop/approval/subgraph facts.
- Confirm the library summary shows fixture-ready and fresh-baseline counts.
- Confirm selected workflow health includes fixture count/status and baseline status.
- Search for `fixtures`, `baseline`, `fresh`, `stale`, `missing`, and a fixture/baseline path fragment; confirm matching workflows remain discoverable.
- With temporary eval files, confirm fixture-present/missing/invalid and baseline-fresh/stale/missing states render without breaking selector grouping.
- Confirm template workflows are marked as `(template)` in the selector, can be found by searching `template`, and show `template` in the selected-workflow health/facts line.
- With a temporary invalid workflow spec, confirm it remains visible as invalid and can be found by searching `invalid` or part of the validation error text.
- Click every node kind used by the reference workflows: `llm`, `tester`, `retriever`, `gate`, `router`, `approval`, `subgraph`, `START`, `END`.
- Confirm selected node metadata includes the fields needed to understand behavior, such as model/provider, prompts, state keys, routes, loop limits, retriever corpus, tester mode, and subgraph mappings.
- Open the raw YAML source and confirm it matches the selected workflow.
- Confirm validation status is visible and understandable.
- Select `simple_llm_template`, confirm the Copy template form appears in Inspect, and confirm the copy button remains disabled until a new workflow id and confirmation are provided.
- Confirm the Copy template form shows expected inputs from read-only `template_parameters`.
- Confirm the Copy template form explains that prompt placeholders such as `{user_input}` are copied unchanged and customized afterward through normal source/proposal review.
- Enter an invalid workflow id such as `Bad-Id` and confirm the form shows a validation message and keeps Copy disabled.
- Enter an existing workflow id such as `coder_tester` and confirm the form shows duplicate-id feedback and keeps Copy disabled.
- Copy the template to a new lowercase snake_case id, then confirm the new workflow appears, is selected, validates, is not marked as a template, has no active template parameters, and has an audit entry under `runs/spec_audit/<new_workflow_id>/`.
- After copying, confirm the workbench shows source and audit paths plus guidance that the copy is now a normal workflow.
- For `rag_subgraph_wrapper`, select the subgraph node and open the child graph/source view without changing the parent workflow selector.

## Run

- Start one run for each reference workflow.
- Confirm the created run is selected automatically.
- Confirm recent runs show lifecycle status, cost, latency, and continuation hints without cramped columns.
- Open run detail and verify raw runtime status is separate from derived lifecycle status when they differ.
- Confirm the Artifacts section shows compact paths and points to `docs/run_artifacts.md`.
- For `approval_review`, confirm the pending approval appears in the approvals panel and run detail.
- Approve or reject one pending approval and confirm the source run shows decided status plus a continuation run link.
- For `rag_subgraph_wrapper`, confirm parent run detail links to the child run and child run detail links back to the parent.
- For `approval_subgraph_wrapper`, confirm the parent run surfaces pending child approval, deciding the child approval creates a parent continuation, and run detail exposes the nested lineage artifacts.

## Improve

- Generate a single mutation proposal for one workflow with a low-risk goal.
- Confirm the proposal shows validation status, summary, YAML, and diff.
- Evaluate the valid proposal and confirm pass rate, cost, latency, and artifact path are shown.
- Run multi-candidate optimization with a small candidate count and confirm candidates are ranked.
- Confirm a recommended candidate is clearly marked but not automatically applied.
- Apply only after explicit confirmation, then verify the graph/source refreshes.

## Recover

- Open rollback snapshots for a workflow that has an apply or restore audit record.
- Preview a snapshot and verify the diff is readable.
- Restore only after explicit confirmation.
- Confirm source YAML and graph refresh after restore.
- Confirm the restore action creates a new audit record.

## Layout Checks

- Test normal desktop width and a narrower desktop width.
- Check that tab labels, buttons, status badges, and table columns do not overlap.
- Check that long run ids, artifact paths, and validation errors wrap or truncate predictably.
- Confirm graph controls remain reachable after switching workbench modes.

## Failure State Checks

- Stop the backend and confirm graph/spec/run areas show clear connection errors.
- Submit an empty proposal goal and confirm validation prevents the request.
- Use an invalid proposed YAML response in mocked tests or dev tools and confirm validation errors remain visible.
- Confirm failed evals or provider errors do not clear the current proposal or selected run context.
