import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.tool_registry import AgentExecutionContext, build_agent_tools
from calculus_agent.agent.tools.read_tools import ReadCurrentPaperInput, read_current_paper
from calculus_agent.models import (
    AgentPendingReplacement,
    KnowledgeNode,
    Paper,
    PaperBlueprintRecord,
    PaperItem,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    QuestionProfile,
    TeacherAgentRunTrace,
)


class _Backend:
    def __init__(self, *responses):
        self.responses = list(responses)

    def complete(self, messages, tools):
        return self.responses.pop(0)


def _tool(action: str, arguments: str = "{}") -> dict:
    return {"message": {"tool_calls": [{"id": "read-call", "type": "function", "function": {"name": action, "arguments": arguments}}]}}


def _final(text: str) -> dict:
    return {"message": {"content": text}}


def _seed_paper(session, *, count: int = 5) -> Paper:
    blueprint = PaperBlueprintRecord(
        id="read-blueprint",
        title="读取测试卷",
        blueprint_json={},
        status="draft",
    )
    paper = Paper(
        id="read-paper-v2",
        blueprint_id=blueprint.id,
        root_paper_id="read-paper-v2",
        version=2,
        status="draft",
        title="第一章测试卷",
        total_score=count * 5,
        validation_status="pending",
    )
    session.add_all([blueprint, paper])
    for position in range(1, count + 1):
        node = KnowledgeNode(
            id=f"read-k{position}",
            node_type="concept",
            name=f"知识点{position}",
            normalized_name=f"知识点{position}",
        )
        draft = QuestionDraft(
            id=f"read-d{position}",
            source_name="read-test",
            source_item_id=str(position),
            variant=1,
            subject="高数",
            question_type="选择题" if position == 1 else "计算题",
            question_text=f"第{position}题真实题干",
            options_json=["A. 选项一", "B. 选项二"] if position == 1 else [],
            normalized_fingerprint=f"{position:064d}",
            status="approved",
        )
        question = Question(
            id=f"read-q{position}",
            draft_id=draft.id,
            question_text=draft.question_text,
            question_type=draft.question_type,
            verification_status="verified",
            review_status="approved",
        )
        session.add_all([node, draft, question])
        session.flush()
        session.add_all([
            QuestionKnowledgeLink(
                question_id=question.id,
                knowledge_node_id=node.id,
                relation_type="primary",
            ),
            QuestionProfile(
                question_id=question.id,
                profile_version=1,
                difficulty=position,
                estimated_time_min=5,
                reasoning_depth=1,
                calculation_load=1,
                knowledge_depth=1,
                comprehensive_level=1,
                confidence=1,
                profile_source="human",
                profile_status="approved",
                reason="read test",
            ),
            PaperItem(
                paper_id=paper.id,
                question_id=question.id,
                section=question.question_type,
                position=position,
                score=position + 1,
                locked=False,
            ),
        ])
    session.flush()
    return paper


def test_read_specific_question_returns_real_content_options_and_metadata(session):
    paper = _seed_paper(session)
    result = read_current_paper(
        session,
        current_paper_version_id=paper.id,
        request=ReadCurrentPaperInput(positions=[1]),
    )
    assert result.ok and result.paper.version == 2
    question = result.questions[0]
    assert question.position == 1
    assert question.content == "第1题真实题干"
    assert question.question_type == "选择题"
    assert question.score == 2
    assert question.difficulty == 1
    assert question.knowledge_points == ["知识点1"]
    assert question.options == ["A. 选项一", "B. 选项二"]


@pytest.mark.parametrize(
    ("message", "position", "expected"),
    [
        ("第一题是啥？", 1, "第1题真实题干"),
        ("第三题考什么知识点？", 3, "知识点3"),
        ("第五题多少分？", 5, "6分"),
    ],
)
def test_agent_executes_llm_read_for_specific_question(session, message, position, expected):
    paper = _seed_paper(session)
    backend = _Backend(
        _tool("read_current_paper", f'{{"positions":[{position}]}}'),
        _final(f"读取完成：{expected}"),
    )
    result = run_teacher_agent(
        session,
        message,
        conversation_id=f"read-{position}",
        paper_id=paper.id,
        version_id=paper.id,
        backend=backend,
    )
    assert result.status == "completed"
    assert expected in result.message
    assert result.paper_read.questions[0].position == position


def test_explicit_question_read_is_narrowed_when_model_omits_positions(session):
    paper = _seed_paper(session)
    backend = _Backend(
        _tool("read_current_paper"),
        _final("第五题已读取。"),
    )

    result = run_teacher_agent(
        session,
        "第五题是什么？",
        conversation_id="read-position-guard",
        paper_id=paper.id,
        version_id=paper.id,
        backend=backend,
    )

    assert result.status == "completed"
    assert [question.position for question in result.paper_read.questions] == [5]
    trace = session.scalars(
        select(TeacherAgentRunTrace).where(
            TeacherAgentRunTrace.conversation_id == "read-position-guard"
        )
    ).one()
    assert trace.tool_calls_json[0]["arguments"] == {"positions": [5]}
    assert trace.tool_calls_json[0]["paper_observation"]["positions"] == [5]


@pytest.mark.parametrize("message", ["给我看看现在这套卷", "这套卷一共多少题？"])
def test_agent_reads_bounded_whole_paper_overview(session, message):
    paper = _seed_paper(session)
    backend = _Backend(_tool("read_current_paper"), _final("当前试卷共5题。"))
    result = run_teacher_agent(
        session,
        message,
        conversation_id="read-overview",
        paper_id=paper.id,
        version_id=paper.id,
        backend=backend,
    )
    assert result.status == "completed"
    assert result.paper_read.paper.question_count == 5
    assert len(result.paper_read.questions) == 5
    assert all(question.preview and question.content is None for question in result.paper_read.questions)
    assert "共5题" in result.message


def test_read_without_current_paper_fails_without_generation(session):
    backend = _Backend(
        _tool("read_current_paper", '{"positions":[1]}'),
        _final("当前还没有可查看的试卷。"),
    )
    result = run_teacher_agent(
        session,
        "第一题是什么？",
        conversation_id="read-no-paper",
        backend=backend,
    )
    assert result.status == "needs_clarification"
    assert result.blocking_errors == ["no_current_paper"]
    assert result.paper is None


def test_read_invalid_position_reports_real_question_count(session):
    paper = _seed_paper(session)
    result = read_current_paper(
        session,
        current_paper_version_id=paper.id,
        request=ReadCurrentPaperInput(positions=[20]),
    )
    assert not result.ok
    assert result.code == "question_position_not_found"
    assert result.position == 20
    assert result.question_count == 5


def test_read_input_rejects_non_positive_positions():
    with pytest.raises(ValidationError):
        ReadCurrentPaperInput(positions=[0])


def test_read_is_read_only_and_trace_contains_validated_arguments(session):
    paper = _seed_paper(session)
    versions_before = session.scalar(select(func.count()).select_from(Paper))
    items_before = session.scalar(select(func.count()).select_from(PaperItem))
    backend = _Backend(
        _tool("read_current_paper", '{"positions":[1]}'),
        _final("第1题读取完成。"),
    )
    result = run_teacher_agent(
        session,
        "第一题是啥？",
        conversation_id="read-trace",
        paper_id=paper.id,
        version_id=paper.id,
        backend=backend,
    )
    traces = list(session.scalars(
        select(TeacherAgentRunTrace).where(
            TeacherAgentRunTrace.conversation_id == "read-trace",
        )
    ))
    assert result.status == "completed"
    assert traces[0].tool_calls_json[0]["tool_name"] == "read_current_paper"
    assert traces[0].tool_calls_json[0]["arguments"]["positions"] == [1]
    assert session.scalar(select(func.count()).select_from(Paper)) == versions_before
    assert session.scalar(select(func.count()).select_from(PaperItem)) == items_before
    assert session.scalar(select(func.count()).select_from(AgentPendingReplacement)) == 0


def test_tool_description_keeps_read_analyze_and_replace_separate():
    tools = build_agent_tools(AgentExecutionContext(
        session=None, conversation_id=None, paper_id=None, version_id=None, state_store=None
    ))
    assert "factual questions" in tools["read_current_paper"].description
    assert "Analyze difficulty" in tools["analyze_current_paper"].description
    assert "requires later confirmation" in tools["preview_replace_question"].description


def test_existing_analysis_and_replacement_actions_are_not_shadowed(session):
    paper = _seed_paper(session)
    analysis = run_teacher_agent(
        session,
        "分析一下这张卷",
        paper_id=paper.id,
        version_id=paper.id,
        backend=_Backend(_tool("analyze_current_paper"), _final("分析完成。")),
    )
    assert analysis.analysis is not None
    replacement_backend = _Backend(
        _tool("preview_replace_question", '{"position":1,"difficulty_direction":"same"}'),
        _final("替换预览完成。"),
    )
    replacement = run_teacher_agent(
        session,
        "第一题换一道",
        conversation_id="read-replace-boundary",
        paper_id=paper.id,
        version_id=paper.id,
        backend=replacement_backend,
    )
    assert replacement.paper_read is None
    assert replacement.status in {"waiting_confirmation", "failed"}
