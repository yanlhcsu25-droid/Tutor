from sqlalchemy import select

from calculus_agent.agent.paper_change_service import PaperChangeService
from calculus_agent.agent.tool_registry import (
    AgentExecutionContext,
    build_agent_tools,
    execute_tool,
)
from calculus_agent.models import (
    Paper,
    PaperBlueprintRecord,
    PaperItem,
    Question,
    QuestionDraft,
)
from calculus_agent.papers.addressing import resolve_section_item_from_items


def _draft(source_item_id: str, text: str, question_type: str) -> QuestionDraft:
    return QuestionDraft(
        source_name="test",
        source_item_id=source_item_id,
        variant=1,
        subject="高等数学",
        question_type=question_type,
        question_text=text,
        normalized_fingerprint=f"fp-{source_item_id}",
        status="approved",
    )


def _question(
    session,
    *,
    source_item_id: str,
    text: str,
    question_type: str,
) -> Question:
    draft = _draft(source_item_id, text, question_type)
    session.add(draft)
    session.flush()

    question = Question(
        draft_id=draft.id,
        question_text=text,
        question_type=question_type,
        verification_status="manual_verified",
        review_status="approved",
        is_active=True,
    )
    session.add(question)
    session.flush()
    return question


def _items(session, paper_id: str) -> list[PaperItem]:
    return list(
        session.scalars(
            select(PaperItem)
            .where(PaperItem.paper_id == paper_id)
            .order_by(PaperItem.position)
        )
    )


def test_section_address_rebinds_to_current_version_after_delete(session, monkeypatch):
    blueprint = PaperBlueprintRecord(
        title="section address regression",
        blueprint_json={},
        status="used",
    )
    session.add(blueprint)
    session.flush()

    v1 = Paper(
        blueprint_id=blueprint.id,
        root_paper_id=None,
        parent_version_id=None,
        version=1,
        status="draft",
        title="v1",
        total_score=20,
        validation_status="pending",
    )
    session.add(v1)
    session.flush()

    questions = {
        label: _question(session, source_item_id=label, text=label, question_type="选择题")
        for label in ("A", "B", "C", "D")
    }
    for position, label in enumerate(("A", "B", "C", "D"), start=1):
        session.add(PaperItem(
            paper_id=v1.id,
            question_id=questions[label].id,
            section="选择题",
            position=position,
            score=5,
            locked=False,
        ))
    session.flush()

    old_second = resolve_section_item_from_items(
        _items(session, v1.id), section_type="选择题", section_order=2
    )
    assert old_second is not None
    assert old_second.question_id == questions["B"].id

    v2 = Paper(
        blueprint_id=blueprint.id,
        root_paper_id=v1.id,
        parent_version_id=v1.id,
        version=2,
        status="draft",
        title="v2",
        total_score=15,
        validation_status="pending",
    )
    session.add(v2)
    session.flush()
    for position, label in enumerate(("A", "C", "D"), start=1):
        session.add(PaperItem(
            paper_id=v2.id,
            question_id=questions[label].id,
            section="选择题",
            position=position,
            score=5,
            locked=False,
        ))
    session.flush()

    new_second = resolve_section_item_from_items(
        _items(session, v2.id), section_type="选择题", section_order=2
    )
    assert new_second is not None
    assert new_second.question_id == questions["C"].id
    assert new_second.question_id != questions["B"].id

    observed: dict[str, object] = {}

    def fake_select_replacement(
        self, *, version, items_by_position, position, change,
        candidates, profiles, knowledge, occupied,
    ):
        observed["version_id"] = version.id
        observed["target_position"] = position
        observed["question_id"] = items_by_position[position].question_id
        return None, "sentinel_stop_after_resolution", []

    monkeypatch.setattr(PaperChangeService, "_select_replacement", fake_select_replacement)

    context = AgentExecutionContext(
        session=session,
        conversation_id=None,
        paper_id=v2.id,
        version_id=v2.id,
        state_store=None,
    )
    tools = build_agent_tools(context)
    result = execute_tool(
        tools["preview_paper_changes"],
        {
            "operations": [{
                "type": "replace_question",
                "target": {"section_type": "选择题", "section_order": 2},
                "difficulty_direction": "same",
            }]
        },
    )

    assert "sentinel_stop_after_resolution" in result.payload["blocking_errors"]
    assert observed["version_id"] == v2.id
    assert observed["target_position"] == 2
    assert observed["question_id"] == questions["C"].id
    assert observed["question_id"] != questions["B"].id

