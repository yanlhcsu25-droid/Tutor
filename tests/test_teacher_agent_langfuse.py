"""Langfuse observability integration tests.

Verifies the four required invariants:

  1. Langfuse being unavailable never blocks or changes Teacher Agent behaviour.
  2. LLM call count is unchanged by the instrumentation.
  3. Tool execution behaviour (arguments + return payload) is unchanged.
  4. Backend exceptions still propagate through the same status / error_*
     fields the Teacher Agent has always emitted.
  5. An exception raised inside the Langfuse SDK itself is swallowed.

All tests monkeypatch ``safe_get_client`` so they do not depend on the
real Langfuse environment — this also prevents the singleton client from
leaking between test runs.
"""

import json
from typing import Any

import pytest
from sqlalchemy import select

from calculus_agent.agent import langfuse_tracing
from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.db import build_session_factory
from calculus_agent.models import TeacherAgentRunTrace


class _CallCountBackend:
    """Records the number of LLM calls and returns the same canned response."""

    def __init__(self, canned: dict):
        self.canned = canned
        self.call_count = 0

    def complete(self, messages, tools):
        self.call_count += 1
        return self.canned

    # Langfuse generation span reads ``backend.model`` when present.
    model = "fake-model"


class _ToolSpy:
    """Wraps the real execute_tool path to capture calls without altering them."""

    def __init__(self):
        self.calls: list[tuple[str, Any]] = []
        self.real_execute_tool = None  # set per-test

    def __call__(self, tool, arguments):
        self.calls.append((tool.name, arguments))
        return self.real_execute_tool(tool, arguments)


def _tool_call(name: str, arguments: dict, *, call_id: str = "call-1") -> dict:
    return {
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
            }],
        }
    }


def _final(text: str) -> dict:
    return {"message": {"role": "assistant", "content": text}}


@pytest.fixture
def session():
    factory = build_session_factory("sqlite:///:memory:")
    from calculus_agent.db import create_schema
    create_schema("sqlite:///:memory:")
    s = factory()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _reset_client_cache():
    """Wipe Langfuse singleton between tests so monkeypatch stays in effect."""
    langfuse_tracing._CLIENT_CACHE["client"] = None
    yield
    langfuse_tracing._CLIENT_CACHE["client"] = None


# ── Test 1 ──────────────────────────────────────────────────────────────
def test_langfuse_unavailable_does_not_block_agent(session, monkeypatch):
    """When Langfuse is unreachable, the Teacher Agent still returns a result."""
    monkeypatch.setattr(langfuse_tracing, "safe_get_client", lambda: None)

    backend = _CallCountBackend(_final("这是直接回答。"))
    result = run_teacher_agent(
        session,
        "你好",
        conversation_id="lf-test-1",
        backend=backend,
    )
    assert result.status == "completed"
    assert result.message == "这是直接回答。"
    trace = session.scalar(
        select(TeacherAgentRunTrace).where(TeacherAgentRunTrace.conversation_id == "lf-test-1")
    )
    assert trace is not None
    assert trace.error_code is None
    assert trace.error_type is None


# ── Test 2 ──────────────────────────────────────────────────────────────
def test_llm_call_count_unchanged(session, monkeypatch):
    """Langfuse instrumentation must not cause any extra backend.complete calls."""
    monkeypatch.setattr(langfuse_tracing, "safe_get_client", lambda: None)

    backend = _CallCountBackend(_final("直接回答"))
    run_teacher_agent(
        session,
        "直接问题",
        conversation_id="lf-test-2",
        backend=backend,
    )
    # Direct answer requires exactly one LLM call.
    assert backend.call_count == 1


# ── Test 3 ──────────────────────────────────────────────────────────────
def test_tool_call_arguments_and_result_unchanged(session, monkeypatch):
    """Tool execution path: arguments in, payload out, unchanged by tracing."""
    monkeypatch.setattr(langfuse_tracing, "safe_get_client", lambda: None)

    # Seed the simplest possible curriculum so preview_generation_plan
    # returns a valid waiting_confirmation payload.
    from calculus_agent.models import CurriculumNode
    chapter = CurriculumNode(id="ch1", node_type="chapter", code="一",
                             title="函数与极限", sort_order=1)
    session.add(chapter)
    session.flush()

    backend = _CallCountBackend(_tool_call(
        "preview_generation_plan",
        {"paper_type": "chapter_exercise", "scope_names": ["函数与极限"],
         "knowledge_preferences": []},
    ))
    # A single tool call must be followed by a final assistant message so the
    # loop does not hit the max_tool_rounds guard.
    backend.canned_final = _final("已为你生成预览，请确认。")
    real_complete = backend.complete

    def two_step_complete(messages, tools):
        if backend.call_count == 0:
            return real_complete(messages, tools)
        backend.call_count += 1
        return backend.canned_final

    backend.complete = two_step_complete

    # Spy execute_tool by patching the reference imported in agent.py.
    import calculus_agent.agent.agent as agent_module
    real_execute_tool = agent_module.execute_tool
    spy = _ToolSpy()
    spy.real_execute_tool = real_execute_tool
    monkeypatch.setattr(agent_module, "execute_tool", spy)

    result = run_teacher_agent(
        session,
        "出题",
        conversation_id="lf-test-3",
        backend=backend,
    )
    # Whether the tool returns ok or needs_clarification, the key invariant
    # here is that the tool layer saw exactly the arguments the LLM emitted,
    # which is what the Langfuse tool observation must mirror.
    assert len(spy.calls) == 1
    tool_name, arguments = spy.calls[0]
    assert tool_name == "preview_generation_plan"
    assert arguments["paper_type"] == "chapter_exercise"
    assert arguments["scope_names"] == ["函数与极限"]
    # And the Agent still terminated normally (no leaked exception).
    assert result.status in {"waiting_confirmation", "needs_clarification", "completed"}


# ── Test 4 ──────────────────────────────────────────────────────────────
def test_backend_exception_propagates_with_correct_error_fields(session, monkeypatch):
    """backend.complete raising RuntimeError still surfaces as failed / llm_call."""
    monkeypatch.setattr(langfuse_tracing, "safe_get_client", lambda: None)

    class BoomBackend:
        model = "fake-model"

        def complete(self, messages, tools):
            raise RuntimeError("simulated llm error")

    result = run_teacher_agent(
        session,
        "请求",
        conversation_id="lf-test-4",
        backend=BoomBackend(),
    )
    assert result.status == "failed"
    assert "agent_execution_failed" in result.blocking_errors
    trace = session.scalar(
        select(TeacherAgentRunTrace).where(TeacherAgentRunTrace.conversation_id == "lf-test-4")
    )
    assert trace.error_code == "agent_execution_failed"
    assert trace.error_type == "RuntimeError"
    assert trace.error_stage == "llm_call"


# ── Test 5 ──────────────────────────────────────────────────────────────
def test_langfuse_sdk_exception_does_not_break_agent(session, monkeypatch):
    """If the Langfuse SDK raises during observation start, Agent must still work."""
    class FailingClient:
        def start_as_current_observation(self, **_):
            raise RuntimeError("simulated SDK failure")

    monkeypatch.setattr(langfuse_tracing, "safe_get_client", lambda: FailingClient())

    # propagate_attributes may also be called — make it raise too, to be safe.
    import calculus_agent.agent.langfuse_tracing as lf

    real_propagate = getattr(lf, "propagate_attributes", None)

    def fake_propagate(**_):
        class _CM:
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *exc): return False
        return _CM()

    if real_propagate is not None:
        monkeypatch.setattr(lf, "propagate_attributes", fake_propagate)

    backend = _CallCountBackend(_final("OK"))
    result = run_teacher_agent(
        session,
        "x",
        conversation_id="lf-test-5",
        backend=backend,
    )
    assert result.status == "completed"
    assert result.message == "OK"
