import json

from sqlalchemy import select

from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.conversation_state import DatabasePendingReplacementStore
from calculus_agent.models import TeacherAgentRunTrace


def _tool(name, arguments):
    return {"message": {"tool_calls": [{"id": "draft", "function": {"name": name, "arguments": arguments}}]}}


def _text(value):
    return {"message": {"content": value}}


class Backend:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def complete(self, messages, tools):
        self.requests.append((messages, tools))
        if not self.responses:
            raise AssertionError("unexpected second LLM round")
        return self.responses.pop(0)


def test_teaching_planning_creates_conversation_draft(session):
    draft = {
        "problem_analysis": "学生混淆极限运算法则和无穷小等价替换。",
        "learning_objectives": ["理解极限意义"],
        "knowledge_focus": ["无穷小", "极限运算法则"],
        "teaching_strategy": ["用反例辨析"],
        "assessment_strategy": ["用短题诊断"],
    }
    backend = Backend(
        _tool(
            "prepare_teaching_planning_draft",
            json.dumps({"topic": "极限", "draft": json.dumps(draft, ensure_ascii=False)}, ensure_ascii=False),
        ),
        _text("已形成教学规划草稿。"),
    )
    result = run_teacher_agent(session, "学生极限不好，帮我设计复习方案。", conversation_id="planning-draft", backend=backend)

    assert result.teaching_planning_draft is not None
    assert result.teaching_planning_draft.knowledge_focus == ["无穷小", "极限运算法则"]
    assert len(result.blocking_errors) == 0
    trace = session.scalar(select(TeacherAgentRunTrace).where(
        TeacherAgentRunTrace.conversation_id == "planning-draft"
    ))
    assert [item["tool_name"] for item in trace.tool_calls_json] == [
        "prepare_teaching_planning_draft"
    ]
    assert len(backend.requests) == 1
    memory = DatabasePendingReplacementStore(session).get_memory("planning-draft")
    assert memory.active_task["type"] == "teaching_planning"
    assert memory.active_task["waiting_for_scope"] is True


def test_completed_scope_draft_does_not_ask_for_scope_again(session):
    draft = {
        "problem_analysis": "学生需要巩固极限运算。",
        "learning_objectives": ["掌握极限运算"],
        "knowledge_focus": ["极限"],
        "teaching_strategy": ["分步复习"],
        "assessment_strategy": ["课堂诊断"],
    }
    backend = Backend(_tool(
        "prepare_teaching_planning_draft",
        json.dumps({
            "topic": "极限",
            "waiting_for_scope": False,
            "draft": draft,
        }, ensure_ascii=False),
    ))

    result = run_teacher_agent(
        session,
        "学生极限不好，帮我设计复习方案。",
        conversation_id="planning-scope-complete",
        backend=backend,
    )

    assert result.status == "completed"
    assert result.teaching_planning_draft is not None
    assert "继续补充教材章节范围" not in result.message
    assert "已保留当前确认的教材范围" in result.message


def test_explicit_scope_continues_existing_teaching_planning_draft(session):
    store = DatabasePendingReplacementStore(session)
    memory = store.get_memory("planning-continuation")
    memory.active_task = {"type": "teaching_planning", "status": "awaiting_scope", "waiting_for_scope": True, "draft": {}}
    store.set_memory("planning-continuation", memory)
    backend = Backend(_text("已收到第一章范围，将继续完善方案。"))

    run_teacher_agent(session, "高数上第一章，基础一点，题型随意。", conversation_id="planning-continuation", backend=backend)

    names = {item["function"]["name"] for item in backend.requests[0][1]}
    assert {"inspect_curriculum", "inspect_question_bank", "create_teaching_design"}.issubset(names)
    assert "当前任务模式：TEACHING_PLANNING" in "\n".join(item.get("content", "") for item in backend.requests[0][0] if item["role"] == "system")
