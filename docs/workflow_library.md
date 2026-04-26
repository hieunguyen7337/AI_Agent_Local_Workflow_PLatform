# Workflow Library Conventions

YAML `GraphSpec` files in `workflows/*.yaml` remain the only workflow authoring surface. Do not add a JSON index, Python workflow module, or separate manifest to organize the library.

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
```

`category` is the primary UI grouping. Use one short lowercase value.

Recommended categories:

- `approval` - workflows that pause for human review or decisions.
- `coding` - coder/tester, repair, or code generation loops.
- `orchestration` - routers, supervisors, fanout, aggregation, or multi-agent coordination.
- `rag` - retrieval and grounded synthesis workflows.
- `subgraph` - wrappers or reusable child-workflow examples.
- `general` - default for uncategorized workflows.

`tags` are secondary search hints. Keep tags lowercase, concise, and behavior-oriented, such as `retrieval`, `loop`, `fanout`, `testing`, or `human-review`.

## Adding A Workflow

1. Add one YAML file under `workflows/`.
2. Set `name` to the same snake_case id unless there is a strong reason to show a friendlier display name.
3. Add a one-line `description`, one `category`, and a small `tags` list.
4. Validate through `GraphSpec` by running the backend tests.
5. Add eval fixtures when the workflow behavior should be regression-tested.
