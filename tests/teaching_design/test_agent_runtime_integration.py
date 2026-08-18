from sqlalchemy import select

from calculus_agent.application.teaching_design_generation import (
    TeachingDesignPaperGenerationResult,
)
from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.tools.paper_tools import (
    GeneratePaperToolResult,
    PaperSummary,
)
import calculus_agent.agent.tool_adapters.teaching_design as teaching_design_adapter
from calculus_agent.models import (
    CurriculumNode,
    TeacherAgentRunTrace,
    Textbook,
)
from calculus_agent.teaching_design.service import TeachingDesignService


class ScriptedBackend:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def complete(self, messages, tools):
        self.requests.append((messages, tools))
        if not self.responses:
            raise AssertionError("ScriptedBackend has no response")
        return self.responses.pop(0)


def _call(name, arguments):
    return {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": name,
                        "arguments": arguments,
                    }
                }
            ],
        }
    }


def _text(value):
    return {
        "message": {
            "role": "assistant",
            "content": value,
        }
    }


def _design_content():
    return {
        "title": "第一到第三章期中复习",
        "objective": "完成第一到第三章核心内容复习并进行阶段测评。",
        "scope_names": ["第一章", "第二章", "第三章"],
        "knowledge_plan": [],
        "teaching_priorities": ["三章核心内容均衡覆盖"],
        "teaching_sequence": [],
        "lecture_plan": {
            "structure": ["知识框架", "重点讲解", "典型例题", "总结"],
            "emphasis": [],
            "notes": [],
        },
        "assessment_plan": {
            "paper_type": "midterm",
            "total_score": 100,
            "duration_minutes": 90,
            "difficulty": "hard",
            "ability_weights": {},
            "question_design_ideas": [],
            "notes": [],
        },
        "evidence_refs": [],
        "feasibility_warnings": [],
    }


def _tool_names(request):
    return {
        item["function"]["name"]
        for item in request[1]
    }


def _seed_curriculum(session):
    textbook = Textbook(
        name="运行时教学设计测试教材",
        edition="T3",
        is_active=True,
    )
    session.add(textbook)
    session.flush()
    for ordinal in range(1, 4):
        session.add(
            CurriculumNode(
                textbook_id=textbook.id,
                parent_id=None,
                node_type="chapter",
                code=str(ordinal),
                title=f"第{ordinal}章",
                sort_order=ordinal,
                review_status="approved",
            )
        )
    session.flush()


def test_real_conversation_create_revise_confirm_is_versioned_and_traceable(
    session,
    monkeypatch,
):
    conversation_id = "design-conversation"
    _seed_curriculum(session)

    class FakePaperGenerationService:
        def __init__(self, *, session, store, conversation_id):
            self.session = session
            self.store = store
            self.conversation_id = conversation_id

        def execute(self, design):
            paper = GeneratePaperToolResult(
                ok=True,
                paper_id="paper-from-confirmed-design",
                version_id="paper-from-confirmed-design",
                summary=PaperSummary(
                    total_questions=10,
                    total_score=100,
                    question_type_counts={},
                ),
                validation_status="passed",
            )
            return TeachingDesignPaperGenerationResult(
                ok=True,
                teaching_design_version_id=design.version_id,
                paper=paper,
            )

    monkeypatch.setattr(
        teaching_design_adapter,
        "TeachingDesignPaperGenerationService",
        FakePaperGenerationService,
    )

    create_backend = ScriptedBackend([
        _call(
            "inspect_curriculum",
            {
                "scope_names": [
                    "第一章",
                    "第二章",
                    "第三章",
                ]
            },
        ),
        _call(
            "inspect_question_bank",
            {
                "scope_names": [
                    "第一章",
                    "第二章",
                    "第三章",
                ],
                "detail_level": "aggregate",
            },
        ),
        _call(
            "create_teaching_design",
            {"content": _design_content()},
        ),
        _text("我已经基于当前课程与题库环境形成第一版教学设计，请你确认或继续修改。"),
    ])
    created_result = run_teacher_agent(
        session,
        "第一到第三章做一次90分钟期中复习，中等偏难。",
        conversation_id=conversation_id,
        backend=create_backend,
    )

    assert created_result.status == "waiting_confirmation"
    assert created_result.teaching_design is not None
    assert created_result.teaching_design.version == 1
    assert created_result.teaching_design.status == "awaiting_confirmation"
    assert created_result.teaching_design.created_by_run_id == created_result.run_id
    assert {
        item.kind
        for item in created_result.teaching_design.content.evidence_refs
    } == {
        "curriculum_scope",
        "question_bank_aggregate",
    }

    first_surface = _tool_names(create_backend.requests[0])
    assert "inspect_curriculum" in first_surface
    assert "inspect_question_bank" in first_surface
    assert "create_teaching_design" in first_surface

    # After create succeeds, runtime removes write tools for the rest of this
    # teacher turn so the model cannot auto-confirm its own proposal.
    assert create_backend.requests[3][1] == []

    revise_backend = ScriptedBackend([
        _call(
            "revise_teaching_design",
            {
                "patch": {
                    "teaching_priorities": [
                        "第三章重点加强",
                        "第一、二章保持核心覆盖",
                    ]
                },
                "change_reason": "teacher_requested_more_chapter_3",
            },
        ),
        _text("已按你的要求形成第二版，第三章权重更高，请确认。"),
    ])
    revised_result = run_teacher_agent(
        session,
        "第三章再重点一点。",
        conversation_id=conversation_id,
        backend=revise_backend,
    )

    assert revised_result.status == "waiting_confirmation"
    assert revised_result.teaching_design is not None
    assert revised_result.teaching_design.version == 2
    assert (
        revised_result.teaching_design.parent_version_id
        == created_result.teaching_design.version_id
    )
    assert revised_result.teaching_design.created_by_run_id == revised_result.run_id
    assert "revise_teaching_design" in _tool_names(revise_backend.requests[0])
    assert "confirm_teaching_design" in _tool_names(revise_backend.requests[0])
    assert revise_backend.requests[1][1] == []

    # No scope change: existing evidence remains valid and no mandatory re-scan
    # is introduced for a simple priority edit.
    assert (
        revised_result.teaching_design.content.evidence_refs
        == created_result.teaching_design.content.evidence_refs
    )

    confirm_backend = ScriptedBackend([
        _call("confirm_teaching_design", {}),
        _text("教学设计已确认，并已按该设计生成试卷。"),
    ])
    confirmed_result = run_teacher_agent(
        session,
        "可以，就按这个来。",
        conversation_id=conversation_id,
        backend=confirm_backend,
    )

    assert confirmed_result.status == "completed"
    assert confirmed_result.teaching_design is not None
    assert confirmed_result.paper is not None
    assert confirmed_result.paper.paper_id == "paper-from-confirmed-design"
    assert confirmed_result.teaching_design_generation is not None
    assert confirmed_result.teaching_design_generation.ok is True
    assert confirmed_result.teaching_design.version == 2
    assert confirmed_result.teaching_design.status == "confirmed"
    assert (
        confirmed_result.teaching_design.confirmed_by_run_id
        == confirmed_result.run_id
    )

    service = TeachingDesignService(session)
    active = service.get_active(
        owner_key="local_teacher",
        conversation_id=conversation_id,
    )
    assert active is not None
    assert active.version_id == confirmed_result.teaching_design.version_id
    assert active.status == "confirmed"

    v1 = service.get(created_result.teaching_design.version_id)
    assert v1.status == "superseded"
    assert v1.superseded_by_version_id == active.version_id

    traces = list(
        session.scalars(
            select(TeacherAgentRunTrace)
            .where(
                TeacherAgentRunTrace.conversation_id == conversation_id
            )
            .order_by(TeacherAgentRunTrace.started_at)
        ).all()
    )
    assert [trace.run_id for trace in traces] == [
        created_result.run_id,
        revised_result.run_id,
        confirmed_result.run_id,
    ]
    assert [
        item["tool_name"]
        for item in traces[0].tool_calls_json
    ] == [
        "inspect_curriculum",
        "inspect_question_bank",
        "create_teaching_design",
    ]
    assert traces[1].tool_calls_json[0]["tool_name"] == "revise_teaching_design"
    assert traces[2].tool_calls_json[0]["tool_name"] == "confirm_teaching_design"
