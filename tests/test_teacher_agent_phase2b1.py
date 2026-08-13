import json
from unittest.mock import patch

from calculus_agent.agent import run_teacher_agent
from calculus_agent.agent.tools.paper_tools import GeneratePaperToolResult, PaperSummary


class _Backend:
    def __init__(self, *responses): self.responses = list(responses)
    def complete(self, messages, tools): return self.responses.pop(0)


def _generate(arguments, name="preview_generation_plan"):
    return {"message": {"tool_calls": [{
        "id": "generate",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }]}}


def _final(text):
    return {"message": {"content": text}}


def test_clarification_never_persists_paper(session):
    tool_result = GeneratePaperToolResult(
        ok=False,
        needs_clarification=True,
        blocking_errors=["missing_exam_scope"],
        clarification_questions=["请确认期中考试范围。"],
    )
    with patch("calculus_agent.agent.tool_registry.generate_paper_from_input", return_value=tool_result):
        result = run_teacher_agent(
            session,
            "帮我出一套期中考试",
            backend=_Backend(_generate({"paper_type": "midterm"}), _final("请确认期中考试范围。")),
        )
    assert result.status == "needs_clarification"
    assert result.paper is None
    assert result.generation_preview and not result.generation_preview.ok


def test_final_clarification_remains_structured(session):
    tool_result = GeneratePaperToolResult(
        ok=False,
        needs_clarification=True,
        blocking_errors=["missing_difficulty_ratio"],
        clarification_questions=["请确认期末难度占比。"],
    )
    with patch("calculus_agent.agent.tool_registry.generate_paper_from_input", return_value=tool_result):
        result = run_teacher_agent(
            session,
            "帮我出一套期末考试",
            backend=_Backend(_generate({"paper_type": "final"}), _final("请确认期末难度占比。")),
        )
    assert result.status == "needs_clarification"
    assert result.clarification_questions


def test_tool_failure_becomes_structured_agent_failure(session):
    tool_result = GeneratePaperToolResult(ok=False, blocking_errors=["scope_not_found"])
    with patch("calculus_agent.agent.tool_registry.generate_paper_from_input", return_value=tool_result):
        result = run_teacher_agent(
            session,
            "帮我出一套第一章测试卷",
            backend=_Backend(
                _generate({"paper_type": "chapter_test", "scope_names": ["第一章"]}),
                _final("当前课程目录中找不到第一章。"),
            ),
        )
    assert result.status == "needs_clarification"
    assert result.blocking_errors == ["scope_not_found"]


def test_confirmed_generation_becomes_completed(session):
    tool_result = GeneratePaperToolResult(
        ok=True,
        paper_id="paper-1",
        version_id="paper-1",
        summary=PaperSummary(total_questions=10, total_score=100, question_type_counts={"选择题": 4}),
    )
    with (
        patch("calculus_agent.agent.tool_registry.build_structured_generation_request") as build,
        patch("calculus_agent.agent.tool_registry.generate_paper_from_input", return_value=tool_result),
    ):
        from calculus_agent.schemas import PaperBlueprint
        from calculus_agent.agent.schemas import PaperGenerationRequest
        build.return_value = (PaperGenerationRequest(blueprint=PaperBlueprint(total_questions=10)), [], [], [])
        preview = run_teacher_agent(
            session,
            "帮我出一套第一章测试，多一点计算题",
            conversation_id="confirmed-generation",
            backend=_Backend(
                _generate({"paper_type": "chapter_test", "scope_names": ["第一章"]}),
                _final("请确认方案。"),
            ),
        )
        result = run_teacher_agent(
            session,
            "确认组卷",
            conversation_id="confirmed-generation",
            backend=_Backend(_generate({}, "confirm_generation_plan"), _final("已完成组卷。")),
        )
    assert preview.status == "waiting_confirmation"
    assert result.status == "completed"
    assert result.paper.ok
    assert result.paper.summary.total_questions == 10
