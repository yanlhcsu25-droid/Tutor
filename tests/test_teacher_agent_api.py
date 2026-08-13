from unittest.mock import Mock

import calculus_agent.api as api
from calculus_agent.agent.agent import TeacherAgentResult
from calculus_agent.agent.tools.paper_tools import GeneratePaperToolResult, PaperSummary
from calculus_agent.config import Settings


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
                question_type_counts={"选择题": 4, "填空题": 2, "解答题": 4},
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
