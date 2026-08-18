import json

from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    QuestionProfile,
    Textbook,
)


def _tool_call(name, arguments):
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


class ObservationAwareBackend:
    """Small deterministic fake proving Tool Observation reaches design choice."""

    def complete(self, messages, tools):
        tool_messages = [
            item
            for item in messages
            if item.get("role") == "tool"
        ]

        if not any(
            item.get("name") == "inspect_curriculum"
            for item in tool_messages
        ):
            return _tool_call(
                "inspect_curriculum",
                {
                    "scope_names": [
                        "第一章",
                        "第二章",
                    ]
                },
            )

        if not any(
            item.get("name") == "inspect_question_bank"
            for item in tool_messages
        ):
            return _tool_call(
                "inspect_question_bank",
                {
                    "scope_names": [
                        "第一章",
                        "第二章",
                    ],
                    "detail_level": "aggregate",
                },
            )

        if not any(
            item.get("name") == "create_teaching_design"
            for item in tool_messages
        ):
            supply_message = next(
                item
                for item in reversed(tool_messages)
                if item.get("name")
                == "inspect_question_bank"
            )
            supply = json.loads(
                supply_message["content"]
            )
            scarce = min(
                supply["chapters"],
                key=lambda item: item["total_questions"],
            )
            priority = (
                f"{scarce['title']}当前可组卷题量"
                f"{scarce['total_questions']}题，"
                "教学重点可保留，但测评不做机械等量硬配额。"
            )
            return _tool_call(
                "create_teaching_design",
                {
                    "content": {
                        "title": "第一到第二章阶段复习",
                        "objective": "完成两章复习与阶段测评。",
                        "scope_names": [
                            "第一章",
                            "第二章",
                        ],
                        "teaching_priorities": [
                            priority
                        ],
                        "feasibility_warnings": [
                            priority
                        ],
                    }
                },
            )

        return _text(
            "已基于当前教材和题库供给形成教学设计，请确认或继续修改。"
        )


def _chapter(session, textbook, ordinal: int):
    chapter = CurriculumNode(
        textbook_id=textbook.id,
        node_type="chapter",
        code=str(ordinal),
        title=f"第{ordinal}章",
        sort_order=ordinal,
        review_status="approved",
    )
    session.add(chapter)
    session.flush()
    section = CurriculumNode(
        textbook_id=textbook.id,
        parent_id=chapter.id,
        node_type="section",
        code=f"{ordinal}.1",
        title=f"{ordinal}.1 内容",
        sort_order=ordinal * 10,
        review_status="approved",
    )
    session.add(section)
    session.flush()
    knowledge = KnowledgeNode(
        curriculum_node_id=section.id,
        node_type="concept",
        name=f"知识{ordinal}",
        normalized_name=f"知识{ordinal}-{textbook.id}",
        source_type="directory",
        confidence=1.0,
        review_status="approved",
    )
    session.add(knowledge)
    session.flush()
    return chapter, knowledge


def _add_question(session, chapter, knowledge, number):
    draft = QuestionDraft(
        source_name="ocr_import",
        source_item_id=f"aware-{chapter.id}-{number}",
        variant=1,
        subject="高等数学",
        question_type="计算题",
        question_text=f"题 {number}",
        reference_answers_json=["1"],
        normalized_fingerprint=(
            f"{chapter.id}{number}".encode().hex()[:64].ljust(64, "0")
        ),
        status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        draft_id=draft.id,
        curriculum_chapter_id=chapter.id,
        question_text=draft.question_text,
        question_type="计算题",
        final_answer="1",
        solution_json={"solution_steps": ["解析"]},
        verification_status="verified",
        review_status="approved",
        is_active=True,
        knowledge_match_status="current",
    )
    session.add(question)
    session.flush()
    session.add(
        QuestionKnowledgeLink(
            question_id=question.id,
            knowledge_node_id=knowledge.id,
            relation_type="primary_concept",
            confidence=1.0,
        )
    )
    session.add(
        QuestionProfile(
            question_id=question.id,
            profile_version=1,
            difficulty=3,
            estimated_time_min=8,
            reasoning_depth=3,
            calculation_load=3,
            knowledge_depth=3,
            comprehensive_level=3,
            confidence=0.95,
            profile_source="human",
            profile_status="approved",
            reason="fixture",
        )
    )
    session.flush()


def _run_with_supply(session, *, first_count: int, second_count: int, conversation_id: str):
    textbook = Textbook(
        name=f"教材-{conversation_id}",
        edition="1",
        is_active=True,
    )
    session.add(textbook)
    session.flush()

    first, k1 = _chapter(session, textbook, 1)
    second, k2 = _chapter(session, textbook, 2)

    for index in range(first_count):
        _add_question(session, first, k1, 100 + index)
    for index in range(second_count):
        _add_question(session, second, k2, 200 + index)

    backend = ObservationAwareBackend()
    result = run_teacher_agent(
        session,
        "帮我设计第一到第二章阶段复习，覆盖尽量均衡。",
        conversation_id=conversation_id,
        backend=backend,
    )
    return result


def test_question_bank_observation_can_change_the_created_design(session):
    first = _run_with_supply(
        session,
        first_count=3,
        second_count=1,
        conversation_id="scarce-second",
    )
    assert first.status == "waiting_confirmation"
    assert first.teaching_design is not None
    first_priority = (
        first.teaching_design.content.teaching_priorities[0]
    )
    assert "题量1题" in first_priority
    assert "第2章" in first_priority
    assert {
        item.kind
        for item in first.teaching_design.content.evidence_refs
    } == {
        "curriculum_scope",
        "question_bank_aggregate",
    }

    # Deactivate the previous textbook so chapter resolution uses the next
    # fixture's current source of truth.
    for textbook in session.query(Textbook).all():
        textbook.is_active = False
    session.flush()

    second = _run_with_supply(
        session,
        first_count=1,
        second_count=3,
        conversation_id="scarce-first",
    )
    assert second.status == "waiting_confirmation"
    assert second.teaching_design is not None
    second_priority = (
        second.teaching_design.content.teaching_priorities[0]
    )
    assert "题量1题" in second_priority
    assert "第1章" in second_priority
    assert second_priority != first_priority
