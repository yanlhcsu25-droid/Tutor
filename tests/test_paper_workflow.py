import pytest
from sqlalchemy import select

from calculus_agent.models import PaperItem, Question, QuestionDraft
from calculus_agent.papers.workflow import (
    BlueprintStateError,
    InfeasiblePaperError,
    confirm_blueprint,
    create_paper,
    get_paper,
    load_paper_preview,
    list_paper_history,
    lock_paper_item,
    reorder_paper_items,
    replace_paper_item,
    redo_paper_operation,
    restore_paper_version,
    save_blueprint,
    update_paper_item,
    undo_paper_operations,
)
from calculus_agent.schemas import PaperBlueprint, SectionRequirement


def _question(session, number: int, question_type: str):
    draft = QuestionDraft(
        source_name="workflow", source_item_id=str(number), variant=1, subject="初中数学",
        grade="八年级", question_type=question_type,
        question_text=f"题目 {number}", reference_answers_json=[str(number)],
        solution_text=f"解析 {number}", normalized_fingerprint=str(number).zfill(64), status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        draft_id=draft.id, question_text=draft.question_text, grade="八年级",
        question_type=question_type, final_answer=str(number),
        solution_json={"solution_steps": [f"解析 {number}"]}, verification_status="verified",
        review_status="approved",
    )
    session.add(question)
    session.flush()
    return question


def _blueprint(seed=42):
    return PaperBlueprint(
        title="持久化测试卷", grade="八年级", total_questions=3, total_score=25,
        sections=[
            SectionRequirement(question_type="选择题", count=2, score_per_question=5, total_score=10),
            SectionRequirement(question_type="计算题", count=1, score_per_question=15, total_score=15),
        ], seed=seed,
    )


def test_unconfirmed_blueprint_cannot_create_paper(session):
    saved = save_blueprint(session, _blueprint())
    with pytest.raises(BlueprintStateError):
        create_paper(session, saved.blueprint_id)


def test_paper_items_persist_and_validate_exact_constraints(session):
    _question(session, 1, "选择题")
    _question(session, 2, "选择题")
    _question(session, 3, "计算题")
    saved = save_blueprint(session, _blueprint())
    confirm_blueprint(session, saved.blueprint_id)
    paper = create_paper(session, saved.blueprint_id)

    assert paper.status == "passed"
    assert paper.total_score == 25
    assert paper.validation_report.passed is True
    assert len(session.scalars(select(PaperItem).where(PaperItem.paper_id == paper.paper_id)).all()) == 3
    assert [item.score for item in paper.preview.items] == [5, 5, 15]


def test_saved_paper_is_single_source_for_preview_and_export(session):
    _question(session, 1, "选择题")
    _question(session, 2, "选择题")
    _question(session, 3, "计算题")
    saved = save_blueprint(session, _blueprint())
    confirm_blueprint(session, saved.blueprint_id)
    paper = create_paper(session, saved.blueprint_id)
    persisted = get_paper(session, paper.paper_id)
    export_source = load_paper_preview(session, paper.paper_id)
    assert [x.question_id for x in persisted.preview.items] == [x.question_id for x in export_source.items]


def test_infeasible_supply_returns_structured_shortage(session):
    _question(session, 1, "选择题")
    saved = save_blueprint(session, _blueprint())
    confirm_blueprint(session, saved.blueprint_id)
    with pytest.raises(InfeasiblePaperError) as raised:
        create_paper(session, saved.blueprint_id)
    assert any(item.code == "QUESTION_TYPE_SHORTAGE" for item in raised.value.violations)



def test_same_seed_produces_same_order(session):
    for number in range(1, 7):
        _question(session, number, "选择题" if number <= 4 else "计算题")
    first = save_blueprint(session, _blueprint(seed=7))
    second = save_blueprint(session, _blueprint(seed=7))
    confirm_blueprint(session, first.blueprint_id)
    confirm_blueprint(session, second.blueprint_id)
    paper_a = create_paper(session, first.blueprint_id)
    paper_b = create_paper(session, second.blueprint_id)
    assert [x.question_id for x in paper_a.preview.items] == [x.question_id for x in paper_b.preview.items]


def test_paper_edits_create_persistent_versions(session):
    for number in range(1, 5):
        _question(session, number, "选择题" if number <= 3 else "计算题")
    saved = save_blueprint(session, _blueprint())
    confirm_blueprint(session, saved.blueprint_id)
    original = create_paper(session, saved.blueprint_id)
    original_ids = [item.question_id for item in original.preview.items]

    replaced = replace_paper_item(session, original.paper_id, original.preview.items[0].item_id)
    assert replaced.paper_id != original.paper_id
    assert replaced.version == 2
    assert replaced.parent_version_id == original.paper_id
    assert [item.question_id for item in get_paper(session, original.paper_id).preview.items] == original_ids
    assert [item.question_id for item in replaced.preview.items] != original_ids

    locked = lock_paper_item(session, replaced.paper_id, replaced.preview.items[0].item_id, locked=True)
    assert locked.version == 3
    assert locked.preview.items[0].locked is True

    reversed_ids = [item.item_id for item in reversed(locked.preview.items)]
    reordered = reorder_paper_items(session, locked.paper_id, reversed_ids)
    assert reordered.version == 4
    assert [item.question_id for item in reordered.preview.items] == [item.question_id for item in reversed(locked.preview.items)]

    rescored = update_paper_item(session, reordered.paper_id, reordered.preview.items[0].item_id, score=1)
    assert rescored.version == 5
    assert rescored.validation_report.passed is False
    assert any(item.code == "TOTAL_SCORE_MISMATCH" for item in rescored.validation_report.violations)


def test_operation_history_supports_undo_redo_and_restore(session):
    for number in range(1, 5):
        _question(session, number, "选择题" if number <= 3 else "计算题")
    saved = save_blueprint(session, _blueprint())
    confirm_blueprint(session, saved.blueprint_id)
    original = create_paper(session, saved.blueprint_id)
    original_ids = [item.question_id for item in original.preview.items]

    replaced = replace_paper_item(
        session, original.paper_id, original.preview.items[0].item_id
    )
    replaced_ids = [item.question_id for item in replaced.preview.items]
    assert replaced_ids != original_ids
    history = list_paper_history(session, replaced.paper_id)
    assert len(history) == 1
    assert history[0].operation_type == "replace_question"
    assert history[0].operations[0]["old_question_id"] == original_ids[0]

    undone = undo_paper_operations(session, replaced.paper_id)
    assert undone.version == 3
    assert [item.question_id for item in undone.preview.items] == original_ids
    assert list_paper_history(session, undone.paper_id)[-1].operation_type == "undo"

    redone = redo_paper_operation(session, undone.paper_id)
    assert redone.version == 4
    assert [item.question_id for item in redone.preview.items] == replaced_ids

    restored = restore_paper_version(session, redone.paper_id, original.paper_id)
    assert restored.version == 5
    assert [item.question_id for item in restored.preview.items] == original_ids
