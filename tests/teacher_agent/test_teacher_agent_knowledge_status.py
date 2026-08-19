"""Agent 状态语义：业务澄清 → needs_clarification，backend 异常 → failed。

不仅测 Tool Result，还要断言：
  - TeacherAgentResult.status
  - teacher_agent_run_trace.result_status
  - trace.error_code / error_type / error_stage 是否有值（业务澄清必须为空）
"""

import json

from sqlalchemy import select

from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.conversation_state import DatabasePendingReplacementStore, PendingGeneration
from calculus_agent.agent.schemas import GeneratePaperInput
from calculus_agent.db import build_session_factory
from tests.integration_db import configured_integration_db_path
from calculus_agent.models import CurriculumNode, KnowledgeNode, TeacherAgentRunTrace


class SequenceBackend:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, tools):
        self.calls.append((messages, tools))
        return self.responses.pop(0)


class BoomBackend:
    """Mock backend that always raises; used to verify hard-exception semantics."""

    def complete(self, messages, tools):
        raise RuntimeError("simulated backend failure")


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


def _seed(session) -> None:
    """第三章 + 第一章 + 一个 orphan 节点，用于业务澄清场景。"""
    chapter3 = CurriculumNode(id="ch3", node_type="chapter", code="三", title="微分中值定理与导数的应用", sort_order=1)
    section3 = CurriculumNode(id="sec3", node_type="section", title="导数与微分", parent_id="ch3", sort_order=1)
    chapter1 = CurriculumNode(id="ch1", node_type="chapter", code="一", title="函数与极限", sort_order=2)
    section1 = CurriculumNode(id="sec1", node_type="section", title="极限运算法则", parent_id="ch1", sort_order=1)
    session.add_all([chapter3, section3, chapter1, section1])
    session.add_all([
        KnowledgeNode(id="kn-deriv", node_type="concept", name="导数定义",
                      normalized_name="导数定义", curriculum_node_id="sec3"),
        KnowledgeNode(id="kn-law", node_type="concept", name="极限运算法则",
                      normalized_name="极限运算法则", curriculum_node_id="sec1"),
        KnowledgeNode(id="kn-parent", node_type="knowledge_point", name="函数极限",
                      normalized_name="函数极限", curriculum_node_id=None),
    ])
    session.flush()


def _preview_call(scope_names, knowledge_preferences, paper_type="chapter_exercise"):
    return _tool_call(
        "prepare_generation_plan",
        {
            "paper_type": paper_type,
            "scope_names": scope_names,
            "knowledge_preferences": knowledge_preferences,
        },
    )


# ---------------------------------------------------------------------------
# Agent Case 1: scope conflict → needs_clarification, NOT failed
# ---------------------------------------------------------------------------
def test_agent_scope_conflict_yields_needs_clarification(session):
    _seed(session)
    backend = SequenceBackend(
        _preview_call(["第三章"], ["极限运算法则"]),
        _final("课程不一致，请确认章节范围。"),
    )
    result = run_teacher_agent(
        session,
        "第三章 重点覆盖：极限运算法则",
        conversation_id="agent-scope-conflict",
        backend=backend,
    )

    # 1) Tool level: blocking_errors has knowledge_scope_conflict
    trace = session.scalar(select(TeacherAgentRunTrace).where(
        TeacherAgentRunTrace.conversation_id == "agent-scope-conflict"
    ))
    tool_result = trace.tool_calls_json[0]["result"]
    assert tool_result["ok"] is False
    assert "knowledge_scope_conflict" in tool_result.get("blocking_errors", [])
    assert any("极限运算法则" in q for q in tool_result.get("clarification_questions", []))

    # 2) Agent level: status MUST be needs_clarification, NEVER failed
    assert result.status == "needs_clarification", result.status
    assert result.status != "failed"

    # 3) Trace: result_status follows Agent; error_* fields MUST be empty
    #    (business clarification is not an execution exception).
    assert trace.result_status == "needs_clarification"
    assert trace.error_code is None
    assert trace.error_type is None
    assert trace.error_message is None
    assert trace.error_stage is None


# ---------------------------------------------------------------------------
# Agent Case 2: unknown knowledge → needs_clarification, NOT failed
# ---------------------------------------------------------------------------
def test_agent_unknown_knowledge_yields_needs_clarification(session):
    _seed(session)
    backend = SequenceBackend(
        _preview_call(["第三章"], ["量子力学"]),
        _final("未识别该知识点。"),
    )
    result = run_teacher_agent(
        session,
        "第三章 重点覆盖：量子力学",
        conversation_id="agent-unknown",
        backend=backend,
    )

    trace = session.scalar(select(TeacherAgentRunTrace).where(
        TeacherAgentRunTrace.conversation_id == "agent-unknown"
    ))
    assert "knowledge_unknown" in trace.tool_calls_json[0]["result"].get("blocking_errors", [])

    assert result.status == "needs_clarification"
    assert result.status != "failed"
    assert trace.result_status == "needs_clarification"
    assert trace.error_code is None
    assert trace.error_type is None
    assert trace.error_stage is None


# ---------------------------------------------------------------------------
# Agent Case 3: orphan node → knowledge_scope_uncertain → needs_clarification
# ---------------------------------------------------------------------------
def test_agent_orphan_knowledge_yields_needs_clarification(session):
    _seed(session)
    backend = SequenceBackend(
        _preview_call(["第三章"], ["函数极限"]),
        _final("该知识点章节归属无法确定。"),
    )
    result = run_teacher_agent(
        session,
        "第三章 重点覆盖：函数极限",
        conversation_id="agent-orphan",
        backend=backend,
    )

    trace = session.scalar(select(TeacherAgentRunTrace).where(
        TeacherAgentRunTrace.conversation_id == "agent-orphan"
    ))
    assert "knowledge_scope_uncertain" in trace.tool_calls_json[0]["result"].get("blocking_errors", [])

    assert result.status == "needs_clarification"
    assert result.status != "failed"
    assert trace.result_status == "needs_clarification"
    assert trace.error_type is None
    assert trace.error_stage is None


# ---------------------------------------------------------------------------
# Agent Case 4: 原始 bad case 直连真实 calculus_agent.db
# ---------------------------------------------------------------------------
def test_agent_real_bad_case_yields_needs_clarification():
    real_db = configured_integration_db_path()
    session = build_session_factory(f"sqlite:///{real_db}")()
    backend = SequenceBackend(
        _preview_call(["第三章"], ["函数极限", "极限运算法则", "无穷小"]),
        _final("需要补充章节与知识点。"),
    )
    result = run_teacher_agent(
        session,
        "第三章 重点覆盖：函数极限、极限运算法则、无穷小",
        conversation_id="agent-real-bad-case",
        backend=backend,
    )

    trace = session.scalar(select(TeacherAgentRunTrace).where(
        TeacherAgentRunTrace.conversation_id == "agent-real-bad-case"
    ))
    assert trace is not None
    tool_result = trace.tool_calls_json[0]["result"]
    errors = set(tool_result.get("blocking_errors", []))
    assert "knowledge_scope_uncertain" in errors
    assert "knowledge_scope_conflict" in errors
    assert "knowledge_unknown" in errors
    assert "knowledge_ambiguous" not in errors

    # Agent 必须是 needs_clarification，绝不能是 failed
    assert result.status == "needs_clarification"
    assert result.status != "failed"
    assert trace.result_status == "needs_clarification"
    assert trace.error_type is None
    assert trace.error_stage is None


# ---------------------------------------------------------------------------
# Agent Case 5: backend 异常 + 已存在的 pending_generation → 必须仍是 failed
# ---------------------------------------------------------------------------
def test_agent_backend_exception_with_pending_stays_failed(session):
    """回归上阶段的修复：硬异常即使有 pending 也不能被覆盖为 waiting_confirmation。"""
    _seed(session)
    # 预先写入 pending_generation（业务澄清不会走这条路径，所以这是真正硬异常的唯一测试）
    store = DatabasePendingReplacementStore(session)
    pending_input = GeneratePaperInput(
        paper_type="chapter_exercise",
        scope_names=["第三章"],
    )
    # reuse PendingGeneration; we need access to the store's set_generation
    # PendingReplacementStore uses set_generation via store interface
    store.set_generation("agent-boom-pending", PendingGeneration(request=pending_input))
    session.flush()

    result = run_teacher_agent(
        session,
        "继续第三章",
        conversation_id="agent-boom-pending",
        backend=BoomBackend(),
    )

    trace = session.scalar(select(TeacherAgentRunTrace).where(
        TeacherAgentRunTrace.conversation_id == "agent-boom-pending"
    ))
    # Agent 必须是 failed，不能因为 pending 存在变成 waiting_confirmation
    assert result.status == "failed", result.status
    assert trace.result_status == "failed"
    assert trace.error_type == "RuntimeError"
    assert trace.error_code == "agent_execution_failed"
    assert trace.error_stage in {"llm_call", "response_parse", "tool_arguments_parse", "tool_execution"}
