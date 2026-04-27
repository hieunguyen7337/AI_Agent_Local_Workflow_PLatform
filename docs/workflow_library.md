# Workflow Library Conventions

YAML `GraphSpec` files in `workflows/*.yaml` remain the only workflow authoring surface. Do not add a JSON index, Python workflow module, or separate manifest to organize the library.

The library is discovered from the flat workflow directory. `GET /api/workflows` returns `id`, `name`, `description`, `category`, `tags`, `template`, validation health, source path, and static graph facts; the frontend selector groups by category and filters locally across those fields.

## File Layout

Keep workflow specs flat under `workflows/` for now:

```text
workflows/
  coder_tester.yaml
  linear_rag.yaml
```

Use lowercase snake_case file names. The workflow id is the file stem, so `workflows/linear_rag.yaml` is selected and referenced as `linear_rag`.

Subdirectories are intentionally deferred. Reconsider them only when flat discovery becomes hard to navigate even with category and tag metadata, because path-like workflow ids would affect the loader, CLI, eval fixtures, subgraph references, API routes, and frontend selection.

## Metadata

Every workflow should include these top-level fields near `name` and `description`:

```yaml
category: rag
tags:
  - retrieval
  - synthesis
template: false
```

`category` is the primary UI grouping. Use one short lowercase value.

Recommended categories:

- `approval` - workflows that pause for human review or decisions.
- `coding` - coder/tester, repair, or code generation loops.
- `orchestration` - routers, supervisors, fanout, aggregation, or multi-agent coordination.
- `rag` - retrieval and grounded synthesis workflows.
- `subgraph` - wrappers or reusable child-workflow examples.
- `template` - copyable starter workflows marked with `template: true`.
- `general` - default for uncategorized workflows.

`tags` are secondary search hints. Keep tags lowercase, concise, and behavior-oriented, such as `retrieval`, `loop`, `fanout`, `testing`, or `human-review`.

## Health Signals

Workflow summaries include static health fields:

- `validation_status` - `valid` or `invalid` after loading through `GraphSpec`.
- `validation_errors` - error text for broken YAML, invalid specs, or invalid subgraph references.
- `source_path` - the local YAML file path under the workflow root.
- `facts` - node, edge, loop, approval-node, and subgraph-node counts.

Invalid specs remain listed so contributors can find and repair them. Health does not include eval fixture presence, baseline freshness, or latest run status; those are runtime/evaluation quality signals, not source-library discovery metadata.

## Templates

A reusable workflow template is still a normal YAML `GraphSpec` file under `workflows/*.yaml`. Mark it with:

```yaml
template: true
template_parameters:
  - key: user_input
    description: Primary task text supplied when the copied workflow runs.
    state_key: user_input
    example: Summarize this note in three bullets.
category: template
tags:
  - starter
  - copyable
  - parameterized
```

Templates must stay executable and valid as ordinary workflows. Do not create a separate template directory, JSON index, manifest, or non-YAML authoring format.

The copy flow is explicit and audited:

1. Select a workflow marked `template: true`.
2. Use the Inspect workbench Copy template form.
3. Provide a lowercase snake_case `new_workflow_id` and optional metadata overrides.
4. Confirm the write.

The UI validates `new_workflow_id` locally before submit. It must be lowercase snake_case, start with a letter, and not already exist in the workflow list. The backend remains the final write authority and still rejects invalid or existing ids.

The API endpoint is `POST /api/workflows/{workflow}/copy-template`. It accepts `new_workflow_id`, optional `name`, `description`, `category`, `tags`, and `accepted_by`. The source must validate and have `template: true`. The new file is written to `workflows/<new_workflow_id>.yaml` with copied nodes, edges, loops, budget, and prompts, but with `template: false`. An audit entry is written under `runs/spec_audit/<new_workflow_id>/<timestamp>/audit.json` with the source template, target path, reviewer, and SHA256 of the written YAML. After copy, the UI selects the new workflow and shows source/audit paths so contributors can continue through normal source review or proposal flow.

## Template Parameterization

Parameterization is convention-only for now. Use the existing prompt/state placeholder style already used by runtime nodes:

```yaml
user_prompt_template: |-
  Task:
  {user_input}
```

Placeholders should be explicit, human-readable, and tied to normal workflow state keys such as `user_input`, `context`, or node output keys. Keep them visible in prompt text or mapping fields so humans and LLMs can understand what should be customized.

Do not introduce `${...}`, Jinja syntax, environment-variable placeholders, secret placeholders, preprocessing, or template-specific substitution rules. Copying a template preserves prompts and placeholders unchanged. After copy, the new `template: false` workflow should be customized through the same source inspection, YAML proposal, diff, eval, and apply flow as any other canonical workflow.

Templates can also declare documentation-only parameter metadata:

```yaml
template_parameters:
  - key: user_input
    description: Primary task text supplied when the copied workflow runs.
    state_key: user_input
    example: Summarize this note in three bullets.
```

This metadata is valid only on `template: true` workflows. It helps the UI explain expected inputs before copy, but it must not substitute text, rewrite prompts, enforce runtime input schemas, read secrets, or introduce a preprocessing step. When a template is copied, the new workflow is written with `template: false` and `template_parameters: []`.

## Adding A Workflow

1. Add one YAML file under `workflows/`.
2. Set `name` to the same snake_case id unless there is a strong reason to show a friendlier display name.
3. Add a one-line `description`, one `category`, a small `tags` list, and `template: false` unless the workflow is intentionally copyable.
4. Validate through `GraphSpec` by running the backend tests.
5. Add eval fixtures when the workflow behavior should be regression-tested.
