from unittest.mock import Mock

import calculus_agent.api as api
from calculus_agent.agent.agent import TeacherAgentResult
from calculus_agent.agent.conversation_state import (
    DatabaseConversationHistoryStore,
    DatabasePendingReplacementStore,
    PendingGeneration,
)
from calculus_agent.agent.schemas import GeneratePaperInput, QuestionTypeRequirement
from calculus_agent.agent.state import WorkspaceService
from calculus_agent.agent.tools.paper_tools import GeneratePaperToolResult, PaperSummary
from calculus_agent.config import Settings
from calculus_agent.models import TeacherAgentRunTrace
from calculus_agent.schemas import AgentRunRequest
from calculus_agent.teaching_design import TeachingDesignContent, TeachingDesignService


def test_legacy_agent_run_endpoint_creates_uuid_scoped_conversation(monkeypatch, session):
    def fake_run(session_arg, message, **kwargs):
        conversation_id = kwargs["conversation_id"]
        assert conversation_id.startswith("legacy-api-")
        run = TeacherAgentRunTrace(
            run_id="legacy-run",
            conversation_id=conversation_id,
            user_message=message,
            status="completed",
            result_status="completed",
            final_response="已完成。",
            tool_calls_json=[],
        )
        session_arg.add(run)
        session_arg.flush()
        return TeacherAgentResult(status="completed", message="已完成。", run_id=run.run_id)

    monkeypatch.setattr(api, "run_teacher_agent", fake_run)
    monkeypatch.setattr(api, "build_teacher_agent_backend", Mock(return_value=Mock()))

    result = api.create_agent_run(AgentRunRequest(request="查询当前试卷"), session, Settings())

    assert result.run_id == "legacy-run"
    assert result.status == "completed"


def test_teacher_agent_http_entry_delegates_to_phase2b_agent(monkeypatch, session):
    expected = TeacherAgentResult(
        status="completed",
        message="已完成组卷。",
        paper=GeneratePaperToolResult(
            ok=True,
            paper_id="paper-1",
            version_id="version-1",
            summary=PaperSummary(
                total_questions=10,
                total_score=100,
                question_type_counts={"选择题": 4, "填空题": 2, "计算题": 4},
            ),
        ),
    )
    runner = Mock(return_value=expected)
    backend = Mock()
    monkeypatch.setattr(api, "run_teacher_agent", runner)
    monkeypatch.setattr(api, "build_teacher_agent_backend", Mock(return_value=backend))

    result = api.run_teacher_agent_endpoint(
        api.TeacherAgentRunRequest(
            message="帮我出一套第一章普通测试卷",
            conversation_id="human-entry-test",
        ),
        session,
        Settings(),
    )

    assert result.status == "completed"
    assert result.paper.summary.total_questions == 10
    assert result.paper.summary.total_score == 100
    assert result.paper.paper_id == "paper-1"
    assert result.paper.version_id == "version-1"
    runner.assert_called_once_with(
        session,
        "帮我出一套第一章普通测试卷",
        conversation_id="human-entry-test",
        paper_id=None,
        version_id=None,
        operation_id=None,
        backend=backend,
    )


def test_teacher_agent_http_entry_keeps_clarification_structured(monkeypatch, session):
    monkeypatch.setattr(
        api,
        "run_teacher_agent",
        Mock(return_value=TeacherAgentResult(
            status="needs_clarification",
            message="需要补充组卷信息。",
            clarification_questions=["请确认本次期中考试的知识范围。"],
        )),
    )
    result = api.run_teacher_agent_endpoint(
        api.TeacherAgentRunRequest(message="帮我出一套期中考试", conversation_id="human-entry-test"),
        session,
        Settings(),
    )
    assert result.status == "needs_clarification"
    assert result.paper is None


def test_teacher_agent_conversation_list_aggregates_message_history(session):
    history = DatabaseConversationHistoryStore(session)
    history.append("conversation-a", role="user", content="第一章测试卷")
    history.append("conversation-a", role="assistant", content="请确认")
    history.append("conversation-b", role="user", content="查询第三章题库")

    conversations = api.list_teacher_agent_conversations(session)

    assert {item.conversation_id for item in conversations} == {
        "conversation-a",
        "conversation-b",
    }
    titles = {item.conversation_id: item.title for item in conversations}
    assert titles["conversation-a"] == "第一章测试卷"
    assert titles["conversation-b"] == "查询第三章题库"
    assert all(item.last_message_at is not None for item in conversations)


def test_teacher_agent_session_reads_messages_workspace_and_pending_plan(session):
    conversation_id = "restored-session"
    history = DatabaseConversationHistoryStore(session)
    history.append(conversation_id, role="user", content="给我出第三章测试")
    history.append(conversation_id, role="assistant", content="请确认组卷方案。")
    TeachingDesignService(session).create(
        owner_key="local_teacher",
        conversation_id=conversation_id,
        content=TeachingDesignContent(
            title="第三章复习",
            objective="理解核心概念",
            scope_names=["第三章"],
        ),
        run_id="run-design",
        source_user_message="帮我设计第三章复习",
    )
    WorkspaceService(session).update(
        conversation_id,
        {
            "active_type": "paper",
            "current_paper_id": "paper-1",
            "current_version_id": "paper-1-v2",
        },
    )
    DatabasePendingReplacementStore(session).set_generation(
        conversation_id,
        PendingGeneration(request=GeneratePaperInput(
            paper_type="chapter_test",
            scope_names=["第三章"],
            total_score=100,
            question_type_requirements=[
                QuestionTypeRequirement(question_type="选择题", count=4, score_each=5),
                QuestionTypeRequirement(question_type="证明题", count=2, score_each=10),
            ],
        )),
    )

    restored = api.get_teacher_agent_session(conversation_id, session)

    assert restored.conversation_id == conversation_id
    assert restored.workspace is not None
    assert restored.workspace.current_paper_id == "paper-1"
    assert restored.workspace.current_version_id == "paper-1-v2"
    assert restored.active_teaching_design is not None
    assert restored.active_teaching_design.title == "第三章复习"
    assert [(item.role, item.content) for item in restored.messages] == [
        ("user", "给我出第三章测试"),
        ("assistant", "请确认组卷方案。"),
    ]
    assert restored.pending_generation.request.question_count == 6
    assert restored.pending_generation.pending_version == 1
