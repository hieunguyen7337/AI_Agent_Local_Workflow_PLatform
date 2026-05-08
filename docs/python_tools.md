# Python Tool Nodes

`python_tool` is a YAML node kind that calls an approved local Python function directly from a workflow, without going through an LLM. It is the standard way to integrate local deterministic transforms, parsers, fusers, DB lookups, and small local computations into a workflow.

Hosted model/API calls do not belong in `python_tool` nodes. Use first-class workflow node kinds such as `llm` and `embedding` for OpenAI/OpenRouter model calls so the graph shows the full pipeline.

## Allowlist

Only callables listed in `python_tools.yaml` at the repo root may be used. Add a new entry before referencing it in a workflow:

```yaml
# python_tools.yaml
allowed_callables:
  - backend.tools.reid_specialists.weighted_reciprocal_rank_fusion
  - mypackage.mymodule.my_function
```

The allowlist is loaded once at first use and cached. Missing or malformed file → empty allowlist (all `python_tool` nodes fail validation). This is the security boundary — functions still run in-process with full Python privileges.

## YAML node shape

```yaml
- id: my_node
  kind: python_tool
  callable_path: mypackage.mymodule.my_function   # must be in python_tools.yaml
  inputs:
    arg_name: state_key                            # function kwarg ← state[state_key]
    another_arg: another_state_key
  output_state_key: result_key                     # state[result_key] ← return value
```

`inputs` maps function keyword-argument names to workflow state keys. If a state key is missing at runtime, the call raises `KeyError` and the run fails. `output_state_key` receives the function's return value directly — lists, dicts, strings, numbers are all fine.

## Function signature conventions

```python
def my_function(arg_name: SomeType, another_arg: AnotherType) -> ReturnType:
    ...
```

- Functions must be pure keyword-callable (no positional-only args).
- Any importable Python type is valid as argument or return type.
- Functions should be deterministic or clearly documented if they are not.
- Functions must not call hosted model APIs such as OpenRouter or OpenAI; use a typed workflow node instead.
- Do not print to stdout/stderr in production functions — output is captured by the node and stored in spans, not visible in the run log.

## Error behavior

- Exceptions from the function propagate unchanged, marking the run as `error`.
- The OTEL span gets `STATUS=ERROR` with the exception message.
- stdout/stderr are captured via `contextlib.redirect_stdout/redirect_stderr` and stored as span attributes (truncated to 4 KB).

## Adding a new tool

1. Implement the function in `backend/tools/` (or another importable location).
2. Add its dotted path to `python_tools.yaml`.
3. Reference it in the workflow YAML with `kind: python_tool` and the appropriate `inputs` / `output_state_key`.
4. Add a test confirming the function works and the spec validates.

## Reference example

Current `person_reid_market1501` tools are local only: start pass-through, query description DB lookup, query description-embedding lookup, eval query visual-embedding lookup, description-facet gallery scoring, weighted reciprocal-rank fusion (visual + description semantic + description facets), and final-ranker output parsing. Hosted Gemini embedding calls are represented as `embedding` nodes, and vector search is represented as `vector_retriever` nodes.
