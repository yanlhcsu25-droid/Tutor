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


def test_fresh_teaching_planning_request_exposes_design_creation_only(session):
    # Fresh Teaching Planning can create a design after environment evidence;
    # history lookup and activation remain unavailable.
    conversation_id = "fresh-request"
    _seed_curriculum(session)
    backend = ScriptedBackend([_text("我先了解下你的需求。")])
    run_teacher_agent(
        session,
        "学生极限一直学不好，帮我安排第一到第三章复习。",
        conversation_id=conversation_id,
        backend=backend,
    )
    surface = _tool_names(backend.requests[0])
    assert "prepare_generation_plan" not in surface
    assert "inspect_curriculum" in surface
    assert "inspect_question_bank" in surface
    assert "create_teaching_design" in surface
    assert "search_teaching_design_history" not in surface
    assert "activate_teaching_design" not in surface


def test_real_conversation_revise_confirm_legacy_design_is_versioned_and_traceable(
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

    # TeachingDesign is legacy-only: fresh requests no longer create a new
    # design. Seed an awaiting-confirmation design to exercise the remaining
    # legacy lifecycle (revise -> confirm).
    service = TeachingDesignService(session)
    seeded = service.create(
        owner_key="local_teacher",
        conversation_id=conversation_id,
        content=_design_content(),
        run_id="seed-create",
        source_user_message="第一到第三章做一次90分钟期中复习，中等偏难。",
    )

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
        == seeded.version_id
    )
    assert revised_result.teaching_design.created_by_run_id == revised_result.run_id
    assert "revise_teaching_design" in _tool_names(revise_backend.requests[0])
    assert "confirm_teaching_design" not in _tool_names(revise_backend.requests[0])
    assert "create_teaching_design" not in _tool_names(revise_backend.requests[0])
    assert revise_backend.requests[1][1] == []

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
    assert "confirm_teaching_design" in _tool_names(confirm_backend.requests[0])
    assert "revise_teaching_design" not in _tool_names(confirm_backend.requests[0])

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

    active = service.get_active(
        owner_key="local_teacher",
        conversation_id=conversation_id,
    )
    assert active is not None
    assert active.version_id == confirmed_result.teaching_design.version_id
    assert active.status == "confirmed"

    v1 = service.get(seeded.version_id)
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
        revised_result.run_id,
        confirmed_result.run_id,
    ]
    assert traces[0].tool_calls_json[0]["tool_name"] == "revise_teaching_design"
    assert traces[1].tool_calls_json[0]["tool_name"] == "confirm_teaching_design"
