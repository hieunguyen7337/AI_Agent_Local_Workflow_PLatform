# Presentation Transcript + Presenter Notes
## QUT AI and ML Society — Agentic AI Workshop
### 12 Slides · 20 min presentation · 40 min Q&A and workshop

---

## CHANGE LOG

Updated for consistency with the current repository state:

- Slide 6 now describes the current five-node `linear_rag.yaml` pipeline: query analysis, embedding, vector retrieval, reranking, and synthesis.
- Slide 8 now describes Recover as a human-confirmed rollback restore that writes an audit entry, rather than a one-click restore.
- Slide 11 no longer says CSV/JSONL dataset evals are missing or planned; the repo now includes generalized dataset eval adapters for CSV, JSONL, and YAML.
- Slide 11 now lists the current next milestone as real person reID specialist model wrappers.

---

## SLIDE 1 — TITLE

### Slide text

**What If Your Agent Can Improve Itself?**

*A local-first pattern for auditable, human-reviewed agent workflow evolution*

QUT AI and ML Society  |  Agentic AI Workshop

*Local-First AI Workflow Platform*

---

### What to say

> "Welcome everyone. I want to start with a question most of you probably haven't thought about yet: what if the LLM powering your agent could actually read the workflow it's running inside — and propose changes to it?
>
> Not hallucinate changes. Not autonomously rewrite itself at runtime. Propose a diff. You review it. You run evals. You decide whether it lands.
>
> That's the pattern we're going to walk through today. It's not a new framework — it sits on top of LangGraph. It's a way of thinking about how you author, version, and evolve agent pipelines, and by the end of this session you'll have built and run it yourself."

---

### Context behind this slide

This slide's job is to reframe how the audience thinks about agent development before any content appears. Most developers in the room have built agents where the pipeline logic lives in Python — decorators, class methods, hardcoded prompt strings. The LLM at the centre of that pipeline has no awareness of the structure it's executing inside.

The question "what if your agent could rewrite its own pipeline" surfaces that gap without being confrontational. It also pre-sells the most compelling feature of the demo (the Improve tab, slides 9 and 10) so the audience has something to look forward to.

The subtitle deliberately includes "human-reviewed" to immediately counter the anxiety that always follows "rewrite its own pipeline" — a developer's first instinct is "that sounds dangerous." The subtitle reassures before the first question is asked.

Keep this slide on screen for no more than 30 seconds. The title is the hook — don't explain it yet.

---
---

## SLIDE 2 — THE FRAGMENTATION PROBLEM

### Slide text

**The Fragmentation Problem**

*Agent workflow logic is split across at least three separate surfaces at once*

**[Diagram: three source boxes converging to a central "no source of truth" box]**

| Box | Sub-label | Real-world examples |
|-----|-----------|---------------------|
| Python Decorators | Logic buried in code | LangChain (`@tool`, `@chain`), LangGraph (node functions), Haystack (`@component`), Semantic Kernel (`@kernel_function`) |
| JSON Configs | Schema separate from runtime | n8n (workflow JSON export), Flowise / Langflow (flow JSON), OpenAI Assistants API (assistant config object), AWS Bedrock Agents (CloudFormation JSON), Dify (workflow export) |
| Hosted Dashboard | State lives outside your stack | CrewAI Cloud, Zapier AI, Microsoft Copilot Studio, Make.com, Relevance AI, AgentOps |

↓ ↓ ↓ *(all three converge to:)*

**No single source of truth**

*Auditing what changed between runs means diffing three artifacts that were never designed to stay in sync.*

---

### What to say

> "Before we look at the solution, let's agree on the problem — because I think most of us have felt it without naming it.
>
> If you've built an agent pipeline, your workflow logic is almost certainly scattered across at least three places. If you use LangChain or LangGraph, the topology lives in Python — decorator chains, node functions, graph definitions in code. If you use n8n or Flowise, the workflow is a JSON document that the GUI generates and the runtime parses. If you're on CrewAI Cloud or Copilot Studio, the agent definition lives in a hosted dashboard that you don't version alongside your codebase.
>
> These aren't bad tools. But none of them were designed around a shared contract between the developer and the runtime. And none of them let the LLM at the centre of your pipeline read the instructions it's operating inside.
>
> The practical consequence is that when something breaks, or when you want to understand why a run two weeks ago behaved differently from today's, you have to cross-reference three different artifacts that were never designed to stay in sync. That's the fragmentation problem."

---

### Context behind this slide

This slide exists to validate the audience's existing frustration before introducing anything new. The goal is recognition, not revelation — the developer in the room should be nodding by the end of the first sentence.

The three categories are deliberately chosen to cover the three dominant paradigms in the current agent tooling landscape:

**Python Decorators (code-first):** This is the LangChain/LangGraph world. The workflow is expressed as Python code with decorators and class definitions. It's the most developer-friendly for people who live in editors, but the workflow structure is implicit — you can't extract a standalone description of "what the pipeline does" without reading the code. The LLM being orchestrated has zero access to it.

**JSON Configs (config-first):** This is the n8n / Flowise / OpenAI Assistants world. There is a schema, but it's typically auto-generated by a GUI or API client and not treated as a first-class document. Diffing JSON workflow exports across versions is painful. The schema is often undocumented and changes between tool versions.

**Hosted Dashboards (GUI-first):** This is the CrewAI Cloud / Copilot Studio / Zapier AI world. The workflow lives in a SaaS product. You have no local copy. Version history is whatever the platform exposes. Running evals against a proposed change requires deploying it, which is the change.

The "No single source of truth" box at the bottom is the punchline. All three approaches produce the same operational failure: when you need to understand what your pipeline is doing, or propose a change to it, or compare the behaviour of two versions, you have no single file to point to.

The real-world examples are important because they ground the abstract problem in tools the audience has actually used or evaluated. Hearing "n8n" or "Copilot Studio" makes the problem feel immediate rather than hypothetical.

---
---

## SLIDE 3 — THE READABILITY GAP

### Slide text

**The Readability Gap**

*The LLM being orchestrated cannot read its own workflow instructions*

**[Diagram: side-by-side comparison, TODAY vs THIS PATTERN]**

### TODAY column

- **LLM Agent**
  - ↓
  - **BLOCKED**
  - ↓
  - Workflow Instructions *(dimmed / inaccessible)*

*The LLM executes a pipeline it cannot inspect or reason about*

### THIS PATTERN column

- **LLM Agent**
  - ↕ reads / proposes
  - **YAML Spec**
  - ↓
  - LangGraph Runtime

*One file both humans and LLMs can read, diff, and propose changes to*

---

### What to say

> "This is the deeper consequence of fragmentation, and it's the one that surprised me most when I started thinking about it seriously.
>
> In every system on the left side of that last slide — decorators, JSON, hosted dashboard — the LLM doing the actual work has no idea what pipeline it's inside. It receives a prompt. It produces output. It has no access to the structure that routes its output to the next step, applies a budget constraint, decides whether to loop back, or determines when to escalate to a human.
>
> Think about what that means for improvement. If you want to make your RAG pipeline cheaper, you change the Python, or you edit the JSON, or you click around the dashboard. The LLM cannot participate in that conversation. It cannot say 'here's what I'd change' because it can't see what there is to change.
>
> The right side of this diagram shows what happens when the workflow lives in a YAML file that the LLM can read. Suddenly the LLM can be given the full spec and asked: 'Given what this pipeline is doing, how would you change it to reduce cost without losing quality?' It reads the spec. It proposes a diff. A human reviews that diff before it touches disk.
>
> That's the readability gap — and closing it is the entire point of the pattern we're about to walk through."

---

### Context behind this slide

Slide 2 established that the problem is fragmentation. Slide 3 deepens that into a second-order consequence: because the workflow is fragmented and buried in code or configs, the LLM at the centre of the system is completely blind to its own operating context.

This is a subtle but important distinction. Most developers accept that the LLM doesn't know about its pipeline — they've never thought of that as a bug. This slide reframes it as a design gap that has real consequences for iteration velocity and observability.

The "BLOCKED" label in the left column is intentionally harsh. The LLM is not partially informed — it has zero access. There is no API, no context injection, no mechanism by which a LangGraph node function or n8n workflow JSON passes the pipeline structure down to the LLM nodes inside it as readable instructions.

The right column is deliberately minimal. It doesn't explain how the YAML gets there or how the compile step works — that comes on slides 5 and 6. This slide's only job is to show that the relationship between the LLM and the spec can be bidirectional: the LLM reads it and proposes changes, rather than being a blind executor.

The phrase "reads / proposes" on the bidirectional arrow is important to say out loud — it's easy to miss that there are two arrows. The pink arrow (down, reads) and purple arrow (up, proposes) are doing different jobs.

---
---

## SLIDE 4 — WHERE THIS FITS

### Slide text

**Where This Fits**

| Layer | What It Is |
|-------|-----------|
| **LangGraph** | Runtime: executes the graph. This project runs on top of it. |
| **CrewAI / AutoGen** | Agent-centric orchestration, a different model entirely. |
| **This pattern** | A thin authoring and review layer on top of any graph runtime. |

**Best for** pipelines needing auditability, human approval, iterative LLM-driven improvement

**Not for** fully autonomous agents that evolve their own behaviour at runtime

*"This is not a new framework. It is a pattern that can be transplanted into any stack."*

---

### What to say

> "Before I go any further, I want to be very explicit about what this is and what it isn't — because the first instinct when you see a new workflow abstraction is to ask 'do I have to throw away LangGraph?'
>
> The answer is no. LangGraph is the runtime underneath this. It handles graph execution, state management, and checkpointing. This pattern adds nothing to that layer — it sits above it.
>
> CrewAI and AutoGen are also not competitors here. They use a different mental model — agents as autonomous actors with roles and goals. That's a valid approach for some problems. This pattern is aimed at a different problem: pipelines where you want explicit, versioned, diffable workflow logic and human review before any change lands.
>
> The clearest way I can put it: this pattern is for you if you've ever needed to answer the question 'what should I do to improve or cut cost on my last Tuesday's run' — and found that you couldn't. It is for you if you want your workflow to iterately improve themselves continuously.
>
> And critically: nothing about this pattern is tied to this specific codebase. The YAML-as-source-of-truth idea, the propose-eval-apply loop, the rollback snapshot — all of that can be transplanted into any stack that has a runtime you can compile a config into."

---

### Context behind this slide

This slide is placed third for a specific reason: the audience needs to hear "this is not a replacement for LangGraph" before the technical content begins, not after. If this positioning comes too late — say, at slide 10 or 11 — the audience has spent the first fifteen minutes mentally building a case for why they don't need it, because they've assumed it's competing with tools they're already invested in.

The table format is also intentional. Showing LangGraph, CrewAI/AutoGen, and this pattern as three rows in the same table signals that they belong to different categories of the same taxonomy, rather than being competing solutions. It respects the audience's existing choices.

The "Best for / Not for" boxes are a deliberate act of scope-limiting honesty. A common failure mode in technical presentations is claiming the solution is universally applicable. By explicitly saying "not for fully autonomous agents," the presenter gains credibility with the portion of the audience whose use case genuinely doesn't fit — and they become less likely to raise disruptive objections during Q&A.

The closing quote — "This is not a new framework. It is a pattern that can be transplanted into any stack." — should be said out loud even though it appears on the slide. It's the most important positioning statement in the entire presentation and should be heard, not just read.

---
---

## SLIDE 5 — THE PATTERN

### Slide text

**The Pattern**

*Make the YAML file the shared contract: human reads it, LLM reads it, runtime compiles it.*

**[Diagram: vertical 4-step flow]**

① YAML spec
↓
② LLM proposes diff
↓
③ Human reviews graph + eval evidence
↓
④ Apply (write) or Discard

*(side note: rollback snapshot kept)*

---

### What to say

> "So here's the pattern in its simplest form. Four steps.
>
> Step one: everything starts from a YAML file. It describes the full workflow — which nodes exist, what kind each one is, how state flows between them, what the budget constraints are, what the edges are. It is the only source of truth. There is no separate runtime config, no GUI state, no Python class that holds the 'real' definition.
>
> Step two: when you want to change something, you tell the system what you want in plain English — 'make the reranker less expensive without hurting quality' — and the LLM is given the full YAML and asked to produce a revised version. It returns a complete updated spec.
>
> Step three: you don't just take the LLM's word for it. The proposed spec is shown to you as a diff — exactly which lines changed and how. At the same time, the platform runs your existing eval fixtures against the proposed spec in memory. You see the pass rate, cost delta, and latency delta before you decide.
>
> Step four: you click Apply, or you discard it. If you apply it, the YAML file is overwritten, and a rollback snapshot is saved automatically. That's the only moment disk is touched.
>
> The whole thing is a loop, not a one-shot operation. You can propose, evaluate, discard, try again with a different goal. Every apply creates a recoverable audit record."

---

### Context behind this slide

This is the central concept slide — the one the entire presentation is scaffolding towards. The four-step flow is deliberately abstract here because the next three slides will make each step concrete (the YAML itself on slide 6, the propose-validate half on slide 7, the eval-apply-recover half on slide 8).

The side note "rollback snapshot kept" is placed outside the main flow intentionally. It's not a step — it happens automatically as a consequence of Apply. Showing it as a side annotation signals that auditability is built into the loop, not bolted on as an afterthought.

The key design principle to convey is the asymmetry of the loop: the LLM can read and propose, but it can never write. The human is the only agent in this system with write authority. This is not a limitation — it is a deliberate architectural guarantee that makes the system trustworthy for production use.

The subtitle — "Make the YAML file the shared contract" — is the conceptual anchor for the whole presentation. Return to this phrasing whenever the audience looks confused: the answer to most "why did you build it this way" questions is some variant of "because the YAML is the shared contract."

---
---

## SLIDE 6 — `linear_rag.yaml`

### Slide text

**`linear_rag.yaml`**

*The only authoring surface: validated by Pydantic, compiled to a LangGraph StateGraph at runtime*

```yaml
schema_version: workflow.graph/v1
name:   linear_rag
entry:  query_analyser
budget:
  cost_usd:    0.50
  latency_ms:  240000
nodes:
  - id:     query_analyser
    kind:   llm
    provider: openai
    model:  gpt-4o-mini
    system_prompt: Rewrite questions
      into retrieval queries.
    output_state_key: query_analysis
  - id:   query_embedding
    kind: embedding
    provider: openrouter
    model: google/gemini-embedding-2-preview
    output_state_key: query_embedding
  - id:   vector_retriever
    kind: vector_retriever
    index_path: evals/linear_rag/vector_index.sqlite
    output_state_key: retrieved_context
  - id:   reranker
    kind: llm
    provider: openai
    model: gpt-4o-mini
    output_state_key: reranked_context
  - id:   synthesiser
    kind: llm
    provider: openai
    model: gpt-4o-mini
    output_state_key: final_answer
edges:
  - from: query_analyser
    to:   query_embedding
  - from: query_embedding
    to:   vector_retriever
  - from: vector_retriever
    to:   reranker
  - from: reranker
    to:   synthesiser
  - from: synthesiser
    to:   __end__
```

**Annotations:**

| Key | Note |
|-----|------|
| `schema_version` | Versioned: diffable between runs |
| `budget` | Cost and latency enforcement per run |
| `kind` | Node type: 10 node kinds supported |
| `output_state_key` | How state flows between nodes |
| `edges` | Explicit topology: no magic routing |

---

### What to say

> "Let's make this concrete. This is a real file from the repo. It defines a five-node RAG pipeline: a query analyser, an embedding node, a vector retriever, a reranker, and a synthesiser.
>
> Read it top to bottom and notice what it tells you without any external documentation. `schema_version` — this spec is versioned, which means two versions of this file are diffable in a way that two Python files are not, because the schema is stable. `budget` — there is a cost ceiling and a latency ceiling baked into the spec itself, not hardcoded in a separate config file. `kind` — every node declares what type it is; there are ten supported kinds in this system including LLMs, embeddings, retrievers, gates, approval nodes, subgraphs, and local Python tools. `output_state_key` — this is how state flows between nodes; the output of `query_analyser` lands in `query_analysis`, which the embedding node can read. `edges` — the graph topology is fully explicit. There is no magic routing, no implicit ordering.
>
> Now the critical question: can a large language model read this file and understand the pipeline it describes? Yes. Can it propose a concrete diff — change `gpt-4o-mini` to something cheaper on the reranker, shorten the system prompt, adjust the budget — in a way that produces a valid spec? Yes. That's the capability slide 3 was pointing to. This file is what makes it possible."

---

### Context behind this slide

This slide does the most important job in the presentation: it proves the concept is real by showing the actual artefact. Up to this point, the pattern has been described abstractly. Showing a real, readable, parseable YAML file makes the claim tangible.

The annotations on the right side are chosen to walk the audience through the five structural decisions embedded in the schema:

**`schema_version`:** Semantic versioning of the schema enables tooling — migration scripts, compatibility checks, and diff rendering — in a way that arbitrary Python dicts do not. The LLM also benefits from a stable schema: it can be given the schema definition and the current file, and reliably produce a valid revision.

**`budget`:** Cost and latency constraints as first-class spec fields, not runtime exceptions, change how you think about pipeline iteration. If a proposed change from the LLM would push the pipeline over budget, the eval step will catch it before Apply. Budget is policy, not monitoring.

**`kind: llm`:** The ten node kinds supported (llm, tester, retriever, vector_retriever, embedding, gate, router, approval, subgraph, python_tool) cover the majority of production agent pipeline patterns. Each kind has a well-defined interface that Pydantic validates. This is what makes LLM-proposed specs safe to accept — the schema constrains what the LLM can produce.

**`output_state_key`:** State management in LangGraph is handled via a shared state object. This field tells the system where to write each node's output in that state, and lets the next node read it by name. The YAML makes this explicit and traceable — you can follow data provenance through the file without running the pipeline.

**`edges`:** Explicit edge declarations over implicit ordering mean the topology is always readable and always diffable. If an LLM proposes adding an edge, you see the exact addition in the diff. There is no ambient routing logic hidden in Python that the diff doesn't capture.

---
---

## SLIDE 7 — THE LOOP: PROPOSE AND VALIDATE

### Slide text

**The Loop: Propose and Validate**

*Steps 1 to 4 of 7: everything here is read-only, nothing writes to disk yet*

**[Diagram: 4-box horizontal flow]**

Input: plain-text mutation goal

① **Write Goal** → ② **LLM Proposes** → ③ **Validate** → ④ **Diff Shown**

Output: validated YAML diff

| Step | Description |
|------|-------------|
| ① Write Goal | Developer states what needs to change in natural language |
| ② LLM Proposes | Full revised YAML returned: the LLM rewrites the spec |
| ③ Validate | Pydantic GraphSpec rejects invalid proposals immediately |
| ④ Diff Shown | Exact line-by-line changes visible before any decision |

*The developer writes the goal in plain text. The LLM returns a fully revised YAML. Pydantic either accepts or rejects it before any run begins.*

---

### What to say

> "This is the first half of the propose-eval-apply loop. Four steps, all read-only — nothing changes on disk during any of these.
>
> Step one: you write a goal in plain English. Something like 'reduce cost on the reranker without hurting retrieval quality.' You don't write code. You don't edit YAML by hand. You state an intent.
>
> Step two: the backend takes your goal and the full current YAML, sends both to the LLM, and asks it to return a complete revised spec. Not a patch, not a diff instruction — a full revised YAML. The LLM is doing the edit; you're reviewing it.
>
> Step three: before that revised spec is shown to you, it passes through Pydantic GraphSpec validation. If the LLM hallucinated a node kind that doesn't exist, or broke an edge reference, or set a budget field to a string instead of a float — the proposal is rejected immediately. You see an error, not a broken pipeline. This is important: the schema is the safety net, not you.
>
> Step four: if the proposal is valid, you see a unified diff — exactly which lines changed, which were added, which were removed. At this point you've spent maybe ten seconds and you have a concrete, schema-valid proposal in front of you.
>
> Everything so far is read-only. Nothing has been touched."

---

### Context behind this slide

This slide and slide 8 together explain the most complex interaction pattern in the system. Splitting them into "propose and validate" versus "eval, apply, recover" is a deliberate pedagogical choice — the loop has a natural midpoint at the diff view, where the human first becomes involved.

**Why a full YAML rewrite rather than a targeted patch?**
This is the question most technically sophisticated audience members will ask. The honest answer is that it's the current implementation, and it has a known limitation (see slide 11: proposal quality degrades on large specs). The reason the full rewrite approach was chosen for the initial implementation is that it gives the LLM full context — it can see the entire pipeline before proposing any change, which reduces the chance of a local edit breaking a non-obvious dependency. A YAML patch format would be more precise but requires the LLM to reason about what it's not changing, which is harder.

**Why Pydantic for validation?**
Pydantic GraphSpec is the contractual boundary between "what the LLM suggested" and "what the system will accept." It validates structure, types, budget field constraints, edge validity (all `from` and `to` references must be real node IDs), and node kind legality. The validation step is the reason the propose step is safe to run without human involvement — invalid proposals are caught programmatically before they reach the human reviewer.

**Why show a diff rather than just the proposed spec?**
Because the human is reviewing a change, not a document. A 50-line YAML file is not easy to review as a full replacement. A unified diff showing four changed lines is trivially reviewable in seconds. The diff format is also the representation that maps most naturally to Git workflows the audience already uses.

---
---

## SLIDE 8 — THE LOOP: EVAL, APPLY, RECOVER

### Slide text

**The Loop: Eval, Apply, Recover**

*Steps 5 to 7 of 7: only one step writes to disk, and only after explicit confirmation*

**[Diagram: 3-box horizontal flow, Apply box highlighted in pink]**

⑥ **Eval in Memory** → ⑦ **Apply or Discard** → ⑧ **Recover**

| Step | Description |
|------|-------------|
| ⑥ Eval in Memory | Proposed spec runs against fixture inputs without touching `workflows/` |
| ⑦ Apply or Discard | Apply overwrites `workflows/*.yaml` and saves a rollback snapshot |
| ⑧ Recover | Human-confirmed restore from any snapshot: each restore creates an audit entry |

**Only this step writes to disk** *(callout under Apply)*

*The LLM never touches disk. Eval is in-memory only. Apply is the single proposal gate. Recover writes only after human confirmation and records the restore as a new audit entry.*

---

### What to say

> "Second half of the loop. Three steps — and there's only one that writes anything to disk.
>
> Step five — eval in memory: the proposed spec is compiled into a LangGraph graph in memory and run against a set of fixture inputs. The fixtures are pre-recorded input-output pairs that represent the expected behaviour of the pipeline. The system returns a pass rate, a cost estimate, and a latency estimate — all for the proposed change, compared against the baseline. You see the numbers before you decide anything.
>
> Step six — apply or discard: this is the only moment in the entire loop that touches disk. You click Apply, you get a confirmation dialog, you confirm — and the YAML file is overwritten with the proposed version. A rollback snapshot of the previous version is saved automatically at the same time. If you click Discard instead, nothing changes anywhere.
>
> Step seven — recover: the Recover tab lists every snapshot with its diff relative to the current spec. Restoring a previous version requires human confirmation, then the restore itself is recorded as a new audit entry. Nothing is silently deleted. The history is append-only.
>
> The summary at the bottom is worth reading out loud: the LLM never touches disk. Eval is in-memory only. Apply is the single gate. That's the safety contract of the whole system."

---

### Context behind this slide

The Apply box is deliberately coloured pink (the primary accent) to visually signal that it is structurally different from the other steps. Every other box in slides 7 and 8 uses the dark purple BOX colour — Apply is the only one that stands out. This is a design choice: the human should feel the weight of that step when they look at the slide.

**Why eval against fixtures rather than real data?**
The fixture-based eval harness is a deliberate scoping decision. Fixture tests are fast (milliseconds per input), deterministic (same input always produces the same eval run), and safe (no production data required). They are not comprehensive — the slide 11 limitation explicitly calls this out. The intent is to give the human reviewer enough signal to make an informed Apply/Discard decision in under thirty seconds, not to replace a full regression suite.

**Why save a rollback snapshot on every Apply?**
Because Apply is irreversible in the immediate term — the previous YAML is overwritten. The rollback mechanism is the safety net that makes Apply feel safe. Without it, every Apply would feel like a high-stakes operation. With it, Apply is a low-stakes, reversible action. This changes the psychology of iteration: developers are more willing to try proposed changes when they know recovery is one click.

**Why is the recover audit entry important?**
Because an audit trail that can be silently modified is not an audit trail. If restoring a snapshot simply overwrote the current file with no record, a series of propose/apply/restore cycles would produce a history with gaps. The restore flow intentionally writes an audit entry, so the history is complete even though the current YAML is replaced by the selected snapshot.

---
---

## SLIDE 9 — UI WALKTHROUGH

### Slide text

**UI Walkthrough**

*Replace this placeholder with a localhost screenshot before presenting*

> ⚠ SCREENSHOT BLOCKER
> http://127.0.0.1:5173  |  linear\_rag selected · reranker node clicked
> Annotate three zones: selector / graph canvas / inspect panel

**Zone labels:**

| Zone | Label |
|------|-------|
| ① | Workflow selector |
| ② | Graph canvas |
| ③ | Inspect panel |

---

### What to say

> "This is what you actually open when you run the app locally. Three zones.
>
> On the left: the workflow selector. All your YAML workflows are listed here, grouped by category and searchable. Selecting one compiles the YAML in memory and renders the graph in the centre panel.
>
> In the centre: the graph canvas. Every node in the YAML becomes a box. Every edge becomes an arrow. The graph is a visual rendering of the YAML — nothing more. If you edit the YAML directly and refresh, the graph updates. They are always in sync because they're the same data.
>
> On the right: the inspect panel. Click any node and you see its full spec: model, system prompt, output state key, kind, budget contribution. Everything you'd want to audit about a specific node is one click away. This is also where you'd confirm that a proposed diff actually changed what you expected it to change.
>
> In the workshop you'll be in this UI in about ten minutes. Take thirty seconds now to absorb the layout — it'll make the exercises faster."

*(If running the live demo: navigate to the UI and walk through the three zones in real time instead of reading from the slide.)*

---

### Context behind this slide

This slide is structurally a transition from concept to practice. The previous slides built the mental model; this slide and slide 10 show the physical interface the audience will use in the workshop.

The three-zone layout (selector / graph / inspect) mirrors a standard developer tool pattern: tree on the left, canvas in the centre, property panel on the right. This is intentional — it reduces cognitive load for developers who have used tools like VS Code, Figma, or any graph-based IDE. The layout is familiar; only the content is new.

The key point to hammer home is that the graph canvas and the YAML file are the same data. There is no separate "visual representation layer" that could get out of sync with the underlying spec. The graph is compiled from the YAML on every render. This makes the UI a viewer, not an editor — the YAML is still the only authoring surface.

The screenshot placeholder (slides 9 and 10) must be replaced before presenting. The screenshots need to be taken with the app running locally, with `linear_rag` selected and the `reranker` node clicked in the inspect panel. Taking the screenshot at least one day before the session ensures that any UI rendering issues can be caught and fixed without pressure.

---
---

## SLIDE 10 — THE IMPROVE TAB

### Slide text

**The Improve Tab**

*Replace this placeholder with a screenshot of the Improve panel before presenting*

> ⚠ SCREENSHOT BLOCKER
> Improve panel  |  diff visible · eval results populated · Apply and Discard buttons shown

*"The LLM wrote this diff. The human decides whether to land it."*

---

### What to say

> "And this is the Improve tab — the most important screen in the whole application.
>
> What you're looking at is the output of the propose-and-validate loop we just walked through on slides 7 and 8. You typed a goal in the text field at the top. The system sent your goal and the full YAML to the LLM. The LLM returned a revised spec. Pydantic validated it. And what's displayed here is the unified diff — the exact lines that changed — alongside the eval results: pass rate, cost delta, latency delta compared to the current spec.
>
> The caption at the bottom of this slide is the sentence I want you to remember: the LLM wrote this diff. The human decides whether to land it.
>
> The Apply button is right there. The Discard button is right next to it. You are in control. The LLM did the edit work. You are doing the review work. That's the division of labour the pattern is designed to produce.
>
> You'll use exactly this screen in Exercise 3 in about fifteen minutes."

*(If running the live demo: show the Improve tab live and walk through a real propose-and-eval cycle. Use the goal text from the workshop handout so the audience can follow along.)*

---

### Context behind this slide

This slide is the payoff moment for the conceptual content of slides 2 through 8. Everything before it was explaining why and how; this slide shows what the experience actually feels like.

The diff view is the most important element of the UI to communicate clearly. A unified diff is a format developers know from Git — green lines for additions, red lines for removals. Seeing LLM-generated changes in this familiar format does two things: it makes the changes easy to review (you're doing code review, a task you've done hundreds of times), and it makes the LLM feel like a collaborator rather than a black box (the diff is transparent in a way that "I changed some things, want to see the new file?" is not).

The eval results row (pass rate / cost / latency) is the second critical element. Without eval results, Apply is a leap of faith. With eval results — even fixture-based, limited ones — Apply is an informed decision. The numbers don't need to be perfect; they need to be directionally useful. "Pass rate 94%, cost down 18%, latency unchanged" is enough to click Apply with reasonable confidence.

The caption — "The LLM wrote this diff. The human decides whether to land it." — is the single sentence that best summarises the human-AI collaboration model of the whole pattern. It should be delivered as a close to the slide, not an afterthought.

---
---

## SLIDE 11 — HONEST LIMITATIONS

### Slide text

**Honest Limitations**

*Being direct here builds credibility: these are exactly what the audience will ask about*

**Proposal quality degrades on large specs**
Full YAML rewrite: the LLM holds the entire spec and must reproduce it without drift. A YAML patch format would help.

**Eval coverage is still only as good as your local datasets**
The repo now supports fixture evals plus generalized CSV, JSONL, and YAML dataset eval adapters, but it does not solve subjective scoring or production distribution coverage for you.

**Budget enforcement is post-node, not mid-stream**
A node over budget is rejected after completion, not cancelled mid-generation. Streaming cancellation is deferred.

**Only two providers: OpenAI and OpenRouter**
Anthropic, Gemini, and Ollama each need a new adapter. The pattern is clean but provider coverage is thin.

**Next milestone is domain-model depth, not more eval plumbing**
The current planned milestone is replacing the placeholder person reID specialists with real model wrappers for visual, text, and body-shape signals.

---

### What to say

> "I want to be direct about where this breaks down, because if I don't tell you, someone in the Q&A will, and it's better coming from me.
>
> First: proposal quality degrades on large specs. The current implementation asks the LLM to rewrite the entire YAML. For a ten-node pipeline, that works well. For a fifty-node pipeline with complex loops, the LLM starts making small errors — repeating a node ID, dropping an edge, subtly changing a field it wasn't asked to change. The schema validation catches the obviously broken ones, but semantically valid but logically wrong changes are harder. A YAML patch format would reduce this risk significantly — it's the planned next step.
>
> Second: eval quality depends on the datasets and scorers you bring. The repo now has fixture evals and generalized dataset eval adapters for CSV, JSONL, and YAML, but that doesn't magically solve subjective scoring or guarantee your eval set matches production traffic.
>
> Third: budget enforcement is post-node. If a node runs over budget, it finishes and then gets flagged. Mid-generation cancellation — stopping a streaming response the moment it exceeds the token budget — is a harder engineering problem and it's deferred.
>
> Fourth: two providers. OpenAI and OpenRouter. If your team is on Anthropic or Gemini or running local Ollama models, you will need to write an adapter. The adapter interface is clean — it's not a large task — but it is a task.
>
> The current next milestone is not more eval plumbing. It is deeper domain capability: replacing the placeholder person reID specialists with real model wrappers for visual, text, and body-shape signals.
>
> These are real limitations. I'm telling you now so they don't come as surprises when you try to apply this to your own system."

---

### Context behind this slide

This slide is structurally one of the most important in the deck, and it is frequently the one presenters want to rush or soften. Do not rush it. Do not soften it.

Developer audiences at technical workshops evaluate presenters as much as they evaluate the content. A presenter who acknowledges limitations clearly and without defensiveness signals that they understand the system deeply — that the limits are known design decisions, not oversights. A presenter who glosses over limitations or deflects questions about them loses credibility precisely when they could be building it.

The limitation and roadmap points chosen here are the ones that technically sophisticated audience members are most likely to probe during Q&A:

**Full rewrite vs patch:** This is a classic LLM reliability tradeoff. Full context enables better holistic proposals; smaller diffs enable more reliable targeted edits. The current choice (full rewrite) optimises for proposal quality on simple pipelines at the cost of reliability on complex ones. Stating this directly pre-empts the "but what about large pipelines" question.

**Dataset-dependent eval:** This is the limitation that most directly constrains production applicability. The repo has fixture evals and generalized CSV/JSONL/YAML dataset adapters, but most real pipelines still have distributions of inputs and probabilistic outputs. The honest answer is that local eval infrastructure is a starting point, not a complete evaluation strategy. Acknowledging this positions the presenter as someone who has thought about production use, not just demos.

**Post-node budget enforcement:** This is a UX limitation more than a correctness one. The pipeline still runs within budget on average — a single node running 10% over budget does not break the system. But it reduces the precision of cost control, which matters for production deployments where cost predictability is required. Streaming budget cancellation is a legitimate engineering feature that is genuinely deferred, not forgotten.

**Two providers:** This is the most immediately practical limitation for most audience members. The adapter interface is designed to be simple — a class with a few methods — but it requires knowing the provider's API. For Anthropic (Claude), Gemini, and Ollama, the adapters are the next obvious contribution to the repo.

---
---

## SLIDE 12 — TAKE BACK THREE THINGS

### Slide text

**Take Back Three Things**

**1  Put the spec in one file both you and the LLM can read**
A shared, human-readable YAML is the foundation of every other property

**2  Keep mutation human-reviewed**
Propose, Eval, explicit Apply. Nothing lands until a human says so.

**3  Treat run artifacts as immutable audit records**
Fork continuations, don't mutate source runs. Rollback restores overwrite the current YAML only after confirmation and record an append-only audit entry.

---

*Key question: where in your pipeline is the spec? Can the LLM read it? Can you diff two versions?*

`[ repo link ]`

---

### What to say

> "Three things. If you remember nothing else from today, remember these.
>
> One: put the spec in one file both you and the LLM can read. Not in Python decorators. Not in a hosted dashboard. Not in a JSON export you've never opened. A file that both humans and language models can parse, reason about, and propose changes to. That property is the foundation of every other property this pattern has.
>
> Two: keep mutation human-reviewed. The LLM is an excellent editor. It is not a safe autonomous committer. Propose, eval, explicit apply — that sequence is the difference between an agent that collaborates with you and one that drifts without your knowledge.
>
> Three: treat run artifacts as immutable audit records. Do not mutate runs. Fork continuations from checkpoints. When restoring a rollback snapshot, require confirmation and write an audit entry. When something breaks in production at two in the morning, the question 'what exactly changed and when' should have a precise, trustworthy answer.
>
> The key question I'll leave you with — and I want you to go home and actually answer it for your current project: where is the spec? Can the LLM read it? Can you diff two versions of it?
>
> If the answer to any of those is no, you now know what to change. The repo link is on the screen. The README has everything you need to start. And in ten minutes you'll have built the loop yourself. Let's open laptops."

---

### Context behind this slide

The closing slide is designed to leave the audience with portable, framework-independent takeaways rather than feature descriptions. The three points are ordered by increasing abstraction:

**Point 1 (single file):** This is the concrete, actionable change. Move your workflow spec into a YAML file. This is something a developer can do next Monday in their existing project, regardless of whether they use this specific codebase.

**Point 2 (human-reviewed mutation):** This is the behavioural principle. It applies whether you're using this system, a hand-rolled CI pipeline, or a commercial agent platform. The instinct to add a human review step before any automated change lands in production is universally applicable to agentic systems.

**Point 3 (immutable audit records):** This is the systems design principle. Fork-on-continuation rather than mutate-in-place is a pattern from functional programming and event sourcing that most developers have encountered in databases or distributed systems, but often don't apply to agent run history. Rollback restore can replace the current YAML, but the restore event itself must be recorded. Naming that explicitly gives the audience a vocabulary they can take into design discussions.

The closing question — "where is the spec? Can the LLM read it? Can you diff two versions?" — is the diagnostic the presenter wants developers to apply to their own systems. It is phrased as a question rather than a recommendation because a question is harder to dismiss. You can dismiss a recommendation by saying "that doesn't apply to my case." You cannot easily dismiss a question — you have to answer it.

The transition to "let's open laptops" is intentional: end on a call to action, not a summary. The energy should move forward into the workshop, not settle into reflection.
