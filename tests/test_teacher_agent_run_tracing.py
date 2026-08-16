"""Deterministic tests for Run-Level Tracing of the Teacher Agent.

These tests pin down the invariants required by the Run-Level Tracing spec:

* Every ``/teacher-agent/run`` request gets exactly one ``run_id`` -- including
  the fast-fail paths (model unavailable) that previously left NO trace.
* The local DB trace (``TeacherAgentRunTrace`` + ``TeacherAgentSpan``) is the
  source of truth and is queryable by ``run_id`` / ``conversation_id``.
* A tool returning ``{"ok": false}`` is a **business failure**: the tool_call
  span stays ``status="success"`` and the failure lives in its output.
* A genuine exception is a **technical error**: the tool_call span is
  ``status="error"`` and the run carries an ``error`` payload.
* Every span links to its ``run_id`` (``parent_span_id`` points at a span in
  the same run); exactly one ``agent`` span exists per run.

The model / business logic is NOT modified -- only tracing is exercised.
"""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.api import get_session, router
from calculus_agent.models import (
    KnowledgeNode,
    Paper,
    PaperBlueprintRecord,
    PaperItem,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    QuestionProfile,
    TeacherAgentRunTrace,
    TeacherAgentSpan,
)


# ── local fixtures / helpers (mirror tests/test_teacher_agent_autonomous.py) ──


class SequenceBackend:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, tools):
        self.calls.append((messages, tools))
        return self.responses.pop(0)


def tool_call(name: str, arguments: dict | None = None, *, call_id: str = "call-1") -> dict:
    return {
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments or {}, ensure_ascii=False)},
            }],
        }
    }


def final(text: str) -> dict:
    return {"message": {"role": "assistant", "content": text}}


def _question(session, number: int, difficulty: int, knowledge_id: str) -> Question:
    draft = QuestionDraft(
        source_name="tracing",
        source_item_id=str(number),
        variant=1,
        subject="高数",
        question_type="计算题",
        question_text=f"追踪测试题干{number}",
        normalized_fingerprint=f"{number:064d}",
        status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        draft_id=draft.id,
        question_text=draft.question_text,
        question_type="计算题",
        verification_status="verified",
        review_status="approved",
    )
    session.add(question)
    session.flush()
    session.add_all([
        QuestionKnowledgeLink(
            question_id=question.id,
            knowledge_node_id=knowledge_id,
            relation_type="primary",
        ),
        QuestionProfile(
            question_id=question.id,
            profile_version=1,
            difficulty=difficulty,
            estimated_time_min=5,
            reasoning_depth=1,
            calculation_load=1,
            knowledge_depth=1,
            comprehensive_level=1,
            confidence=1,
            profile_source="human",
            profile_status="approved",
            reason="tracing test",
        ),
    ])
    return question


def _paper(session) -> Paper:
    node = KnowledgeNode(
        id="tracing-k",
        node_type="concept",
        name="函数极限",
        normalized_name="函数极限",
    )
    session.add(node)
    session.flush()
    target = _question(session, 1, 4, node.id)
    second = _question(session, 2, 3, node.id)
    third = _question(session, 3, 4, node.id)
    _question(session, 4, 2, node.id)
    record = PaperBlueprintRecord(
        id="tracing-bp",
        title="追踪测试卷",
        status="draft",
        blueprint_json={"_agent_metadata": {"scope_node_ids": [node.id]}},
    )
    paper = Paper(
        id="tracing-paper",
        blueprint_id=record.id,
        root_paper_id="tracing-paper",
        version=1,
        status="draft",
        title="追踪测试卷",
        total_score=30,
        validation_status="pending",
    )
    session.add_all([record, paper])
    session.flush()
    for position, question in enumerate([target, second, third], 1):
        session.add(PaperItem(
            paper_id=paper.id,
            question_id=question.id,
            section="计算题",
            position=position,
            score=10,
            locked=False,
        ))
    session.flush()
    return paper


# ── invariant helpers ──


def _get_run(session, run_id: str) -> TeacherAgentRunTrace:
    run = session.scalar(
        select(TeacherAgentRunTrace).where(TeacherAgentRunTrace.run_id == run_id)
    )
    assert run is not None, f"run {run_id} must be persisted"
    return run


def _get_spans(session, run_id: str) -> list[TeacherAgentSpan]:
    return list(
        session.scalars(
            select(TeacherAgentSpan)
            .where(TeacherAgentSpan.run_id == run_id)
            .order_by(TeacherAgentSpan.started_at)
        ).all()
    )


def assert_run_invariants(session, run_id: str, *, expect_status: str):
    """Core invariant: one run row, one agent span, all spans link to run_id."""
    run = _get_run(session, run_id)
    assert run.run_id == run_id
    assert run.status == expect_status
    spans = _get_spans(session, run_id)
    assert spans, "run must have at least one span"
    span_ids = {s.span_id for s in spans}
    agent_spans = [s for s in spans if s.span_type == "agent"]
    assert len(agent_spans) == 1, "exactly one agent span per run"
    for s in spans:
        assert s.run_id == run_id, "every span must link to its run_id"
        if s.span_type != "agent":
            assert s.parent_span_id in span_ids, (
                "non-root span must link to a parent in the same run"
            )
    return run, spans


# ── 1. Success (completed chat, no tools) ──


def test_success_completed_chat_has_run_id_and_spans(session):
    backend = SequenceBackend(final("你好，需要我帮你组卷、查看试卷，还是调整题目？"))
    result = run_teacher_agent(session, "你好", conversation_id="t-success", backend=backend)
    assert result.status == "completed"
    assert result.run_id is not None
    run, spans = assert_run_invariants(session, result.run_id, expect_status="completed")
    # No tools -> exactly one model_call, no tool_call spans.
    assert any(s.span_type == "model_call" for s in spans)
    assert not any(s.span_type == "tool_call" for s in spans)


# ── 2. Business Failed (tool returns ok=false, NOT an exception) ──


def test_business_failed_tool_call_is_success_span(session):
    # Model asks for a tool that does not exist -> business failure, status failed,
    # but the dispatch itself is a normal return (no exception).
    backend = SequenceBackend(
        tool_call("does_not_exist", {"x": 1}),
        final("抱歉，无法处理该请求。"),
    )
    result = run_teacher_agent(session, "帮我做点魔法", conversation_id="t-bizfail", backend=backend)
    assert result.status == "failed"
    assert result.run_id is not None
    run, spans = assert_run_invariants(session, result.run_id, expect_status="failed")
    tool_spans = [s for s in spans if s.span_type == "tool_call"]
    assert tool_spans, "expected a tool_call span for the unknown tool"
    for s in tool_spans:
        # KEY: business failure -> span stays success; failure lives in output.
        assert s.status == "success"
        assert s.output_json is not None
        assert s.output_json.get("ok") is False
        assert s.output_json.get("code") == "unknown_tool"


# ── 3. Needs Clarification (business signal, no tool exception) ──


def test_needs_clarification_is_traced(session):
    paper = _paper(session)
    backend = SequenceBackend(
        final("第2题是我猜的极限题。"),
        final(json.dumps({"paper_observation_required": True, "answer": ""}, ensure_ascii=False)),
        final("第2题仍然是我猜的极限题。"),
        final("第2题仍然是我猜的极限题。"),
    )
    result = run_teacher_agent(
        session, "第2题是什么？", conversation_id="t-needs-clar",
        paper_id=paper.id, version_id=paper.id, backend=backend,
    )
    assert result.status == "needs_clarification"
    assert result.run_id is not None
    run, spans = assert_run_invariants(session, result.run_id, expect_status="needs_clarification")
    assert any(s.span_type == "model_call" for s in spans)


# ── 4. Model Unavailable (fast-fail early return -- previously UNTRACED) ──


def test_model_unavailable_early_return_still_traced(session):
    # backend=None hits the early-return failure path BEFORE the old trace row
    # was ever created. Run-Level Tracing must still persist a queryable run.
    result = run_teacher_agent(session, "你好", conversation_id="t-model-down", backend=None)
    assert result.status == "failed"
    assert result.run_id is not None
    run = _get_run(session, result.run_id)
    assert run.status == "failed"
    # Business failure (model unavailable) is captured via status + final_response,
    # NOT the technical error_* columns (those are for genuine exceptions only).
    assert "不可用" in (run.final_response or "")
    assert run.error_code is None
    assert run.conversation_id == "t-model-down"
    spans = _get_spans(session, result.run_id)
    # Only the root agent span is created before the early return.
    assert [s.span_type for s in spans] == ["agent"]
    assert spans[0].status == "success"  # business failure, not a span error


# ── 5. Tool Exception (genuine technical error -> span error) ──


def test_tool_exception_records_error_span(session, monkeypatch):
    def boom(tool, arguments):  # signature matches execute_tool(tool, arguments)
        raise RuntimeError("kaboom-db")

    monkeypatch.setattr("calculus_agent.agent.agent.execute_tool", boom)
    paper = _paper(session)
    backend = SequenceBackend(
        tool_call("read_current_paper", {"positions": [3]}),
        final("done"),
    )
    result = run_teacher_agent(
        session, "第3题是什么？", conversation_id="t-toolexc",
        paper_id=paper.id, version_id=paper.id, backend=backend,
    )
    assert result.status == "failed"
    assert result.run_id is not None
    run, spans = assert_run_invariants(session, result.run_id, expect_status="failed")
    # turn_error must be captured (genuine exception -> technical error).
    assert run.error_code == "agent_execution_failed"
    tool_spans = [s for s in spans if s.span_type == "tool_call"]
    assert tool_spans, "expected a tool_call span"
    # KEY: genuine exception -> span status error (vs business ok=false -> success).
    assert all(s.status == "error" for s in tool_spans)
    # The agent span must be error because a genuine exception occurred.
    agent = [s for s in spans if s.span_type == "agent"][0]
    assert agent.status == "error"


# ── 6. Multi-turn, same conversation -> distinct run_ids, all linked ──


def test_multi_turn_same_conversation_distinct_run_ids(session):
    r1 = run_teacher_agent(
        session, "你好", conversation_id="t-multi",
        backend=SequenceBackend(final("你好，我能帮你组卷、查看或调整试卷。")),
    )
    r2 = run_teacher_agent(
        session, "再见", conversation_id="t-multi",
        backend=SequenceBackend(final("再见，有需要随时找我。")),
    )
    assert r1.run_id is not None and r2.run_id is not None
    assert r1.run_id != r2.run_id  # exactly one run_id per request

    rows = session.scalars(
        select(TeacherAgentRunTrace).where(TeacherAgentRunTrace.conversation_id == "t-multi")
    ).all()
    assert {row.run_id for row in rows} == {r1.run_id, r2.run_id}
    # Both runs are independently queryable and well-formed.
    assert_run_invariants(session, r1.run_id, expect_status="completed")
    assert_run_invariants(session, r2.run_id, expect_status="completed")


# ── 7. Full span tree for a tool-using success (agent -> model -> tool -> state) ──


def test_tool_using_turn_produces_full_span_tree(session):
    paper = _paper(session)
    backend = SequenceBackend(
        tool_call("read_current_paper", {"positions": [3]}, call_id="read-3"),
        tool_call(
            "preview_replace_question",
            {"position": 3, "difficulty_direction": "easier"},
            call_id="replace-3",
        ),
        final("已找到第3题更简单的替代题，请确认。"),
    )
    result = run_teacher_agent(
        session, "第3题太难了，换简单一点", conversation_id="t-tree",
        paper_id=paper.id, version_id=paper.id, backend=backend,
    )
    assert result.status == "waiting_confirmation"
    assert result.run_id is not None
    run, spans = assert_run_invariants(session, result.run_id, expect_status="waiting_confirmation")

    types = [s.span_type for s in spans]
    assert types.count("model_call") >= 1
    assert types.count("tool_call") == 2  # read_current_paper + preview_replace_question
    # A pending action was created -> working memory changed -> state_transition.
    assert types.count("state_transition") >= 1

    tool_spans = [s for s in spans if s.span_type == "tool_call"]
    for s in tool_spans:
        assert s.status == "success"  # business success path
        assert s.parent_span_id is not None  # linked under the run

    # The state_transition span must hang off a tool_call span in the same run.
    tool_ids = {s.span_id for s in tool_spans}
    for s in spans:
        if s.span_type == "state_transition":
            assert s.parent_span_id in tool_ids


# ── 8. GET endpoints are queryable (local trace is source of truth) ──


def _client(session) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


def test_get_run_by_run_id_and_list_by_conversation(session):
    paper = _paper(session)
    r1 = run_teacher_agent(
        session, "第3题是什么？", conversation_id="t-get",
        paper_id=paper.id, version_id=paper.id,
        backend=SequenceBackend(
            tool_call("read_current_paper", {"positions": [3]}),
            final("第3题是“追踪测试题干3”。"),
        ),
    )
    r2 = run_teacher_agent(session, "你好", conversation_id="t-get", backend=SequenceBackend(final("你好")))

    client = _client(session)
    resp = client.get(f"/api/v1/teacher-agent/runs/{r1.run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == r1.run_id
    assert body["conversation_id"] == "t-get"
    assert body["status"] == "completed"
    assert any(s["span_type"] == "tool_call" for s in body["spans"])

    # 404 for an unknown run_id.
    missing = client.get("/api/v1/teacher-agent/runs/does-not-exist")
    assert missing.status_code == 404

    # List by conversation returns both runs, oldest first.
    listing = client.get("/api/v1/teacher-agent/runs", params={"conversation_id": "t-get"})
    assert listing.status_code == 200
    ids = [item["run_id"] for item in listing.json()]
    assert ids == [r1.run_id, r2.run_id]
