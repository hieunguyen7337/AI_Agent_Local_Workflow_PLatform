"""FastAPI route tests for graph telemetry overlays."""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import backend.runtime.artifacts as artifacts
from backend.server import routes
from backend.server.app import app
from backend.runtime.artifacts import resolve_run_dir, run_dir_for_id
from backend.telemetry.exporter import ensure_schema
from backend.providers.base import LLMResponse, Usage
from backend.graphspec import graph_spec_to_metadata, load_graph_spec
from backend.runtime.executor import run_graph


def _write_single_run(runs_root: Path) -> None:
    run_dir = run_dir_for_id(runs_root, "coder_tester", "run_api")
    run_dir.mkdir(parents=True, exist_ok=True)
    db_path = run_dir / "telemetry.db"
    ensure_schema(db_path)
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT OR REPLACE INTO runs (run_id, graph_name, started_ns, status, cost_usd, latency_ms, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("run_api", "coder_tester", 123, "ok", 0.01, 10.0, None),
        )
        con.execute(
            "INSERT OR REPLACE INTO spans "
            "(span_id, trace_id, parent_span_id, name, start_ns, end_ns, duration_ms, status, "
            "run_id, graph_name, node_id, node_kind, iteration, model, input_tokens, output_tokens, cost_usd, attributes_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "api_span_1",
                "1" * 32,
                None,
                "node.planner",
                100,
                200,
                100.0,
                "OK",
                "run_api",
                "coder_tester",
                "planner",
                "llm",
                0,
                "m",
                1,
                1,
                0.01,
                json.dumps({}),
            ),
        )


def _copy_workflow_specs(tmp_path: Path) -> Path:
    specs_root = tmp_path / "workflows"
    specs_root.mkdir()
    for path in Path("workflows").glob("*.yaml"):
        shutil.copy(path, specs_root / path.name)
    return specs_root


def test_graph_node_metrics_endpoint(tmp_path: Path, monkeypatch):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    _write_single_run(runs_root)
    monkeypatch.setattr(artifacts, "RUNS_ROOT", runs_root)

    client = TestClient(app)
    resp = client.get("/api/graph/coder_tester/node-metrics?limit=50")
    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow"] == "coder_tester"
    assert body["limit"] == 50
    assert "metrics" in body
    assert "planner" in body["metrics"]
    assert body["metrics"]["planner"]["invocations"] >= 1


def test_graph_node_metrics_rejects_bad_limit():
    client = TestClient(app)
    resp = client.get("/api/graph/coder_tester/node-metrics?limit=0")
    assert resp.status_code == 400


def test_start_run_endpoint_runs_selected_workflow(tmp_path: Path, monkeypatch):
    runs_root = tmp_path / "runs"
    monkeypatch.setattr(artifacts, "RUNS_ROOT", runs_root)
    captured = {}

    def _fake_run_workflow_function(workflow, input_state, **kwargs):
        captured["workflow"] = workflow
        captured["input_state"] = input_state
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            run_id="run_api_start",
            workflow=workflow,
            status="ok",
            error=None,
            cost_usd=0.01,
            latency_ms=12.0,
            run_dir=run_dir_for_id(runs_root, workflow, "run_api_start"),
            final_state={"final_answer": "done"},
        )

    monkeypatch.setattr(routes, "run_workflow_function", _fake_run_workflow_function)

    client = TestClient(app)
    resp = client.post(
        "/api/runs?workflow=linear_rag",
        json={"input": "What is the refund window?", "expected": "30 days"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "run_api_start"
    assert body["graph_name"] == "linear_rag"
    assert body["final_state"]["final_answer"] == "done"
    assert captured["workflow"] == "linear_rag"
    assert captured["input_state"] == "What is the refund window?"
    assert captured["kwargs"]["expected"] == "30 days"
    assert captured["kwargs"]["runs_root"] == runs_root


def test_batch_run_endpoint_accepts_multiple_inputs(monkeypatch, tmp_path: Path):
    runs_root = tmp_path / "runs"
    monkeypatch.setattr(artifacts, "RUNS_ROOT", runs_root)
    captured = {}

    def _fake_run_workflow_batch(workflow, items, **kwargs):
        captured["workflow"] = workflow
        captured["items"] = items
        captured["kwargs"] = kwargs
        return [
            SimpleNamespace(
                id=item.id,
                status="ok",
                final_state={"answer": item.input_state["user_input"]},
                run_id=f"run_{item.id}",
                run_dir=run_dir_for_id(runs_root, workflow, f"run_{item.id}"),
                error=None,
                cost_usd=0.01,
                latency_ms=10.0,
            )
            for item in items
        ]

    monkeypatch.setattr(routes, "run_workflow_batch", _fake_run_workflow_batch)

    client = TestClient(app)
    resp = client.post(
        "/api/workflows/linear_rag/batch-run",
        json={
            "max_concurrency": 2,
            "items": [
                {"id": "a", "input_state": {"user_input": "first"}},
                {"id": "b", "input_state": {"user_input": "second"}},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow"] == "linear_rag"
    assert [item["id"] for item in body["results"]] == ["a", "b"]
    assert body["results"][0]["final_state"]["answer"] == "first"
    assert captured["kwargs"]["max_concurrency"] == 2


def test_batch_run_request_default_max_concurrency_is_50():
    request = routes.BatchRunRequest(items=[routes.BatchRunItem(input_state={"user_input": "x"})])
    assert request.max_concurrency == 50


def test_start_run_missing_workflow_returns_404():
    client = TestClient(app)
    resp = client.post("/api/runs?workflow=not_a_workflow", json={"input": "hello"})
    assert resp.status_code == 404


def test_get_graph_linear_rag_shape():
    client = TestClient(app)
    resp = client.get("/api/graph/linear_rag")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "linear_rag"
    node_ids = {n["id"] for n in body["nodes"]}
    assert {"query_analyser", "query_embedding", "vector_retriever", "reranker", "synthesiser"} <= node_ids


def test_get_graph_supervisor_loop_shape():
    client = TestClient(app)
    resp = client.get("/api/graph/supervisor_loop")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "supervisor_loop"
    node_ids = {n["id"] for n in body["nodes"]}
    assert {"supervisor", "dispatch", "researcher", "writer"} <= node_ids
    labels = {(edge["source"], edge["label"], edge["target"]) for edge in body["edges"] if edge["kind"] == "conditional"}
    assert ("dispatch", "RESEARCHER", "researcher") in labels
    assert ("dispatch", "WRITER", "writer") in labels
    dispatch = next(node for node in body["nodes"] if node["id"] == "dispatch")
    assert dispatch["kind"] == "router"
    assert dispatch["metadata"]["routes"]["RESEARCHER"] == "researcher"


def test_get_graph_dispatch_aggregate_shape():
    client = TestClient(app)
    resp = client.get("/api/graph/dispatch_aggregate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "dispatch_aggregate"
    node_ids = {n["id"] for n in body["nodes"]}
    assert {"dispatcher", "specialist_a", "specialist_b", "aggregator"} <= node_ids
    edges = {(edge["source"], edge["target"], edge["kind"]) for edge in body["edges"]}
    assert ("dispatcher", "specialist_a", "normal") in edges
    assert ("dispatcher", "specialist_b", "normal") in edges
    assert ("specialist_a", "aggregator", "normal") in edges
    assert ("specialist_b", "aggregator", "normal") in edges


def test_get_graph_approval_review_shape():
    client = TestClient(app)
    resp = client.get("/api/graph/approval_review")
    assert resp.status_code == 200
    body = resp.json()
    node_ids = {n["id"] for n in body["nodes"]}
    assert {"draft", "human_review", "finalizer"} <= node_ids
    approval = next(node for node in body["nodes"] if node["id"] == "human_review")
    assert approval["kind"] == "approval"
    labels = {
        (edge["source"], edge["label"], edge["target"])
        for edge in body["edges"]
        if edge["kind"] == "conditional"
    }
    assert ("human_review", "approved", "finalizer") in labels
    assert ("human_review", "rejected", "__end__") in labels


def test_get_spec_returns_yaml_and_validated_spec():
    client = TestClient(app)
    resp = client.get("/api/spec/coder_tester")
    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow"] == "coder_tester"
    assert body["source_path"] == "workflows/coder_tester.yaml"
    assert "schema_version: workflow.graph/v1" in body["yaml"]
    assert body["spec"]["name"] == "coder_tester"
    assert body["spec"]["entry"] == "planner"


def test_get_spec_supervisor_loop_includes_router_routes():
    client = TestClient(app)
    resp = client.get("/api/spec/supervisor_loop")
    assert resp.status_code == 200
    body = resp.json()
    dispatch = next(node for node in body["spec"]["nodes"] if node["id"] == "dispatch")
    assert dispatch["kind"] == "router"
    assert dispatch["routes"]["RESEARCHER"] == "researcher"
    assert dispatch["routes"]["FINISH"] == "__end__"


def test_get_spec_template_includes_template_parameters():
    client = TestClient(app)
    resp = client.get("/api/spec/simple_llm_template")
    assert resp.status_code == 200
    body = resp.json()
    assert body["spec"]["template"] is True
    assert body["spec"]["template_parameters"] == [
        {
            "key": "user_input",
            "description": "Primary task text supplied when the copied workflow runs.",
            "state_key": "user_input",
            "example": "Summarize this note in three bullets.",
        }
    ]


def test_get_spec_missing_workflow_returns_404():
    client = TestClient(app)
    resp = client.get("/api/spec/not_a_workflow")
    assert resp.status_code == 404


def test_propose_mutation_returns_valid_diff(monkeypatch):
    current_yaml = Path("workflows/coder_tester.yaml").read_text(encoding="utf-8")
    proposed_yaml = current_yaml.replace("temperature: 0.2", "temperature: 0.1", 1)

    def _fake_call_provider(**kwargs):
        return LLMResponse(
            text=f"Summary: Lowered planner temperature.\n{proposed_yaml}",
            usage=Usage(1, 1),
            model="gpt-4o-mini",
        )

    monkeypatch.setattr("backend.graphspec.mutation.call_provider", _fake_call_provider)
    before = Path("workflows/coder_tester.yaml").read_text(encoding="utf-8")

    client = TestClient(app)
    resp = client.post(
        "/api/spec/coder_tester/propose-mutation",
        json={"goal": "Make planner output more deterministic."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "valid"
    assert body["summary"] == "Lowered planner temperature."
    assert "temperature: 0.1" in body["proposed_yaml"]
    assert "-    temperature: 0.2" in body["diff"]
    assert "+    temperature: 0.1" in body["diff"]
    assert body["validation_errors"] == []
    assert Path("workflows/coder_tester.yaml").read_text(encoding="utf-8") == before


def test_propose_mutation_returns_invalid_validation_errors(monkeypatch):
    def _fake_call_provider(**kwargs):
        return LLMResponse(
            text="schema_version: workflow.graph/v1\nname: broken\nentry: missing\nnodes: []\nbudget:\n  cost_usd: 0.1\n  latency_ms: 1000\n",
            usage=Usage(1, 1),
            model="gpt-4o-mini",
        )

    monkeypatch.setattr("backend.graphspec.mutation.call_provider", _fake_call_provider)

    client = TestClient(app)
    resp = client.post(
        "/api/spec/coder_tester/propose-mutation",
        json={"goal": "Break it for test coverage."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "invalid"
    assert body["proposed_yaml"].startswith("schema_version")
    assert body["validation_errors"]


def test_propose_mutation_requires_goal():
    client = TestClient(app)
    resp = client.post("/api/spec/coder_tester/propose-mutation", json={"goal": ""})
    assert resp.status_code == 422


def test_propose_mutation_missing_workflow_returns_404():
    client = TestClient(app)
    resp = client.post(
        "/api/spec/not_a_workflow/propose-mutation",
        json={"goal": "Reduce cost."},
    )
    assert resp.status_code == 404


def test_evaluate_proposal_runs_valid_yaml_with_mocked_provider(monkeypatch, tmp_path: Path):
    def _fake_stream_openrouter(**kwargs):
        system = kwargs["messages"][0]["content"]
        user = kwargs["messages"][1]["content"]
        if "Break the user's task" in system:
            return LLMResponse(text="plan", usage=Usage(1, 1), model="m")
        if "careful coder" in system and "fizzbuzz" in user:
            return LLMResponse(
                text=(
                    "def fizzbuzz(n):\n"
                    "    return ['FizzBuzz' if i % 15 == 0 else 'Fizz' if i % 3 == 0 else 'Buzz' if i % 5 == 0 else str(i) for i in range(1, n + 1)]\n"
                ),
                usage=Usage(1, 1),
                model="m",
            )
        if "careful coder" in system and "reverse_words" in user:
            return LLMResponse(
                text="def reverse_words(s):\n    return ' '.join(reversed(s.split()))\n",
                usage=Usage(1, 1),
                model="m",
            )
        if "careful coder" in system and "is_palindrome" in user:
            return LLMResponse(
                text=(
                    "def is_palindrome(s):\n"
                    "    cleaned = ''.join(ch.lower() for ch in s if ch.isalnum())\n"
                    "    return cleaned == cleaned[::-1]\n"
                ),
                usage=Usage(1, 1),
                model="m",
            )
        raise AssertionError("unexpected provider call")

    runs_root = tmp_path / "runs"
    monkeypatch.setattr(artifacts, "RUNS_ROOT", runs_root)
    monkeypatch.setattr("backend.providers.openrouter.stream_openrouter", _fake_stream_openrouter)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    before = Path("workflows/coder_tester.yaml").read_text(encoding="utf-8")

    client = TestClient(app)
    resp = client.post(
        "/api/spec/coder_tester/evaluate-proposal",
        json={
            "proposed_yaml": before,
            "n_per_fixture": 1,
            "max_cost_usd": 2.0,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["validation_errors"] == []
    assert body["eval"]["completed_run_count"] == 3
    assert body["eval"]["overall"]["pass_rate"] == 1.0
    assert body["run_artifact"].endswith("/eval.json")
    assert Path("workflows/coder_tester.yaml").read_text(encoding="utf-8") == before


def test_evaluate_proposal_invalid_yaml_does_not_call_provider(monkeypatch):
    def _fail_provider(**kwargs):
        raise AssertionError("provider should not be called for invalid YAML")

    monkeypatch.setattr("backend.providers.openrouter.stream_openrouter", _fail_provider)
    client = TestClient(app)
    resp = client.post(
        "/api/spec/coder_tester/evaluate-proposal",
        json={
            "proposed_yaml": "schema_version: workflow.graph/v1\nname: broken\nentry: missing\nnodes: []\nbudget:\n  cost_usd: 0.1\n  latency_ms: 1000\n",
            "n_per_fixture": 1,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "invalid"
    assert body["validation_errors"]
    assert body["eval"] is None


def test_evaluate_proposal_missing_workflow_returns_404():
    client = TestClient(app)
    resp = client.post(
        "/api/spec/not_a_workflow/evaluate-proposal",
        json={"proposed_yaml": "name: nope", "n_per_fixture": 1},
    )
    assert resp.status_code == 404


def test_optimize_proposals_valid_invalid_candidates_and_report(
    monkeypatch, tmp_path: Path
):
    runs_root = tmp_path / "runs"
    evals_root = tmp_path / "evals"
    fixture_dir = evals_root / "coder_tester"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "fixtures.yaml").write_text(
        "- id: one\n  input: write fizzbuzz\n  expected: fizzbuzz\n  test_code: \"assert True\"\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(artifacts, "RUNS_ROOT", runs_root)
    monkeypatch.setattr(routes, "EVALS_ROOT", evals_root)

    current_yaml = Path("workflows/coder_tester.yaml").read_text(encoding="utf-8")
    provider_replies = [
        LLMResponse(text=f"Summary: Keep behavior.\n{current_yaml}", usage=Usage(1, 1), model="gpt-4o-mini"),
        LLMResponse(
            text="schema_version: workflow.graph/v1\nname: broken\nentry: missing\nnodes: []\nbudget:\n  cost_usd: 0.1\n  latency_ms: 1000\n",
            usage=Usage(1, 1),
            model="gpt-4o-mini",
        ),
    ]

    def _fake_call_provider(**kwargs):
        return provider_replies.pop(0)

    def _fake_stream_openrouter(**kwargs):
        user = kwargs["messages"][-1]["content"]
        if user.startswith("Task:"):
            return LLMResponse(text="plan", usage=Usage(1, 1), model="m")
        return LLMResponse(text="def fizzbuzz(n):\n    return []\n", usage=Usage(1, 1), model="m")

    monkeypatch.setattr("backend.graphspec.optimization.call_provider", _fake_call_provider)
    monkeypatch.setattr("backend.providers.openrouter.stream_openrouter", _fake_stream_openrouter)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    client = TestClient(app)
    resp = client.post(
        "/api/spec/coder_tester/optimize-proposals",
        json={
            "goal": "Reduce cost.",
            "candidate_count": 2,
            "n_per_fixture": 1,
            "max_cost_usd": 2.0,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow"] == "coder_tester"
    assert body["status"] == "ok"
    assert body["recommended_candidate_id"] == "candidate_1"
    assert Path(body["report_artifact"]).exists()
    assert len(body["candidates"]) == 2
    assert body["candidates"][0]["status"] == "valid"
    assert body["candidates"][0]["recommended"] is True
    assert body["candidates"][0]["eval"]["completed_run_count"] == 1
    assert body["candidates"][1]["status"] == "invalid"
    assert body["candidates"][1]["validation_errors"]
    assert body["candidates"][1]["eval"] is None
    assert Path("workflows/coder_tester.yaml").read_text(encoding="utf-8") == current_yaml


def test_optimize_proposals_cost_cap_stops_cleanly(monkeypatch, tmp_path: Path):
    runs_root = tmp_path / "runs"
    evals_root = tmp_path / "evals"
    fixture_dir = evals_root / "coder_tester"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "fixtures.yaml").write_text(
        "- id: one\n  input: write fizzbuzz\n  expected: fizzbuzz\n  test_code: \"assert True\"\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(artifacts, "RUNS_ROOT", runs_root)
    monkeypatch.setattr(routes, "EVALS_ROOT", evals_root)
    current_yaml = Path("workflows/coder_tester.yaml").read_text(encoding="utf-8")

    def _fake_call_provider(**kwargs):
        return LLMResponse(text=f"Summary: Candidate.\n{current_yaml}", usage=Usage(1, 1), model="gpt-4o-mini")

    def _expensive_openrouter(**kwargs):
        return LLMResponse(text="ok", usage=Usage(1_000_000, 1_000_000), model="m")

    monkeypatch.setattr("backend.graphspec.optimization.call_provider", _fake_call_provider)
    monkeypatch.setattr("backend.providers.openrouter.stream_openrouter", _expensive_openrouter)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    client = TestClient(app)
    resp = client.post(
        "/api/spec/coder_tester/optimize-proposals",
        json={
            "goal": "Reduce cost.",
            "candidate_count": 2,
            "n_per_fixture": 1,
            "max_cost_usd": 0.000001,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "stopped_cost_cap"
    assert body["candidates"][0]["eval"]["status"] in {"stopped_cost_cap", "ok"}
    assert body["candidates"][1]["eval"]["status"] == "skipped_cost_cap"


def test_optimize_proposals_missing_workflow_returns_404():
    client = TestClient(app)
    resp = client.post(
        "/api/spec/not_a_workflow/optimize-proposals",
        json={"goal": "Reduce cost.", "candidate_count": 2},
    )
    assert resp.status_code == 404


def test_optimize_proposals_missing_fixtures_returns_400(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(routes, "EVALS_ROOT", tmp_path / "evals")
    client = TestClient(app)
    resp = client.post(
        "/api/spec/coder_tester/optimize-proposals",
        json={"goal": "Reduce cost.", "candidate_count": 2},
    )
    assert resp.status_code == 400


def test_list_approvals_and_run_detail_include_pending_approval(
    monkeypatch, tmp_path: Path
):
    runs_root = tmp_path / "runs"
    monkeypatch.setattr(artifacts, "RUNS_ROOT", runs_root)
    metadata = graph_spec_to_metadata(load_graph_spec("approval_review"))
    result = run_graph(
        metadata,
        user_input="draft an answer",
        runs_root=runs_root,
        initial_state_overrides={"draft_answer": "Draft text"},
        start_at_node="human_review",
    )
    assert result.status == "pending_approval"

    client = TestClient(app)
    approvals_resp = client.get("/api/approvals")
    assert approvals_resp.status_code == 200
    approvals = approvals_resp.json()
    assert len(approvals) == 1
    assert approvals[0]["run_id"] == result.run_id
    assert approvals[0]["node_id"] == "human_review"
    assert approvals[0]["state_snapshot"]["draft_answer"] == "Draft text"

    run_resp = client.get(f"/api/runs/{result.run_id}")
    assert run_resp.status_code == 200
    body = run_resp.json()
    assert body["status"] == "pending_approval"
    assert body["approval_status"] == "pending"
    assert body["display_status"] == "pending_approval"
    assert body["approval"]["node_id"] == "human_review"
    assert body["approval"]["artifact_path"].endswith("/approval.json")


def _make_pending_approval_run(monkeypatch, tmp_path: Path, *, draft: str = "Draft text"):
    runs_root = tmp_path / "runs"
    monkeypatch.setattr(artifacts, "RUNS_ROOT", runs_root)
    metadata = graph_spec_to_metadata(load_graph_spec("approval_review"))
    result = run_graph(
        metadata,
        user_input="draft an answer",
        runs_root=runs_root,
        initial_state_overrides={"draft_answer": draft},
        start_at_node="human_review",
    )
    assert result.status == "pending_approval"
    return runs_root, result


def test_approval_decision_approved_forks_continuation(monkeypatch, tmp_path: Path):
    runs_root, pending = _make_pending_approval_run(monkeypatch, tmp_path)

    def _fake_openai(**kwargs):
        return LLMResponse(text="Final approved answer.", usage=Usage(1, 1), model="gpt-4o-mini")

    monkeypatch.setattr("backend.providers.openai.stream_openai", _fake_openai)
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    client = TestClient(app)
    resp = client.post(
        f"/api/approvals/{pending.run_id}/decision",
        json={"decision": "approved", "reviewer": "tester", "comment": "Looks good."},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["source_run_id"] == pending.run_id
    assert body["status"] == "resumed"
    assert body["decision"] == "approved"
    assert body["continuation_status"] == "ok"
    continuation_dir = Path(body["continuation_run_dir"])
    assert continuation_dir.exists()
    resume = json.loads((continuation_dir / "approval_resume.json").read_text(encoding="utf-8"))
    assert resume["source_run_id"] == pending.run_id
    assert resume["approval_node_id"] == "human_review"
    assert resume["decision"] == "approved"

    decision_path = resolve_run_dir(runs_root, pending.run_id) / "approval_decision.json"
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    assert decision["reviewer"] == "tester"
    assert decision["comment"] == "Looks good."
    assert decision["continuation_run_id"] == body["continuation_run_id"]

    run_resp = client.get(f"/api/runs/{pending.run_id}")
    assert run_resp.status_code == 200
    run_body = run_resp.json()
    assert run_body["approval_decision"]["decision"] == "approved"
    assert run_body["approval_decision"]["artifact_path"].endswith("/approval_decision.json")
    assert run_body["status"] == "pending_approval"
    assert run_body["approval_status"] == "approved"
    assert run_body["display_status"] == "approved_continued"
    assert run_body["continuation_run_id"] == body["continuation_run_id"]
    assert run_body["continuation_status"] == "ok"

    continuation_resp = client.get(f"/api/runs/{body['continuation_run_id']}")
    assert continuation_resp.status_code == 200
    continuation_body = continuation_resp.json()
    assert continuation_body["approval_resume"]["source_run_id"] == pending.run_id
    assert continuation_body["approval_resume"]["decision"] == "approved"
    assert continuation_body["approval_resume"]["artifact_path"].endswith("/approval_resume.json")


def test_approvals_endpoint_filters_pending_decided_and_all(monkeypatch, tmp_path: Path):
    _runs_root, decided = _make_pending_approval_run(monkeypatch, tmp_path / "decided")
    pending_root = tmp_path / "decided" / "runs"

    def _fake_openai(**kwargs):
        return LLMResponse(text="Final approved answer.", usage=Usage(1, 1), model="gpt-4o-mini")

    monkeypatch.setattr("backend.providers.openai.stream_openai", _fake_openai)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    client = TestClient(app)
    decision_resp = client.post(
        f"/api/approvals/{decided.run_id}/decision",
        json={"decision": "approved"},
    )
    assert decision_resp.status_code == 200

    metadata = graph_spec_to_metadata(load_graph_spec("approval_review"))
    pending = run_graph(
        metadata,
        user_input="another draft",
        runs_root=pending_root,
        initial_state_overrides={"draft_answer": "Pending draft"},
        start_at_node="human_review",
    )
    assert pending.status == "pending_approval"

    pending_resp = client.get("/api/approvals")
    assert pending_resp.status_code == 200
    pending_items = pending_resp.json()
    assert {item["run_id"] for item in pending_items} == {pending.run_id}
    assert pending_items[0]["status"] == "pending"

    decided_resp = client.get("/api/approvals?status=decided")
    assert decided_resp.status_code == 200
    decided_items = decided_resp.json()
    assert {item["run_id"] for item in decided_items} == {decided.run_id}
    assert decided_items[0]["status"] == "decided"
    assert decided_items[0]["approval_decision"]["decision"] == "approved"

    all_resp = client.get("/api/approvals?status=all")
    assert all_resp.status_code == 200
    assert {item["run_id"] for item in all_resp.json()} == {pending.run_id, decided.run_id}


def test_approvals_endpoint_rejects_invalid_status():
    client = TestClient(app)
    resp = client.get("/api/approvals?status=unknown")
    assert resp.status_code == 400


def test_approval_decision_rejected_forks_to_end_without_provider(monkeypatch, tmp_path: Path):
    _runs_root, pending = _make_pending_approval_run(monkeypatch, tmp_path)

    def _fail_openai(**kwargs):
        raise AssertionError("rejected approval should route to END")

    monkeypatch.setattr("backend.providers.openai.stream_openai", _fail_openai)

    client = TestClient(app)
    resp = client.post(
        f"/api/approvals/{pending.run_id}/decision",
        json={"decision": "rejected"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "rejected"
    assert body["continuation_status"] == "ok"


def test_approval_decision_rejects_duplicate_decision(monkeypatch, tmp_path: Path):
    _runs_root, pending = _make_pending_approval_run(monkeypatch, tmp_path)

    def _fake_openai(**kwargs):
        return LLMResponse(text="Final approved answer.", usage=Usage(1, 1), model="gpt-4o-mini")

    monkeypatch.setattr("backend.providers.openai.stream_openai", _fake_openai)
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    client = TestClient(app)
    first = client.post(
        f"/api/approvals/{pending.run_id}/decision",
        json={"decision": "approved"},
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/approvals/{pending.run_id}/decision",
        json={"decision": "approved"},
    )
    assert second.status_code == 409


def test_approval_decision_missing_run_returns_404(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(artifacts, "RUNS_ROOT", tmp_path / "runs")
    client = TestClient(app)
    resp = client.post(
        "/api/approvals/not_a_run/decision",
        json={"decision": "approved"},
    )
    assert resp.status_code == 404


def test_approval_decision_non_pending_run_returns_400(monkeypatch, tmp_path: Path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    _write_single_run(runs_root)
    approval_path = resolve_run_dir(runs_root, "run_api") / "approval.json"
    approval_path.write_text(
        json.dumps(
            {
                "workflow": "approval_review",
                "run_id": "run_api",
                "node_id": "human_review",
                "prompt": "Review.",
                "approval_state_key": "approval_decision",
                "approved_target": "finalizer",
                "rejected_target": "__end__",
                "created_ns": 1,
                "state_snapshot": {"user_input": "x"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(artifacts, "RUNS_ROOT", runs_root)

    client = TestClient(app)
    resp = client.post(
        "/api/approvals/run_api/decision",
        json={"decision": "approved"},
    )
    assert resp.status_code == 400


def test_run_detail_includes_subgraph_parent_child_lineage(monkeypatch, tmp_path: Path):
    runs_root = tmp_path / "runs"
    monkeypatch.setattr(artifacts, "RUNS_ROOT", runs_root)

    replies = [
        LLMResponse(text="refund window", usage=Usage(1, 1), model="gpt-4o-mini"),
        LLMResponse(text="Nimbus refunds are available within 30 days.", usage=Usage(1, 1), model="gpt-4o-mini"),
        LLMResponse(text="The refund window is 30 days.", usage=Usage(1, 1), model="gpt-4o-mini"),
    ]

    def _fake_openai(**kwargs):
        return replies.pop(0)

    monkeypatch.setattr("backend.providers.openai.stream_openai", _fake_openai)
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    metadata = graph_spec_to_metadata(load_graph_spec("rag_subgraph_wrapper"))
    result = run_graph(metadata, user_input="What is the refund window?", runs_root=runs_root)
    assert result.status == "ok"

    client = TestClient(app)
    parent_resp = client.get(f"/api/runs/{result.run_id}")
    assert parent_resp.status_code == 200
    parent_body = parent_resp.json()
    assert len(parent_body["subgraphs"]) == 1
    lineage = parent_body["subgraphs"][0]
    assert lineage["parent_run_id"] == result.run_id
    assert lineage["child_workflow"] == "linear_rag"
    assert lineage["status"] == "ok"
    assert lineage["artifact_path"].endswith(".json")

    child_resp = client.get(f"/api/runs/{lineage['child_run_id']}")
    assert child_resp.status_code == 200
    child_body = child_resp.json()
    assert child_body["parent_run"]["parent_run_id"] == result.run_id
    assert child_body["parent_run"]["node_id"] == "rag_child"
    assert child_body["parent_run"]["artifact_path"].endswith("/parent_run.json")


def test_run_detail_ignores_malformed_subgraph_lineage(monkeypatch, tmp_path: Path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    _write_single_run(runs_root)
    run_dir = resolve_run_dir(runs_root, "run_api")
    subgraphs = run_dir / "subgraphs"
    subgraphs.mkdir()
    (subgraphs / "bad.json").write_text("{not json", encoding="utf-8")
    (run_dir / "parent_run.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(artifacts, "RUNS_ROOT", runs_root)

    client = TestClient(app)
    resp = client.get("/api/runs/run_api")
    assert resp.status_code == 200
    body = resp.json()
    assert "subgraphs" not in body
    assert body["parent_run"] is None


def test_apply_proposal_updates_spec_and_creates_audit(monkeypatch, tmp_path: Path):
    specs_root = _copy_workflow_specs(tmp_path)
    audit_root = tmp_path / "runs" / "spec_audit"
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)
    monkeypatch.setattr(routes, "SPEC_AUDIT_ROOT", audit_root)
    source_path = specs_root / "coder_tester.yaml"
    original = source_path.read_text(encoding="utf-8")
    proposed = original.replace("temperature: 0.2", "temperature: 0.1", 1)

    client = TestClient(app)
    resp = client.post(
        "/api/spec/coder_tester/apply-proposal",
        json={
            "proposed_yaml": proposed,
            "proposal_summary": "Lower planner temperature.",
            "evaluation_artifact": "runs/proposal_eval_coder_tester_test/eval.json",
            "accepted_by": "test-user",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow"] == "coder_tester"
    assert body["status"] == "applied"
    assert body["spec"]["name"] == "coder_tester"
    assert body["yaml"] == proposed
    assert "-    temperature: 0.2" in body["diff"]
    assert "+    temperature: 0.1" in body["diff"]
    assert source_path.read_text(encoding="utf-8") == proposed

    audit_path = Path(body["audit_path"])
    rollback_path = Path(body["rollback_path"])
    assert rollback_path.read_text(encoding="utf-8") == original
    audit = json.loads((audit_path / "audit.json").read_text(encoding="utf-8"))
    assert audit["workflow"] == "coder_tester"
    assert audit["accepted_by"] == "test-user"
    assert audit["proposal_summary"] == "Lower planner temperature."
    assert audit["evaluation_artifact"] == "runs/proposal_eval_coder_tester_test/eval.json"
    assert audit["validation_status"] == "valid"

    spec_resp = client.get("/api/spec/coder_tester")
    assert spec_resp.status_code == 200
    assert "temperature: 0.1" in spec_resp.json()["yaml"]

    graph_resp = client.get("/api/graph/coder_tester")
    assert graph_resp.status_code == 200
    planner = next(node for node in graph_resp.json()["nodes"] if node["id"] == "planner")
    assert planner["metadata"]["temperature"] == 0.1


def test_apply_proposal_invalid_yaml_leaves_file_unchanged(monkeypatch, tmp_path: Path):
    specs_root = _copy_workflow_specs(tmp_path)
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)
    monkeypatch.setattr(routes, "SPEC_AUDIT_ROOT", tmp_path / "runs" / "spec_audit")
    source_path = specs_root / "coder_tester.yaml"
    original = source_path.read_text(encoding="utf-8")

    client = TestClient(app)
    resp = client.post(
        "/api/spec/coder_tester/apply-proposal",
        json={
            "proposed_yaml": (
                "schema_version: workflow.graph/v1\n"
                "name: coder_tester\n"
                "entry: missing\n"
                "nodes: []\n"
                "budget:\n"
                "  cost_usd: 0.1\n"
                "  latency_ms: 1000\n"
            )
        },
    )

    assert resp.status_code == 400
    assert source_path.read_text(encoding="utf-8") == original


def test_apply_proposal_workflow_identity_mismatch_leaves_file_unchanged(
    monkeypatch, tmp_path: Path
):
    specs_root = _copy_workflow_specs(tmp_path)
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)
    monkeypatch.setattr(routes, "SPEC_AUDIT_ROOT", tmp_path / "runs" / "spec_audit")
    source_path = specs_root / "coder_tester.yaml"
    original = source_path.read_text(encoding="utf-8")
    proposed = original.replace("name: coder_tester", "name: linear_rag", 1)

    client = TestClient(app)
    resp = client.post(
        "/api/spec/coder_tester/apply-proposal",
        json={"proposed_yaml": proposed},
    )

    assert resp.status_code == 400
    assert "does not match workflow" in resp.text
    assert source_path.read_text(encoding="utf-8") == original


def test_apply_proposal_missing_workflow_returns_404(monkeypatch, tmp_path: Path):
    specs_root = tmp_path / "workflows"
    specs_root.mkdir()
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)
    monkeypatch.setattr(routes, "SPEC_AUDIT_ROOT", tmp_path / "runs" / "spec_audit")

    client = TestClient(app)
    resp = client.post(
        "/api/spec/not_a_workflow/apply-proposal",
        json={"proposed_yaml": "name: not_a_workflow"},
    )
    assert resp.status_code == 404


def test_rollback_list_preview_and_restore(monkeypatch, tmp_path: Path):
    specs_root = _copy_workflow_specs(tmp_path)
    audit_root = tmp_path / "runs" / "spec_audit"
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)
    monkeypatch.setattr(routes, "SPEC_AUDIT_ROOT", audit_root)
    source_path = specs_root / "coder_tester.yaml"
    original = source_path.read_text(encoding="utf-8")
    proposed = original.replace("temperature: 0.2", "temperature: 0.1", 1)

    client = TestClient(app)
    apply_resp = client.post(
        "/api/spec/coder_tester/apply-proposal",
        json={
            "proposed_yaml": proposed,
            "proposal_summary": "Lower planner temperature.",
            "accepted_by": "test-user",
        },
    )
    assert apply_resp.status_code == 200
    snapshot_id = Path(apply_resp.json()["audit_path"]).name

    list_resp = client.get("/api/spec/coder_tester/rollback-snapshots")
    assert list_resp.status_code == 200
    snapshots = list_resp.json()["snapshots"]
    assert snapshots[0]["snapshot_id"] == snapshot_id
    assert snapshots[0]["accepted_by"] == "test-user"
    assert snapshots[0]["proposal_summary"] == "Lower planner temperature."

    preview_resp = client.get(f"/api/spec/coder_tester/rollback-snapshots/{snapshot_id}/preview")
    assert preview_resp.status_code == 200
    preview = preview_resp.json()
    assert preview["current_yaml"] == proposed
    assert preview["rollback_yaml"] == original
    assert "-    temperature: 0.1" in preview["diff"]
    assert "+    temperature: 0.2" in preview["diff"]

    restore_resp = client.post(
        f"/api/spec/coder_tester/rollback-snapshots/{snapshot_id}/restore",
        json={"accepted_by": "restore-user"},
    )
    assert restore_resp.status_code == 200
    restored = restore_resp.json()
    assert restored["status"] == "restored"
    assert restored["snapshot_id"] == snapshot_id
    assert restored["yaml"] == original
    assert source_path.read_text(encoding="utf-8") == original
    restore_audit = json.loads((Path(restored["audit_path"]) / "audit.json").read_text(encoding="utf-8"))
    assert restore_audit["action"] == "rollback_restore"
    assert restore_audit["snapshot_id"] == snapshot_id
    assert restore_audit["accepted_by"] == "restore-user"
    assert Path(restored["rollback_path"]).read_text(encoding="utf-8") == proposed


def test_rollback_restore_invalid_yaml_leaves_file_unchanged(monkeypatch, tmp_path: Path):
    specs_root = _copy_workflow_specs(tmp_path)
    audit_root = tmp_path / "runs" / "spec_audit"
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)
    monkeypatch.setattr(routes, "SPEC_AUDIT_ROOT", audit_root)
    source_path = specs_root / "coder_tester.yaml"
    original = source_path.read_text(encoding="utf-8")
    snapshot_dir = audit_root / "coder_tester" / "bad_snapshot"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "original.yaml").write_text("not: [valid", encoding="utf-8")

    client = TestClient(app)
    resp = client.post("/api/spec/coder_tester/rollback-snapshots/bad_snapshot/restore", json={})
    assert resp.status_code == 400
    assert source_path.read_text(encoding="utf-8") == original


def test_rollback_restore_identity_mismatch_leaves_file_unchanged(monkeypatch, tmp_path: Path):
    specs_root = _copy_workflow_specs(tmp_path)
    audit_root = tmp_path / "runs" / "spec_audit"
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)
    monkeypatch.setattr(routes, "SPEC_AUDIT_ROOT", audit_root)
    source_path = specs_root / "coder_tester.yaml"
    original = source_path.read_text(encoding="utf-8")
    snapshot_dir = audit_root / "coder_tester" / "wrong_workflow"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "original.yaml").write_text(
        original.replace("name: coder_tester", "name: linear_rag", 1),
        encoding="utf-8",
    )

    client = TestClient(app)
    resp = client.post("/api/spec/coder_tester/rollback-snapshots/wrong_workflow/restore", json={})
    assert resp.status_code == 400
    assert "does not match workflow" in resp.text
    assert source_path.read_text(encoding="utf-8") == original


def test_rollback_missing_workflow_or_snapshot_returns_404(monkeypatch, tmp_path: Path):
    specs_root = _copy_workflow_specs(tmp_path)
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)
    monkeypatch.setattr(routes, "SPEC_AUDIT_ROOT", tmp_path / "runs" / "spec_audit")
    client = TestClient(app)

    missing_workflow = client.get("/api/spec/not_a_workflow/rollback-snapshots")
    assert missing_workflow.status_code == 404

    missing_snapshot = client.get("/api/spec/coder_tester/rollback-snapshots/nope/preview")
    assert missing_snapshot.status_code == 404

    missing_restore = client.post("/api/spec/coder_tester/rollback-snapshots/nope/restore", json={})
    assert missing_restore.status_code == 404


# ── M5.8: nested approval subgraph tests ─────────────────────────────────────

def _make_pending_subgraph_approval_run(monkeypatch, tmp_path: Path):
    """Run approval_subgraph_wrapper until the child hits its approval node."""
    runs_root = tmp_path / "runs"
    monkeypatch.setattr(artifacts, "RUNS_ROOT", runs_root)
    replies = [
        LLMResponse(text="Draft answer for review.", usage=Usage(1, 1), model="gpt-4o-mini"),
    ]
    monkeypatch.setattr("backend.providers.openai.stream_openai", lambda **kw: replies.pop(0))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    metadata = graph_spec_to_metadata(load_graph_spec("approval_subgraph_wrapper"))
    result = run_graph(
        metadata,
        user_input="Write something for review.",
        runs_root=runs_root,
    )
    assert result.status == "pending_approval"
    return runs_root, result


def test_get_run_exposes_pending_subgraph_lineage(monkeypatch, tmp_path: Path):
    runs_root, parent = _make_pending_subgraph_approval_run(monkeypatch, tmp_path)

    client = TestClient(app)
    resp = client.get(f"/api/runs/{parent.run_id}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["display_status"] == "pending_child_approval"
    assert "pending_subgraph_approval" in body
    psa = body["pending_subgraph_approval"]
    assert psa["parent_run_id"] == parent.run_id
    assert psa["node_id"] == "review_child"
    assert psa["child_workflow"] == "approval_review"
    assert "approval" not in body or body.get("approval") is None
    assert body["pending_child_run_id"] == psa["child_run_id"]


def test_decide_child_approval_forks_parent_continuation(monkeypatch, tmp_path: Path):
    runs_root, parent = _make_pending_subgraph_approval_run(monkeypatch, tmp_path)
    pending_path = parent.run_dir / "pending_subgraph_approval.json"
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    child_run_id = pending["child_run_id"]

    # Continuation of the child approval hits the finalizer (an LLM node).
    monkeypatch.setattr(
        "backend.providers.openai.stream_openai",
        lambda **kw: LLMResponse(text="Final approved text.", usage=Usage(1, 1), model="gpt-4o-mini"),
    )

    client = TestClient(app)
    resp = client.post(
        f"/api/approvals/{child_run_id}/decision",
        json={"decision": "approved", "reviewer": "tester", "comment": "LGTM"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["continuation_status"] == "ok"
    assert "parent_continuation_run_id" in body
    parent_cont_run_id = body["parent_continuation_run_id"]
    assert parent_cont_run_id is not None

    # Source parent run dir should have subgraph_decision.json.
    parent_run_dir = resolve_run_dir(runs_root, parent.run_id)
    sd_path = parent_run_dir / "subgraph_decision.json"
    assert sd_path.exists()
    sd = json.loads(sd_path.read_text(encoding="utf-8"))
    assert sd["parent_continuation_run_id"] == parent_cont_run_id
    assert sd["decision"] == "approved"

    # Parent continuation run dir should have subgraph_resume.json.
    parent_cont_dir = resolve_run_dir(runs_root, parent_cont_run_id)
    sr_path = parent_cont_dir / "subgraph_resume.json"
    assert sr_path.exists()
    sr = json.loads(sr_path.read_text(encoding="utf-8"))
    assert sr["source_parent_run_id"] == parent.run_id


def test_get_run_after_child_decision_shows_child_decided_continued(monkeypatch, tmp_path: Path):
    runs_root, parent = _make_pending_subgraph_approval_run(monkeypatch, tmp_path)
    pending = json.loads((parent.run_dir / "pending_subgraph_approval.json").read_text(encoding="utf-8"))
    child_run_id = pending["child_run_id"]

    monkeypatch.setattr(
        "backend.providers.openai.stream_openai",
        lambda **kw: LLMResponse(text="Final approved text.", usage=Usage(1, 1), model="gpt-4o-mini"),
    )

    client = TestClient(app)
    client.post(
        f"/api/approvals/{child_run_id}/decision",
        json={"decision": "approved"},
    )

    resp = client.get(f"/api/runs/{parent.run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["display_status"] == "child_decided_continued"
    assert "subgraph_decision" in body
    assert body["parent_continuation_run_id"] is not None


def test_duplicate_child_decision_still_rejected(monkeypatch, tmp_path: Path):
    runs_root, parent = _make_pending_subgraph_approval_run(monkeypatch, tmp_path)
    pending = json.loads((parent.run_dir / "pending_subgraph_approval.json").read_text(encoding="utf-8"))
    child_run_id = pending["child_run_id"]

    monkeypatch.setattr(
        "backend.providers.openai.stream_openai",
        lambda **kw: LLMResponse(text="Final approved text.", usage=Usage(1, 1), model="gpt-4o-mini"),
    )

    client = TestClient(app)
    first = client.post(f"/api/approvals/{child_run_id}/decision", json={"decision": "approved"})
    assert first.status_code == 200
    second = client.post(f"/api/approvals/{child_run_id}/decision", json={"decision": "approved"})
    assert second.status_code == 409


# ── M5.10: list workflows endpoint ───────────────────────────────────────────

_MINIMAL_LLM_NODE = (
    "  - id: {node_id}\n"
    "    kind: llm\n"
    "    provider: openai\n"
    "    model: gpt-4o-mini\n"
    "    system_prompt: system\n"
    "    user_prompt_template: '{{user_input}}'\n"
    "    output_state_key: answer\n"
)

def _minimal_spec(name: str, description: str, node_id: str) -> str:
    return (
        "schema_version: workflow.graph/v1\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"entry: {node_id}\n"
        "nodes:\n"
        + _MINIMAL_LLM_NODE.format(node_id=node_id)
        + "edges: []\n"
        "budget:\n"
        "  cost_usd: 1.0\n"
        "  latency_ms: 30000\n"
    )


def test_list_workflows_endpoint(monkeypatch, tmp_path: Path):
    specs_root = tmp_path / "workflows"
    specs_root.mkdir()
    (specs_root / "alpha.yaml").write_text(_minimal_spec("Alpha Workflow", "First workflow", "node_a"), encoding="utf-8")
    (specs_root / "beta.yaml").write_text(_minimal_spec("Beta Workflow", "Second workflow", "node_b"), encoding="utf-8")
    evals_root = tmp_path / "evals"
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)
    monkeypatch.setattr(routes, "EVALS_ROOT", evals_root)

    client = TestClient(app)
    resp = client.get("/api/workflows")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    assert items[0]["id"] == "alpha"
    assert items[0]["name"] == "Alpha Workflow"
    assert items[0]["description"] == "First workflow"
    assert items[0]["category"] == "general"
    assert items[0]["tags"] == []
    assert items[0]["template"] is False
    assert items[0]["template_parameter_count"] == 0
    assert items[0]["validation_status"] == "valid"
    assert items[0]["validation_errors"] == []
    assert items[0]["source_path"].endswith("alpha.yaml")
    assert items[0]["facts"] == {
        "node_count": 1,
        "edge_count": 0,
        "loop_count": 0,
        "approval_node_count": 0,
        "subgraph_node_count": 0,
    }
    assert items[0]["eval_quality"] == {
        "fixture_status": "missing",
        "fixture_path": (evals_root / "alpha" / "fixtures.yaml").as_posix(),
        "fixture_count": 0,
        "fixture_errors": [],
        "baseline_status": "not_applicable",
        "baseline_path": (evals_root / "alpha" / "baseline.json").as_posix(),
        "baseline_updated_at": None,
        "stale_reasons": [],
    }
    assert items[1]["id"] == "beta"
    assert items[1]["name"] == "Beta Workflow"


def test_list_workflows_broken_spec_still_listed(monkeypatch, tmp_path: Path):
    specs_root = tmp_path / "workflows"
    specs_root.mkdir()
    (specs_root / "good.yaml").write_text(_minimal_spec("Good", "OK", "node_g"), encoding="utf-8")
    (specs_root / "broken.yaml").write_text("not: [valid yaml for graphspec", encoding="utf-8")
    evals_root = tmp_path / "evals"
    broken_eval_dir = evals_root / "broken"
    broken_eval_dir.mkdir(parents=True)
    (broken_eval_dir / "fixtures.yaml").write_text(
        "- id: one\n  input: hello\n  expected: hello\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)
    monkeypatch.setattr(routes, "EVALS_ROOT", evals_root)

    client = TestClient(app)
    resp = client.get("/api/workflows")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    broken = next(i for i in items if i["id"] == "broken")
    assert broken["name"] == "broken"
    assert broken["description"] == ""
    assert broken["category"] == "general"
    assert broken["tags"] == []
    assert broken["template"] is False
    assert broken["template_parameter_count"] == 0
    assert broken["validation_status"] == "invalid"
    assert broken["validation_errors"]
    assert broken["source_path"].endswith("broken.yaml")
    assert broken["facts"] == {
        "node_count": 0,
        "edge_count": 0,
        "loop_count": 0,
        "approval_node_count": 0,
        "subgraph_node_count": 0,
    }
    assert broken["eval_quality"]["fixture_status"] == "present"
    assert broken["eval_quality"]["fixture_count"] == 1
    assert broken["eval_quality"]["baseline_status"] == "missing"


def test_list_workflows_endpoint_returns_library_metadata(monkeypatch, tmp_path: Path):
    specs_root = tmp_path / "workflows"
    specs_root.mkdir()
    (specs_root / "searchable.yaml").write_text(
        _minimal_spec("Searchable", "Has metadata", "node_s")
        .replace("description: Has metadata\n", "description: Has metadata\ncategory: rag\ntags:\n  - retrieval\n  - example\n"),
        encoding="utf-8",
    )
    evals_root = tmp_path / "evals"
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)
    monkeypatch.setattr(routes, "EVALS_ROOT", evals_root)

    client = TestClient(app)
    resp = client.get("/api/workflows")
    assert resp.status_code == 200
    assert resp.json() == [
        {
            "id": "searchable",
            "name": "Searchable",
            "description": "Has metadata",
            "category": "rag",
            "tags": ["retrieval", "example"],
            "template": False,
            "template_parameter_count": 0,
            "validation_status": "valid",
            "validation_errors": [],
            "source_path": (specs_root / "searchable.yaml").as_posix(),
            "facts": {
                "node_count": 1,
                "edge_count": 0,
                "loop_count": 0,
                "approval_node_count": 0,
                "subgraph_node_count": 0,
            },
            "eval_quality": {
                "fixture_status": "missing",
                "fixture_path": (evals_root / "searchable" / "fixtures.yaml").as_posix(),
                "fixture_count": 0,
                "fixture_errors": [],
                "baseline_status": "not_applicable",
                "baseline_path": (evals_root / "searchable" / "baseline.json").as_posix(),
                "baseline_updated_at": None,
                "stale_reasons": [],
            },
        }
    ]


def test_list_workflows_invalid_graphspec_still_listed_with_errors(monkeypatch, tmp_path: Path):
    specs_root = tmp_path / "workflows"
    specs_root.mkdir()
    (specs_root / "invalid.yaml").write_text(
        (
            "schema_version: workflow.graph/v1\n"
            "name: Invalid\n"
            "entry: missing\n"
            "nodes: []\n"
            "budget:\n"
            "  cost_usd: 1.0\n"
            "  latency_ms: 30000\n"
        ),
        encoding="utf-8",
    )
    evals_root = tmp_path / "evals"
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)
    monkeypatch.setattr(routes, "EVALS_ROOT", evals_root)

    client = TestClient(app)
    resp = client.get("/api/workflows")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["id"] == "invalid"
    assert body[0]["validation_status"] == "invalid"
    assert body[0]["validation_errors"]
    assert "entry node" in body[0]["validation_errors"][0]
    assert body[0]["facts"]["node_count"] == 0
    assert body[0]["eval_quality"]["fixture_status"] == "missing"
    assert body[0]["eval_quality"]["baseline_status"] == "not_applicable"


def test_list_workflows_counts_approval_and_subgraph_nodes(monkeypatch, tmp_path: Path):
    specs_root = tmp_path / "workflows"
    specs_root.mkdir()
    (specs_root / "child_wf.yaml").write_text(
        _minimal_spec("Child", "Child workflow", "child_node"),
        encoding="utf-8",
    )
    (specs_root / "review_wrapper.yaml").write_text(
        (
            "schema_version: workflow.graph/v1\n"
            "name: Review Wrapper\n"
            "description: Approval and subgraph facts.\n"
            "entry: review\n"
            "nodes:\n"
            "  - id: review\n"
            "    kind: approval\n"
            "    prompt: Review this.\n"
            "    approved_target: child\n"
            "    rejected_target: __end__\n"
            "  - id: child\n"
            "    kind: subgraph\n"
            "    workflow: child_wf\n"
            "    inputs:\n"
            "      user_input: user_input\n"
            "    outputs:\n"
            "      final_answer: answer\n"
            "budget:\n"
            "  cost_usd: 1.0\n"
            "  latency_ms: 30000\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(routes, "EVALS_ROOT", tmp_path / "evals")
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)

    client = TestClient(app)
    resp = client.get("/api/workflows")
    assert resp.status_code == 200
    wrapper = next(item for item in resp.json() if item["id"] == "review_wrapper")
    assert wrapper["validation_status"] == "valid"
    assert wrapper["facts"] == {
        "node_count": 2,
        "edge_count": 0,
        "loop_count": 0,
        "approval_node_count": 1,
        "subgraph_node_count": 1,
    }


def test_list_workflows_eval_quality_fresh_baseline(monkeypatch, tmp_path: Path):
    specs_root = tmp_path / "workflows"
    evals_root = tmp_path / "evals"
    specs_root.mkdir()
    eval_dir = evals_root / "alpha"
    eval_dir.mkdir(parents=True)
    spec_path = specs_root / "alpha.yaml"
    fixtures_path = eval_dir / "fixtures.yaml"
    baseline_path = eval_dir / "baseline.json"
    spec_path.write_text(_minimal_spec("Alpha", "First workflow", "node_a"), encoding="utf-8")
    fixtures_path.write_text("- id: one\n  input: hello\n  expected: hello\n", encoding="utf-8")
    baseline_path.write_text(json.dumps({"workflow": "alpha", "overall": {}}), encoding="utf-8")
    now = time.time()
    old = now - 100
    spec_path.touch()
    fixtures_path.touch()
    baseline_path.touch()
    import os

    os.utime(spec_path, (old, old))
    os.utime(fixtures_path, (old + 10, old + 10))
    os.utime(baseline_path, (now, now))
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)
    monkeypatch.setattr(routes, "EVALS_ROOT", evals_root)

    client = TestClient(app)
    resp = client.get("/api/workflows")
    assert resp.status_code == 200
    quality = resp.json()[0]["eval_quality"]
    assert quality["fixture_status"] == "present"
    assert quality["fixture_count"] == 1
    assert quality["fixture_errors"] == []
    assert quality["baseline_status"] == "fresh"
    assert quality["baseline_path"] == baseline_path.as_posix()
    assert isinstance(quality["baseline_updated_at"], int)
    assert quality["stale_reasons"] == []


def test_list_workflows_eval_quality_invalid_fixture(monkeypatch, tmp_path: Path):
    specs_root = tmp_path / "workflows"
    evals_root = tmp_path / "evals"
    specs_root.mkdir()
    eval_dir = evals_root / "alpha"
    eval_dir.mkdir(parents=True)
    (specs_root / "alpha.yaml").write_text(_minimal_spec("Alpha", "First workflow", "node_a"), encoding="utf-8")
    (eval_dir / "fixtures.yaml").write_text("not_a_list: true\n", encoding="utf-8")
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)
    monkeypatch.setattr(routes, "EVALS_ROOT", evals_root)

    client = TestClient(app)
    resp = client.get("/api/workflows")
    assert resp.status_code == 200
    quality = resp.json()[0]["eval_quality"]
    assert quality["fixture_status"] == "invalid"
    assert quality["fixture_count"] == 0
    assert quality["fixture_errors"]
    assert quality["baseline_status"] == "not_applicable"


def test_list_workflows_eval_quality_missing_invalid_and_stale_baselines(monkeypatch, tmp_path: Path):
    specs_root = tmp_path / "workflows"
    evals_root = tmp_path / "evals"
    specs_root.mkdir()
    import os

    for wid in ("missing_base", "invalid_base", "stale_base"):
        (specs_root / f"{wid}.yaml").write_text(_minimal_spec(wid, "Workflow", "node_a"), encoding="utf-8")
        eval_dir = evals_root / wid
        eval_dir.mkdir(parents=True)
        (eval_dir / "fixtures.yaml").write_text("- id: one\n  input: hello\n  expected: hello\n", encoding="utf-8")

    (evals_root / "invalid_base" / "baseline.json").write_text("{not valid json", encoding="utf-8")
    stale_baseline = evals_root / "stale_base" / "baseline.json"
    stale_baseline.write_text(json.dumps({"workflow": "stale_base", "overall": {}}), encoding="utf-8")
    old = time.time() - 100
    os.utime(stale_baseline, (old, old))
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)
    monkeypatch.setattr(routes, "EVALS_ROOT", evals_root)

    client = TestClient(app)
    resp = client.get("/api/workflows")
    assert resp.status_code == 200
    by_id = {item["id"]: item["eval_quality"] for item in resp.json()}
    assert by_id["missing_base"]["baseline_status"] == "missing"
    assert by_id["invalid_base"]["baseline_status"] == "invalid"
    assert by_id["invalid_base"]["fixture_errors"]
    assert by_id["stale_base"]["baseline_status"] == "stale"
    assert "workflow spec is newer than baseline" in by_id["stale_base"]["stale_reasons"]


def test_list_workflows_eval_quality_mismatched_baseline_invalid(monkeypatch, tmp_path: Path):
    specs_root = tmp_path / "workflows"
    evals_root = tmp_path / "evals"
    specs_root.mkdir()
    eval_dir = evals_root / "alpha"
    eval_dir.mkdir(parents=True)
    (specs_root / "alpha.yaml").write_text(_minimal_spec("Alpha", "First workflow", "node_a"), encoding="utf-8")
    (eval_dir / "fixtures.yaml").write_text("- id: one\n  input: hello\n  expected: hello\n", encoding="utf-8")
    (eval_dir / "baseline.json").write_text(json.dumps({"workflow": "beta"}), encoding="utf-8")
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)
    monkeypatch.setattr(routes, "EVALS_ROOT", evals_root)

    client = TestClient(app)
    resp = client.get("/api/workflows")
    assert resp.status_code == 200
    quality = resp.json()[0]["eval_quality"]
    assert quality["baseline_status"] == "invalid"
    assert "does not match workflow" in quality["fixture_errors"][0]


def _template_spec(name: str = "Template") -> str:
    return _minimal_spec(name, "Copy me", "node_t").replace(
        "description: Copy me\n",
        (
            "description: Copy me\n"
            "category: template\n"
            "tags:\n"
            "  - starter\n"
            "template: true\n"
            "template_parameters:\n"
            "  - key: user_input\n"
            "    description: Primary user task.\n"
            "    state_key: user_input\n"
            "    example: Say hello.\n"
        ),
    )


def test_list_workflows_endpoint_returns_template_flag(monkeypatch, tmp_path: Path):
    specs_root = tmp_path / "workflows"
    specs_root.mkdir()
    (specs_root / "starter.yaml").write_text(_template_spec("Starter"), encoding="utf-8")
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)

    client = TestClient(app)
    resp = client.get("/api/workflows")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["id"] == "starter"
    assert body[0]["template"] is True
    assert body[0]["template_parameter_count"] == 1


def test_copy_template_writes_new_workflow_and_audit(monkeypatch, tmp_path: Path):
    specs_root = tmp_path / "workflows"
    specs_root.mkdir()
    audit_root = tmp_path / "runs" / "spec_audit"
    (specs_root / "starter.yaml").write_text(_template_spec("Starter"), encoding="utf-8")
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)
    monkeypatch.setattr(routes, "SPEC_AUDIT_ROOT", audit_root)

    client = TestClient(app)
    resp = client.post(
        "/api/workflows/starter/copy-template",
        json={
            "new_workflow_id": "copied_workflow",
            "description": "Copied workflow.",
            "category": "general",
            "tags": ["copied"],
            "accepted_by": "tester",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow"] == "copied_workflow"
    assert body["source_template"] == "starter"
    assert body["status"] == "copied"
    assert body["source_path"] == (specs_root / "copied_workflow.yaml").as_posix()
    assert body["spec"]["name"] == "copied_workflow"
    assert body["spec"]["description"] == "Copied workflow."
    assert body["spec"]["category"] == "general"
    assert body["spec"]["tags"] == ["copied"]
    assert body["spec"]["template"] is False
    assert body["spec"]["template_parameters"] == []
    written = specs_root / "copied_workflow.yaml"
    assert written.exists()
    written_text = written.read_text(encoding="utf-8")
    assert "template: false" in written_text
    assert "template_parameters: []" in written_text
    audit = json.loads((Path(body["audit_path"]) / "audit.json").read_text(encoding="utf-8"))
    assert audit["action"] == "template_copy"
    assert audit["source_template"] == "starter"
    assert audit["workflow"] == "copied_workflow"
    assert audit["accepted_by"] == "tester"


def test_copy_template_rejects_non_template_source(monkeypatch, tmp_path: Path):
    specs_root = tmp_path / "workflows"
    specs_root.mkdir()
    (specs_root / "regular.yaml").write_text(_minimal_spec("Regular", "Not a template", "node_r"), encoding="utf-8")
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)

    client = TestClient(app)
    resp = client.post(
        "/api/workflows/regular/copy-template",
        json={"new_workflow_id": "copy_target"},
    )
    assert resp.status_code == 400
    assert "not a template" in resp.text


def test_copy_template_rejects_invalid_or_existing_target(monkeypatch, tmp_path: Path):
    specs_root = tmp_path / "workflows"
    specs_root.mkdir()
    (specs_root / "starter.yaml").write_text(_template_spec("Starter"), encoding="utf-8")
    (specs_root / "existing.yaml").write_text(_minimal_spec("Existing", "Existing", "node_e"), encoding="utf-8")
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)

    client = TestClient(app)
    invalid = client.post(
        "/api/workflows/starter/copy-template",
        json={"new_workflow_id": "Bad-Id"},
    )
    assert invalid.status_code == 400

    existing = client.post(
        "/api/workflows/starter/copy-template",
        json={"new_workflow_id": "existing"},
    )
    assert existing.status_code == 409


def test_copy_template_rejects_invalid_metadata_override(monkeypatch, tmp_path: Path):
    specs_root = tmp_path / "workflows"
    specs_root.mkdir()
    (specs_root / "starter.yaml").write_text(_template_spec("Starter"), encoding="utf-8")
    monkeypatch.setattr(routes, "WORKFLOW_SPECS_ROOT", specs_root)

    client = TestClient(app)
    resp = client.post(
        "/api/workflows/starter/copy-template",
        json={"new_workflow_id": "bad_copy", "tags": "not-a-list"},
    )
    assert resp.status_code == 422
    assert not (specs_root / "bad_copy.yaml").exists()
