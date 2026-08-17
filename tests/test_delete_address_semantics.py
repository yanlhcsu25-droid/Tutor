import pytest
from pydantic import ValidationError

import calculus_agent.agent.tool_registry as tool_registry
from calculus_agent.agent.tool_registry import (
    AgentExecutionContext,
    PreviewAdjustmentInput,
    build_agent_tools,
    execute_tool,
)
from calculus_agent.agent.tools.analysis_tools import (
    PaperAdjustmentPreview,
    _build_removal_operations,
)
from calculus_agent.models import (
    Paper,
    PaperBlueprintRecord,
    PaperItem,
    Question,
    QuestionDraft,
)
from calculus_agent.papers.addressing import QuestionAddress


def _item(position: int, score: float, section: str = "选择题") -> PaperItem:
    return PaperItem(
        paper_id="paper",
        question_id=f"q{position}",
        section=section,
        position=position,
        score=score,
        locked=False,
    )


def test_plain_delete_does_not_redistribute_removed_score():
    items = [
        _item(1, 5),
        _item(2, 5),
        _item(3, 10, "计算题"),
    ]
    operations, errors = _build_removal_operations(
        items,
        remove_positions=[2],
        target_total_score=None,
    )
    assert errors == []
    assert [op.type for op in operations] == ["remove_question"]
    assert operations[0].position == 2


def test_explicit_target_total_keeps_rebalance_behavior():
    items = [
        _item(1, 5),
        _item(2, 5),
        _item(3, 10, "计算题"),
    ]
    operations, errors = _build_removal_operations(
        items,
        remove_positions=[2],
        target_total_score=20,
    )
    assert errors == []
    assert any(op.type == "remove_question" for op in operations)
    assert any(op.type == "change_score" for op in operations)


def test_adjustment_input_rejects_mixed_remove_address_modes():
    with pytest.raises(ValidationError):
        PreviewAdjustmentInput(
            remove_addresses=[
                QuestionAddress(
                    section_type="填空题",
                    section_order=2,
                )
            ],
            remove_positions=[4],
        )


def _create_question(session, sid: str, text: str, qtype: str) -> Question:
    draft = QuestionDraft(
        source_name="test",
        source_item_id=sid,
        variant=1,
        subject="高等数学",
        question_type=qtype,
        question_text=text,
        normalized_fingerprint=f"fp-{sid}",
        status="approved",
    )
    session.add(draft)
    session.flush()

    question = Question(
        draft_id=draft.id,
        question_text=text,
        question_type=qtype,
        verification_status="manual_verified",
        review_status="approved",
        is_active=True,
    )
    session.add(question)
    session.flush()
    return question


def test_delete_address_resolves_to_current_global_position(
    session,
    monkeypatch,
):
    blueprint = PaperBlueprintRecord(
        title="delete address",
        blueprint_json={},
        status="used",
    )
    session.add(blueprint)
    session.flush()

    paper = Paper(
        blueprint_id=blueprint.id,
        version=1,
        status="draft",
        title="paper",
        total_score=20,
        validation_status="pending",
    )
    session.add(paper)
    session.flush()

    specs = [
        ("c1", "选择1", "选择题", 5),
        ("c2", "选择2", "选择题", 5),
        ("f1", "填空1", "填空题", 5),
        ("f2", "填空2", "填空题", 5),
    ]

    for position, (sid, text, qtype, score) in enumerate(specs, 1):
        question = _create_question(session, sid, text, qtype)
        session.add(
            PaperItem(
                paper_id=paper.id,
                question_id=question.id,
                section=qtype,
                position=position,
                score=score,
                locked=False,
            )
        )
    session.flush()

    observed = {}

    def fake_preview_adjust_paper(
        db_session,
        *,
        paper_id,
        knowledge_preferences=None,
        question_type_changes=None,
        remove_positions=None,
        target_total_score=None,
    ):
        observed["paper_id"] = paper_id
        observed["remove_positions"] = list(remove_positions or [])
        observed["target_total_score"] = target_total_score
        return PaperAdjustmentPreview(
            ok=False,
            paper_id=paper_id,
            blocking_errors=["sentinel_stop_after_resolution"],
        )

    monkeypatch.setattr(
        tool_registry,
        "preview_adjust_paper",
        fake_preview_adjust_paper,
    )

    context = AgentExecutionContext(
        session=session,
        conversation_id=None,
        paper_id=paper.id,
        version_id=paper.id,
        state_store=None,
    )
    tools = build_agent_tools(context)

    result = execute_tool(
        tools["preview_adjust_paper"],
        {
            "remove_addresses": [
                {
                    "section_type": "填空题",
                    "section_order": 2,
                }
            ]
        },
    )

    assert result.payload["blocking_errors"] == [
        "sentinel_stop_after_resolution"
    ]
    assert observed["remove_positions"] == [4]
    assert observed["target_total_score"] is None


def test_delete_schema_documents_natural_score_drop():
    schema = PreviewAdjustmentInput.model_json_schema()
    description = schema["properties"]["target_total_score"]["description"]
    assert "scores disappear naturally" in description
