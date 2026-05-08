# Future Scope

This file tracks deliberately deferred work beyond the current baseline. It should stay aligned with the product vision:

- YAML `GraphSpec` files are the simple source of truth for workflows.
- LLMs should be able to read, analyze, propose mutations, and iterate on that source of truth.
- Humans should be able to inspect the same spec as a graph, review node metadata, compare diffs, and evaluate behavior before accepting changes.
- The runtime should compile validated specs into executable LangGraph workflows without making Python workflow code the long-term authoring surface.

Entries should be pruned or moved when they graduate into implemented milestones.

## Current baseline

- **Canonical spec path** - YAML workflow files are the primary editable source of truth. Pydantic `GraphSpec` validates them and converts them into runtime `GraphMetadata`.
- **Graph and source inspection** - the frontend can show graph topology, full node metadata, raw YAML, and validation state for each workflow.
- **Read-only mutation proposals** - the app can ask an LLM to propose revised YAML, validate the proposal, and show a diff without writing files.
- **Read-only proposal evaluation** - valid proposed YAML can run through the eval harness in memory and compare behavior before any acceptance step.
- **Human-reviewed apply** - a valid proposal can be accepted into `workflows/*.yaml` after explicit confirmation, with an audit record and rollback snapshot.
- **Approval interrupts** - YAML `approval` nodes can pause a run, persist pending approval state locally, and expose that state through the API and run detail UI.
- **Approval decisions** - pending approvals can be approved or rejected, producing an immutable source decision artifact and a forked continuation run with lineage.
- **Approval workbench** - approvals are visible as a first-class frontend list with pending/decided filtering and source/continuation navigation.
- **Approval eval coverage** - approval workflows can be evaluated with fixture-provided decisions, continuation lineage checks, and approval path coverage metrics.
- **Reusable collapsed subgraphs** - YAML workflows can reference acyclic child workflows as collapsed nodes, execute them as nested local runs, and persist parent/child lineage artifacts.
- **Richer subgraph review** - reviewers can open a subgraph node's child graph/source context, navigate parent/child run lineage, and evaluate mapped subgraph outputs.
- **Multi-proposal optimization loop** - the app can generate multiple YAML mutation candidates, evaluate them under a shared cost cap, rank them, and recommend one for human review.
- **Rollback restore flow** - rollback snapshots can be listed, previewed, and restored after human confirmation, with a new audit record for restore actions.
- **YAML-only workflow loading** - CLI, replay, evals, API, graph export, and tests load canonical YAML specs without Python workflow module fallback. The Python builder remains only as an internal metadata/compiler helper.
- **Nested approval subgraphs** - child subgraph pauses on an approval node surface as parent `pending_approval`; child decisions auto-fork a parent continuation run; lineage links parent source, child source, child continuation, and parent continuation via `pending_subgraph_approval.json`, `subgraph_decision.json`, and `subgraph_resume.json`.
- **Approval subgraph eval coverage** - `approval_subgraph_wrapper` has fixture-driven eval support; the harness detects nested approval runs, routes `decide_approval` to the child run id, and scores against the parent continuation's final state.
- **Structured run artifacts** - execution runs are stored by workflow/date with readable run ids, manifests, artifact paths in the API/UI, and documentation for inspecting JSONL and SQLite files.
- **UI workbench cleanup** - the frontend keeps the graph canvas primary and organizes the review surface into Inspect, Run, Improve, and Recover modes so the source-of-truth loop is easier to follow.
- **Workflow library conventions** - `GraphSpec` includes `category` and `tags` metadata; `GET /api/workflows` scans flat `workflows/*.yaml` and returns `[{id, name, description, category, tags}]`; the frontend selector is API-driven, searchable, and grouped by category.
- **Workflow library health signals** - workflow summaries include validation status, validation errors, source path, and static graph facts so invalid YAML remains discoverable and repairable.
- **Reusable workflow templates** - YAML workflows can opt into template status with `template: true`; templates remain valid executable `GraphSpec` files, appear in `/api/workflows`, and can be copied through a human-confirmed UI/API flow that writes a new canonical workflow YAML with `template: false` plus a local audit record.
- **Template parameterization conventions** - templates may use normal runtime prompt/state placeholders such as `{user_input}`; copy preserves placeholders unchanged, and customization remains part of the normal YAML source/proposal/apply loop.
- **Schema-backed template parameter metadata** - templates can declare documentation-only `template_parameters` for expected inputs; the API/UI surface them read-only, and copied workflows clear them when becoming normal `template: false` specs.
- **Template copy ergonomics** - the copy UI validates target ids locally, warns on duplicate workflow ids before submit, keeps backend rejection as the write authority, and shows post-copy source/audit guidance for the new normal workflow.
- **Workflow library quality signals beyond validation** - workflow summaries include static eval fixture presence, fixture count, and baseline freshness derived from `evals/<workflow>/` files without latest run history or hosted state.
- **Workflow-as-function runtime** - validated YAML workflows can be called from Python through a stable local API that accepts full workflow state, preserves `user_input` compatibility, reuses the canonical LangGraph runtime, and returns status, final state, run id, artifact directory, error, cost, and latency.
- **Generalized dataset eval adapters** - workflows can be evaluated against local CSV, JSONL, or YAML datasets using `dataset_eval.yaml` configs with explicit row-to-state mappings and built-in scorers, all executed through the workflow-as-function runtime.
- **Telemetry-safe high-concurrency evals** - canonical CLI/API runs, fixture evals, dataset evals, and external integrations use `run_workflow_function`; multiple inputs can be submitted through `run_workflow_batch` or `POST /api/workflows/{workflow}/batch-run`; default local concurrency is 50, and a process-wide routing tracer keeps spans isolated in each run's own telemetry artifacts.
- **External local Python tool nodes** - YAML workflows can declare `kind: python_tool` nodes that call approved local Python functions; callables are checked against `python_tools.yaml` at spec validation time and again at execution time; `inputs` maps function kwargs to state keys; `output_state_key` captures the return value; stdout/stderr are captured in spans; the `person_reid_market1501` workflow demonstrates placeholder query-side specialists and per-specialist retrievers implemented in `backend/tools/reid_specialists.py`.
- **Multimodal LLM image inputs** - YAML `llm` nodes can declare `image_inputs` that bind local image paths from workflow state; runtime reads the file at node execution time and sends base64 `image_url` content parts to vision-capable providers without storing image bytes in YAML or workflow state.
- **Person-reID Market-1501 concept demo** - `person_reid_market1501` workflow with live OpenRouter boss/final-ranker calls + query-side specialists + per-specialist retrievers over offline description DBs (description/facets/tokens/text embeddings); `person_reid_market1501_eval` uses precomputed query descriptions for a 100-query/500-gallery partition. `evals/person_reid_market1501/` contains `dataset_eval.yaml`, partition and description DB builders, gitignored partition/DB/index paths, and a `map_cmc` retrieval scorer (mAP, CMC@1/5/10 with proper junk filtering). Pipeline includes `rrf_precompute` (weighted RRF with description channels) and `parse_final_ranking` (JSON parser + regex fallback -> `ranked_gallery_ids`). Region-aware facet retriever uses token-overlap and contradiction penalties; ablation tooling reports CMC@20 and description-correct/visual-miss disagreement at K={1,5,10,20}. Verified eval baseline remains **mAP=34.5%, CMC@1/5/10=35%** on the 100q/500g partition (placeholder visual/text/body stubs).

## Product usability

- **UI audit checklist** - maintain a manual checklist for validating graph/source/run/proposal/eval/apply/rollback/approval/subgraph/artifact flows before adding major backend capabilities.
- **State clarity** - keep raw runtime status separate from derived lifecycle status, especially for approval continuations, failed runs, and rollback/apply audit actions.
- **Workbench density** - prefer task-focused panels over stacking unrelated controls in one sidebar. The graph should remain the primary canvas while the workbench changes mode by user intent.
- **Artifact readability** - keep local artifact paths visible but compact, with documentation pointing users to the right JSONL or SQLite inspection method.

## Next product milestone

- **Real specialist model wrappers for person reID** - replace the three placeholder `python_tool` specialists in `person_reid_market1501` with real model implementations: TransReID or ViT for the visual specialist, CLIP or SigLIP for the text specialist, HMR2.0 or SMPLify for the body shape specialist. Requires a strategy for heavyweight model dependencies, GPU loading, and real model weights within the `python_tool` allowlist boundary.

## Workflow capabilities

- **Person re-identification full pipeline** - weighted RRF fusion, LLM output parsing, region-aware description facet scoring, and description-enriched final ranker are shipped (M5.21 baseline mAP=34.5% on 100q/500g); remaining deferred items are: (a) real model implementations and gallery DBs for visual/text/body-shape specialists (TransReID/ViT, CLIP/SigLIP, HMR2.0/SMPLify); (b) dynamic boss dispatch via LLM tool-calling so the orchestrator selects which specialists to run at runtime; (c) k-reciprocal re-ranking (Zhong et al., +5–8% mAP); (d) full Market-1501 evaluation protocol with 3,368 queries × 19,732 gallery, per-query proper junk filtering, and multi-query feature averaging. **Critical implementation note**: new `output_state_key` values used by `python_tool` or `llm` nodes must be declared in `backend/runtime/state.py` `WorkflowState` — LangGraph silently drops updates to undeclared keys.
- **Workflow library quality repair actions** - guide contributors toward running evals or updating baselines from stale/missing library quality states. Keep actions explicit and human-triggered; do not auto-run evals from discovery.
- **Inline subgraph editing** - child graph inspection is read-only; editing still happens through the YAML proposal/apply loop.
- **Latest run status in library discovery** - latest run status remains deferred because it mixes runtime history with static workflow library metadata.
- **Automatic eval repair from discovery** - workflow library quality signals are read-only. Buttons or links may guide users next, but discovery must not automatically run evals or update baselines.
- **Template parameter execution** - wizard-driven substitution, required runtime input schemas, and placeholder replacement are deferred until the project has enough copied-template usage to justify moving beyond read-only metadata.

## Technical debt and compatibility

- **CLI `--set` deep overrides** - M1 supports one level deep only (`--set node.field=value`). Add deeper override syntax only when real use cases appear.
- **Spec patch format** - current proposals use full YAML rewrites plus unified diffs. If smaller proposal payloads become necessary, design YAML-native constrained edit operations instead of adding another source format.
- **Workflow subdirectories** - flat `workflows/*.yaml` remains the canonical layout. Reconsider subdirectories only if category/tag navigation is insufficient, because path-like workflow ids would affect loader, CLI, eval fixtures, subgraph references, API routes, and frontend selection.
- **OTEL GenAI semconv churn** - attribute names are isolated in `telemetry/genai_attrs.py`; update only when the upstream convention stabilizes.

## Explicitly rejected

- Custom DSL with `@graph` decorators or `>>` operator overloading. One primary authoring surface: YAML workflow specs validated by `GraphSpec`.
- Broad cloud abstraction layer for LLM providers. Keep direct adapters unless a real integration need appears beyond OpenRouter + OpenAI.
- Mid-node budget cancellation as a required platform behavior. Budget enforcement intentionally happens after a node finishes and before dispatching the next node.
- Hosted tracing backends such as LangSmith or Studio. Local-first is a hard constraint.
- Haystack or other RAG-specific frameworks as the platform substrate. LangGraph is broader and remains the runtime layer.
- Graphviz for static export. Mermaid covers the current need and renders on GitHub natively.
