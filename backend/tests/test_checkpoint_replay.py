"""Checkpoint replay with forked snapshot migration."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.checkpointing.replay import (
    apply_overrides,
    migrate_snapshot_state,
    parse_set_arg,
    replay as do_replay,
    resolve_replay_boundary,
)
from backend.providers import openrouter as orouter
from backend.providers.base import LLMResponse, Usage
from backend.runtime.errors import ReplayError
from backend.runtime.executor import run_graph
from backend.workflows import coder_tester


class _Replies:
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = 0

    def __call__(self, **kwargs) -> LLMResponse:
        self.calls += 1
        if not self._replies:
            raise AssertionError("unexpected provider call during replay test")
        return self._replies.pop(0)


def test_parse_set_arg():
    out = parse_set_arg(["coder.temperature=0.5", "tester.max_retries=1", "coder.model=foo"])
    assert out == {
        "coder": {"temperature": 0.5, "model": "foo"},
        "tester": {"max_retries": 1},
    }


def test_parse_set_arg_coerces_types():
    out = parse_set_arg(["a.b=true", "a.c=42", "a.d=3.14", "a.e=hello"])
    assert out == {"a": {"b": True, "c": 42, "d": 3.14, "e": "hello"}}


def test_apply_overrides_updates_config():
    b = coder_tester.build()
    original_prompt = b._nodes["coder"].user_prompt_template
    apply_overrides(b, {"coder": {"user_prompt_template": "NEW"}})
    assert b._nodes["coder"].user_prompt_template == "NEW"
    assert b._nodes["coder"].user_prompt_template != original_prompt


def test_resolve_replay_boundary_picks_latest_matching_node(monkeypatch, tmp_path: Path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()

    replies = _Replies(
        [
            LLMResponse(text="plan", usage=Usage(1, 1), model="m"),
            LLMResponse(text="code v1", usage=Usage(1, 1), model="m"),
            LLMResponse(text="FAIL\ntry again", usage=Usage(1, 1), model="m"),
            LLMResponse(text="code v2", usage=Usage(1, 1), model="m"),
            LLMResponse(text="PASS", usage=Usage(1, 1), model="m"),
        ]
    )
    monkeypatch.setattr(orouter, "stream_openrouter", replies)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    source = run_graph(
        coder_tester.build_compiled(),
        user_input="write f",
        expected="a function",
        runs_root=runs_root,
    )
    boundary = resolve_replay_boundary(
        coder_tester.build_compiled(),
        source_run_id=source.run_id,
        at="coder",
        runs_root=runs_root,
    )
    assert boundary.replay_from_node == "coder"
    assert boundary.snapshot_state["tester_verdict"] is False
    assert boundary.snapshot_state["tester_feedback"] == "try again"


def test_replay_forks_new_run_and_preserves_source_dir(monkeypatch, tmp_path: Path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()

    first = _Replies(
        [
            LLMResponse(text="plan", usage=Usage(1, 1), model="m"),
            LLMResponse(text="code v1", usage=Usage(1, 1), model="m"),
            LLMResponse(text="PASS", usage=Usage(1, 1), model="m"),
        ]
    )
    monkeypatch.setattr(orouter, "stream_openrouter", first)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    source = run_graph(
        coder_tester.build_compiled(),
        user_input="write fizzbuzz",
        expected="python code",
        runs_root=runs_root,
    )
    source_files_before = sorted(p.name for p in source.run_dir.iterdir())

    second = _Replies(
        [
            LLMResponse(text="plan replay", usage=Usage(1, 1), model="m"),
            LLMResponse(text="code replay", usage=Usage(1, 1), model="m"),
            LLMResponse(text="PASS", usage=Usage(1, 1), model="m"),
        ]
    )
    monkeypatch.setattr(orouter, "stream_openrouter", second)

    replayed = do_replay(
        workflow="coder_tester",
        run_id=source.run_id,
        runs_root=runs_root,
    )
    assert replayed.source_run_id == source.run_id
    assert replayed.replay_run_id != source.run_id
    assert replayed.run_dir != source.run_dir
    assert replayed.final_state["plan"] == "plan replay"
    assert replayed.final_state["coder_output"] == "code replay"
    assert sorted(p.name for p in source.run_dir.iterdir()) == source_files_before
    assert not (source.run_dir / "replay.json").exists()
    lineage = json.loads((replayed.run_dir / "replay.json").read_text(encoding="utf-8"))
    assert lineage["source_run_id"] == source.run_id
    assert lineage["replay_run_id"] == replayed.replay_run_id


def test_replay_from_mid_graph_node_runs_only_downstream_nodes(monkeypatch, tmp_path: Path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()

    source_replies = _Replies(
        [
            LLMResponse(text="source plan", usage=Usage(1, 1), model="m"),
            LLMResponse(text="source code", usage=Usage(1, 1), model="m"),
            LLMResponse(text="PASS", usage=Usage(1, 1), model="m"),
        ]
    )
    monkeypatch.setattr(orouter, "stream_openrouter", source_replies)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    source = run_graph(
        coder_tester.build_compiled(),
        user_input="write add",
        expected="python code",
        runs_root=runs_root,
    )

    replay_replies = _Replies(
        [
            LLMResponse(text="replayed code", usage=Usage(1, 1), model="m"),
            LLMResponse(text="PASS", usage=Usage(1, 1), model="m"),
        ]
    )
    monkeypatch.setattr(orouter, "stream_openrouter", replay_replies)

    replayed = do_replay(
        workflow="coder_tester",
        run_id=source.run_id,
        at="coder",
        runs_root=runs_root,
    )
    assert replayed.replay_from_node == "coder"
    assert replayed.final_state["plan"] == "source plan"
    assert replayed.final_state["coder_output"] == "replayed code"
    assert replay_replies.calls == 2


def test_migrate_snapshot_state_preserves_legacy_keys():
    class _Workflow:
        pass

    migrated = migrate_snapshot_state(
        {"user_input": "q", "coder_output": "print(1)", "legacy_only": "old"},
        workflow_module=_Workflow(),
        source_run_id="run_old",
        replay_from_node="coder",
        user_input="q",
        user_input_locked=False,
    )
    assert migrated["coder_output"] == "print(1)"
    assert migrated["plan"] == ""
    assert migrated["artifacts"]["_legacy_state"]["legacy_only"] == "old"


def test_migrate_snapshot_state_allows_workflow_hook_for_renames():
    class _Workflow:
        @staticmethod
        def migrate_replay_state(snapshot_state, *, source_run_id, replay_from_node):
            return {"final_answer": snapshot_state["legacy_answer"]}

    migrated = migrate_snapshot_state(
        {"user_input": "q", "legacy_answer": "42"},
        workflow_module=_Workflow(),
        source_run_id="run_old",
        replay_from_node="synthesiser",
        user_input="q",
        user_input_locked=False,
    )
    assert migrated["final_answer"] == "42"
    assert migrated["artifacts"]["_legacy_state"]["legacy_answer"] == "42"


def test_replay_invalid_node_fails_cleanly(monkeypatch, tmp_path: Path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()

    replies = _Replies(
        [
            LLMResponse(text="plan", usage=Usage(1, 1), model="m"),
            LLMResponse(text="code", usage=Usage(1, 1), model="m"),
            LLMResponse(text="PASS", usage=Usage(1, 1), model="m"),
        ]
    )
    monkeypatch.setattr(orouter, "stream_openrouter", replies)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    source = run_graph(
        coder_tester.build_compiled(),
        user_input="write f",
        expected="a function",
        runs_root=runs_root,
    )
    with pytest.raises(ReplayError, match="does not exist"):
        do_replay(
            workflow="coder_tester",
            run_id=source.run_id,
            at="missing_node",
            runs_root=runs_root,
        )
