"""Runtime integration tests with mocked providers."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from backend.builder.api import (
    END,
    ApprovalNodeConfig,
    GateNodeConfig,
    GraphBuilder,
    LLMNodeConfig,
    TesterNodeConfig,
)
from backend.providers import openai as oai
from backend.providers import openrouter as orouter
from backend.providers.base import LLMResponse, Usage
from backend.graphspec import graph_spec_to_metadata, load_graph_spec
from backend.runtime.cancellation import CancellationController
from backend.runtime.executor import run_graph
from backend.runtime.artifacts import resolve_run_dir
from backend.runtime.nodes.llm import make_llm_node


class _Replies:
    """Deterministic scripted replies for OpenRouter calls, keyed by call order."""

    def __init__(self, replies: list[LLMResponse]):
        self._replies = list(replies)

    def __call__(self, **kwargs) -> LLMResponse:
        if not self._replies:
            return LLMResponse(text="", usage=Usage(0, 0), model=kwargs.get("model", "x"))
        return self._replies.pop(0)


@pytest.fixture
def tmp_runs_root(tmp_path: Path) -> Path:
    root = tmp_path / "runs"
    root.mkdir()
    return root


def _build(max_iter: int = 3) -> GraphBuilder:
    b = GraphBuilder(name="ct_test", cost_budget_usd=1.0, latency_budget_ms=60_000)
    b.add_node(
        LLMNodeConfig(
            id="coder",
            model="minimax/minimax-m2.7",
            system_prompt="code",
            user_prompt_template="{user_input}",
            output_state_key="coder_output",
        )
    )
    b.add_node(TesterNodeConfig(id="tester", model="minimax/minimax-m2.7"))
    b.add_node(GateNodeConfig(id="gate", pass_target=END, fail_target="coder"))
    b.set_entry("coder")
    b.add_edge("coder", "tester")
    b.add_edge("tester", "gate")
    b.add_loop("gate", "coder", max_iterations=max_iter)
    return b


def test_llm_node_without_image_inputs_sends_text_content(monkeypatch):
    captured: dict = {}

    def _reply(_provider, **kwargs):
        captured["messages"] = kwargs["messages"]
        return LLMResponse(text="ok", usage=Usage(1, 1), model=kwargs["model"])

    monkeypatch.setattr("backend.runtime.nodes.llm.stream_provider", _reply)
    cfg = LLMNodeConfig(
        id="answer",
        model="minimax/minimax-m2.7",
        system_prompt="system",
        user_prompt_template="Task: {user_input}",
        output_state_key="answer",
    )
    node = make_llm_node(cfg, run_id="run1", graph_name="graph", on_cost=lambda _cost: None)

    assert node({"user_input": "hello"}) == {"answer": "ok"}
    assert captured["messages"][1]["content"] == "Task: hello"


def test_llm_node_with_image_inputs_sends_base64_image_part(monkeypatch, tmp_path: Path):
    image_path = tmp_path / "query.jpg"
    image_path.write_bytes(b"fake-jpeg")
    captured: dict = {}

    def _reply(_provider, **kwargs):
        captured["messages"] = kwargs["messages"]
        return LLMResponse(text="ok", usage=Usage(1, 1), model=kwargs["model"])

    monkeypatch.setattr("backend.runtime.nodes.llm.stream_provider", _reply)
    cfg = LLMNodeConfig(
        id="vision",
        model="qwen/qwen3-vl-8b-instruct",
        system_prompt="system",
        user_prompt_template="Inspect {user_input}",
        output_state_key="answer",
        image_inputs=[{"state_key": "query_image_path", "detail": "low"}],
    )
    node = make_llm_node(cfg, run_id="run2", graph_name="graph", on_cost=lambda _cost: None)

    assert node({"user_input": "q1", "query_image_path": str(image_path)}) == {"answer": "ok"}
    content = captured["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "Inspect q1"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["detail"] == "low"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_happy_path_passes_gate(monkeypatch, tmp_runs_root):
    replies = [
        LLMResponse(text="def f(): pass", usage=Usage(5, 3), model="m"),  # coder
        LLMResponse(text="PASS\ngood", usage=Usage(4, 2), model="m"),  # tester
    ]
    monkeypatch.setattr(orouter, "stream_openrouter", _Replies(replies))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    metadata = _build().compile()
    result = run_graph(
        metadata, user_input="write f", expected="a function f", runs_root=tmp_runs_root
    )
    assert result.status == "ok"
    assert result.final_state.get("tester_verdict") is True
    assert result.final_state.get("tester_mode") == "llm_judge"
    assert re.match(r"^run_\d{8}T\d{6}Z_ct_test_[0-9a-f]{8}$", result.run_id)
    assert result.run_dir == resolve_run_dir(tmp_runs_root, result.run_id)
    assert result.run_dir.parent.name == result.run_id[:12].removeprefix("run_")
    manifest = json.loads((result.run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["workflow"] == "ct_test"
    assert manifest["run_id"] == result.run_id
    assert manifest["status"] == "ok"
    assert manifest["artifacts"]["telemetry_db"].endswith("/telemetry.db")


def test_loop_then_pass(monkeypatch, tmp_runs_root):
    replies = [
        LLMResponse(text="attempt 1", usage=Usage(5, 3), model="m"),  # coder
        LLMResponse(text="FAIL\ntry again", usage=Usage(4, 2), model="m"),  # tester -> fail
        LLMResponse(text="attempt 2", usage=Usage(5, 3), model="m"),  # coder again
        LLMResponse(text="PASS\ngood", usage=Usage(4, 2), model="m"),  # tester -> pass
    ]
    monkeypatch.setattr(orouter, "stream_openrouter", _Replies(replies))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    metadata = _build().compile()
    result = run_graph(
        metadata, user_input="do x", expected="result", runs_root=tmp_runs_root
    )
    assert result.status == "ok"
    assert result.final_state.get("tester_verdict") is True


def test_max_iterations_halts_cleanly(monkeypatch, tmp_runs_root):
    always_fail = [
        LLMResponse(text="c", usage=Usage(1, 1), model="m"),
        LLMResponse(text="FAIL", usage=Usage(1, 1), model="m"),
    ] * 10
    monkeypatch.setattr(orouter, "stream_openrouter", _Replies(always_fail))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    metadata = _build(max_iter=1).compile()
    result = run_graph(
        metadata, user_input="do x", expected="result", runs_root=tmp_runs_root
    )
    assert result.status == "MaxIterationsError"


def test_budget_exceeded_halts(monkeypatch, tmp_runs_root):
    # Each coder/tester call reports huge token counts -> exceeds a tiny budget.
    replies = [
        LLMResponse(text="c", usage=Usage(1_000_000, 1_000_000), model="m"),
        LLMResponse(text="FAIL", usage=Usage(1_000_000, 1_000_000), model="m"),
    ] * 10
    monkeypatch.setattr(orouter, "stream_openrouter", _Replies(replies))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    b = GraphBuilder(name="tiny", cost_budget_usd=0.0001, latency_budget_ms=60_000)
    b.add_node(
        LLMNodeConfig(
            id="coder",
            model="minimax/minimax-m2.7",
            system_prompt="s",
            user_prompt_template="{user_input}",
            output_state_key="coder_output",
        )
    )
    b.add_node(TesterNodeConfig(id="tester", model="minimax/minimax-m2.7"))
    b.add_node(GateNodeConfig(id="gate", pass_target=END, fail_target="coder"))
    b.set_entry("coder")
    b.add_edge("coder", "tester")
    b.add_edge("tester", "gate")
    b.add_loop("gate", "coder", max_iterations=3)

    metadata = b.compile()
    result = run_graph(
        metadata, user_input="x", expected="y", runs_root=tmp_runs_root
    )
    assert result.status == "budget_exceeded"


def test_sandbox_mode_with_test_code_skips_llm_tester(monkeypatch, tmp_runs_root):
    calls: list[int] = []

    def _reply(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            return LLMResponse(
                text="def add(a, b):\n    return a + b\n",
                usage=Usage(1, 1),
                model="m",
            )
        raise AssertionError("tester should not call OpenRouter in sandbox mode")

    monkeypatch.setattr(orouter, "stream_openrouter", _reply)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    metadata = _build().compile()
    result = run_graph(
        metadata,
        user_input="write add",
        expected="function add",
        runs_root=tmp_runs_root,
        initial_state_overrides={"_test_code": "assert add(1, 2) == 3"},
    )
    assert result.status == "ok"
    assert result.final_state.get("tester_verdict") is True
    assert result.final_state.get("tester_mode") == "sandbox"
    assert len(calls) == 1


def test_openai_provider_runtime_sets_genai_system(monkeypatch, tmp_runs_root):
    calls: list[int] = []

    def _reply(**kwargs):
        calls.append(1)
        return LLMResponse(text="openai result", usage=Usage(2, 3), model="gpt-4o-mini")

    monkeypatch.setattr(oai, "stream_openai", _reply)
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    b = GraphBuilder(name="openai_graph", cost_budget_usd=1.0, latency_budget_ms=60_000)
    b.add_node(
        LLMNodeConfig(
            id="answer",
            provider="openai",
            model="gpt-4o-mini",
            system_prompt="answer",
            user_prompt_template="{user_input}",
            output_state_key="final_answer",
        )
    )
    b.set_entry("answer")
    b.add_edge("answer", END)

    result = run_graph(b.compile(), user_input="hello", runs_root=tmp_runs_root)
    assert result.status == "ok"
    assert result.final_state.get("final_answer") == "openai result"
    assert len(calls) == 1

    with sqlite3.connect(result.run_dir / "telemetry.db") as con:
        attrs_json = con.execute(
            "SELECT attributes_json FROM spans WHERE run_id=? AND node_id=?",
            (result.run_id, "answer"),
        ).fetchone()[0]
    attrs = json.loads(attrs_json)
    assert attrs["gen_ai.system"] == "openai"


def test_cancellation_during_llm_generation_marks_run_cancelled(monkeypatch, tmp_runs_root):
    def _stream_reply(**kwargs):
        cancel_check = kwargs["cancel_check"]
        while not cancel_check():
            time.sleep(0.01)
        raise AssertionError("cancel_check should have interrupted before returning")

    monkeypatch.setattr(orouter, "stream_openrouter", _stream_reply)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    controller = CancellationController()
    timer = threading.Timer(0.05, controller.cancel)
    timer.start()
    try:
        result = run_graph(
            _build().compile(),
            user_input="write f",
            expected="a function",
            runs_root=tmp_runs_root,
            cancellation=controller,
        )
    finally:
        timer.cancel()
    assert result.status == "cancelled"
    assert result.error == "user_cancelled"
    assert result.final_state.get("coder_output", "") == ""


def test_approval_node_halts_with_pending_artifact(tmp_runs_root):
    b = GraphBuilder(name="approval_test", cost_budget_usd=1.0, latency_budget_ms=60_000)
    b.add_node(
        ApprovalNodeConfig(
            id="review",
            prompt="Review before continuing.",
            approved_target=END,
            rejected_target=END,
        )
    )
    b.set_entry("review")

    result = run_graph(b.compile(), user_input="needs review", runs_root=tmp_runs_root)

    assert result.status == "pending_approval"
    assert result.error is None
    approval_path = result.run_dir / "approval.json"
    assert approval_path.exists()
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    assert approval["workflow"] == "approval_test"
    assert approval["run_id"] == result.run_id
    assert approval["node_id"] == "review"
    assert approval["prompt"] == "Review before continuing."
    assert approval["state_snapshot"]["user_input"] == "needs review"
    assert result.final_state["pending_approval"]["node_id"] == "review"


def test_subgraph_node_executes_child_run_and_writes_lineage(monkeypatch, tmp_runs_root):
    replies = [
        LLMResponse(text="search query", usage=Usage(10, 2), model="gpt-4o-mini"),
        LLMResponse(text="relevant evidence", usage=Usage(10, 4), model="gpt-4o-mini"),
        LLMResponse(text="grounded answer", usage=Usage(10, 5), model="gpt-4o-mini"),
    ]
    monkeypatch.setattr(oai, "stream_openai", _Replies(replies))
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    metadata = graph_spec_to_metadata(load_graph_spec("rag_subgraph_wrapper"))
    result = run_graph(metadata, user_input="What is in the corpus?", runs_root=tmp_runs_root)

    assert result.status == "ok"
    assert result.final_state["rag_answer"] == "grounded answer"
    assert result.cost_usd > 0

    lineage_files = list((result.run_dir / "subgraphs").glob("rag_child_*.json"))
    assert len(lineage_files) == 1
    lineage = json.loads(lineage_files[0].read_text(encoding="utf-8"))
    assert lineage["parent_run_id"] == result.run_id
    assert lineage["child_workflow"] == "linear_rag"
    assert lineage["status"] == "ok"
    assert lineage["outputs"] == {"final_answer": "rag_answer"}
    assert (resolve_run_dir(tmp_runs_root, lineage["child_run_id"]) / "parent_run.json").exists()


def test_nested_subgraph_pauses_parent_with_lineage(monkeypatch, tmp_runs_root):
    # approval_review's draft node calls an LLM; after that it hits the approval node.
    replies = [
        LLMResponse(text="Draft answer for review.", usage=Usage(5, 5), model="gpt-4o-mini"),
    ]
    monkeypatch.setattr(oai, "stream_openai", _Replies(replies))
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    metadata = graph_spec_to_metadata(load_graph_spec("approval_subgraph_wrapper"))
    result = run_graph(metadata, user_input="Write something for review.", runs_root=tmp_runs_root)

    assert result.status == "pending_approval"
    assert result.error is None

    # Parent has pending_subgraph_approval.json but no approval.json of its own.
    assert not (result.run_dir / "approval.json").exists()
    pending_path = result.run_dir / "pending_subgraph_approval.json"
    assert pending_path.exists()
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    assert pending["parent_run_id"] == result.run_id
    assert pending["node_id"] == "review_child"
    assert pending["child_workflow"] == "approval_review"

    # Child pending run has both approval.json and parent_run.json.
    child_run_id = pending["child_run_id"]
    child_run_dir = resolve_run_dir(tmp_runs_root, child_run_id)
    assert (child_run_dir / "approval.json").exists()
    parent_link = json.loads((child_run_dir / "parent_run.json").read_text(encoding="utf-8"))
    assert parent_link["parent_run_id"] == result.run_id
    assert parent_link["node_id"] == "review_child"


def test_subgraph_resume_marker_passthrough(monkeypatch, tmp_runs_root):
    from backend.runtime.nodes.subgraph import make_subgraph_node
    from backend.builder.nodes import SubgraphNodeConfig

    cfg = SubgraphNodeConfig(
        id="test_child",
        workflow="approval_review",
        inputs={"user_input": "user_input"},
        outputs={"final_answer": "result_key"},
    )

    sentinel = object()
    call_count = {"n": 0}

    def _fail_cost(cost: float) -> None:
        call_count["n"] += 1

    node_fn = make_subgraph_node(
        cfg,
        run_id="parent_run_test",
        graph_name="test_workflow",
        run_dir=tmp_runs_root,
        runs_root=tmp_runs_root,
        on_cost=_fail_cost,
    )

    # State contains the resume marker — child execution should be skipped.
    state = {
        "user_input": "ignored",
        "_subgraph_resume": {
            "test_child": {
                "child_run_id": "child_continuation_xyz",
                "outputs": {"result_key": "mapped_output_value"},
            }
        },
    }
    output = node_fn(state)  # type: ignore[arg-type]

    # No child execution, so on_cost should not have been called.
    assert call_count["n"] == 0
    assert output["result_key"] == "mapped_output_value"
    # Marker for this node should be cleared.
    assert "test_child" not in output.get("_subgraph_resume", {})
