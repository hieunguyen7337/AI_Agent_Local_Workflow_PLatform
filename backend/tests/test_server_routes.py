"""FastAPI route tests for graph telemetry overlays."""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.server import routes
from backend.server.app import app
from backend.telemetry.exporter import ensure_schema
from backend.providers.base import LLMResponse, Usage
from backend.graphspec import graph_spec_to_metadata, load_graph_spec
from backend.runtime.executor import run_graph


def _write_single_run(runs_root: Path) -> None:
    run_dir = runs_root / "run_api"
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
    monkeypatch.setattr(routes, "RUNS_ROOT", runs_root)

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


def test_get_graph_linear_rag_shape():
    client = TestClient(app)
    resp = client.get("/api/graph/linear_rag")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "linear_rag"
    node_ids = {n["id"] for n in body["nodes"]}
    assert {"query_analyser", "retriever", "reranker", "synthesiser"} <= node_ids


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
    monkeypatch.setattr(routes, "RUNS_ROOT", runs_root)
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
    monkeypatch.setattr(routes, "RUNS_ROOT", runs_root)
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
    monkeypatch.setattr(routes, "RUNS_ROOT", runs_root)
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
    monkeypatch.setattr(routes, "RUNS_ROOT", runs_root)
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
    assert body["approval"]["node_id"] == "human_review"
    assert body["approval"]["artifact_path"].endswith("/approval.json")


def _make_pending_approval_run(monkeypatch, tmp_path: Path, *, draft: str = "Draft text"):
    runs_root = tmp_path / "runs"
    monkeypatch.setattr(routes, "RUNS_ROOT", runs_root)
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

    decision_path = runs_root / pending.run_id / "approval_decision.json"
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    assert decision["reviewer"] == "tester"
    assert decision["comment"] == "Looks good."
    assert decision["continuation_run_id"] == body["continuation_run_id"]

    run_resp = client.get(f"/api/runs/{pending.run_id}")
    assert run_resp.status_code == 200
    run_body = run_resp.json()
    assert run_body["approval_decision"]["decision"] == "approved"
    assert run_body["approval_decision"]["artifact_path"].endswith("/approval_decision.json")

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
    monkeypatch.setattr(routes, "RUNS_ROOT", tmp_path / "runs")
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
    approval_path = runs_root / "run_api" / "approval.json"
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
    monkeypatch.setattr(routes, "RUNS_ROOT", runs_root)

    client = TestClient(app)
    resp = client.post(
        "/api/approvals/run_api/decision",
        json={"decision": "approved"},
    )
    assert resp.status_code == 400


def test_run_detail_includes_subgraph_parent_child_lineage(monkeypatch, tmp_path: Path):
    runs_root = tmp_path / "runs"
    monkeypatch.setattr(routes, "RUNS_ROOT", runs_root)

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
    subgraphs = runs_root / "run_api" / "subgraphs"
    subgraphs.mkdir()
    (subgraphs / "bad.json").write_text("{not json", encoding="utf-8")
    (runs_root / "run_api" / "parent_run.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(routes, "RUNS_ROOT", runs_root)

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
