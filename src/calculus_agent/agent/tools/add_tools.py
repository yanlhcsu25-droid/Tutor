"""Deterministic add-question preview using the shared AdjustmentPlan contract."""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.models import AdjustmentPlanRecord, Paper, PaperItem, Question, QuestionDraft
from calculus_agent.papers.selector import EXCLUDED_PAPER_SOURCE_NAMES
from calculus_agent.question_types import PAPER_QUESTION_TYPES, canonical_question_type

from .analysis_tools import (
    AdjustmentOperation,
    AdjustmentPlan,
    PaperSummary,
    _knowledge,
    _profiles,
    _scope_ids,
    _summary,
    analyze_paper,
    validate_adjustment_operations,
)


class AddQuestionPreview(BaseModel):
    ok: bool
    paper_id: str
    plan: AdjustmentPlan | None = None
    question_type: str
    score: float | None = None
    selected_question_id: str | None = None
    insert_position: int | None = None
    candidate_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    blocking_errors: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)


def infer_add_score(
    items: list[PaperItem],
    *,
    question_type: str,
    explicit_score: float | None,
) -> tuple[float | None, str | None]:
    if explicit_score is not None:
        if explicit_score <= 0:
            return None, "add_question_score_invalid"
        return float(explicit_score), None

    canonical_type = canonical_question_type(question_type)
    scores = {
        float(item.score)
        for item in items
        if canonical_question_type(item.section) == canonical_type
    }
    if not scores:
        return None, "add_question_score_required"
    if len(scores) > 1:
        return None, "add_question_score_ambiguous"
    return next(iter(scores)), None


def section_insert_position(
    items: list[PaperItem],
    *,
    question_type: str,
) -> int:
    canonical_type = canonical_question_type(question_type)
    order = list(PAPER_QUESTION_TYPES)
    if canonical_type not in order:
        raise ValueError("question_type_invalid")

    same_type = [
        item for item in items
        if canonical_question_type(item.section) == canonical_type
    ]
    if same_type:
        return max(item.position for item in same_type) + 1

    target_index = order.index(canonical_type)
    for item in sorted(items, key=lambda value: value.position):
        item_type = canonical_question_type(item.section)
        if item_type in order and order.index(item_type) > target_index:
            return item.position
    return len(items) + 1


def _planned_items_with_addition(
    items: list[PaperItem],
    *,
    operation: AdjustmentOperation,
) -> list[PaperItem]:
    if (
        operation.type != "add_question"
        or operation.new_question_id is None
        or operation.section is None
    ):
        raise ValueError("invalid_add_operation")

    result: list[PaperItem] = []
    inserted = False

    for item in sorted(items, key=lambda value: value.position):
        if not inserted and item.position == operation.position:
            result.append(
                PaperItem(
                    paper_id=item.paper_id,
                    question_id=operation.new_question_id,
                    section=canonical_question_type(operation.section),
                    position=operation.position,
                    score=operation.score_after,
                    locked=False,
                )
            )
            inserted = True

        result.append(
            PaperItem(
                paper_id=item.paper_id,
                question_id=item.question_id,
                section=item.section,
                position=item.position,
                score=item.score,
                locked=item.locked,
            )
        )

    if not inserted:
        result.append(
            PaperItem(
                paper_id=items[0].paper_id if items else "",
                question_id=operation.new_question_id,
                section=canonical_question_type(operation.section),
                position=operation.position,
                score=operation.score_after,
                locked=False,
            )
        )

    for position, item in enumerate(result, 1):
        item.position = position
    return result


def preview_add_question(
    session: Session,
    *,
    paper_id: str,
    question_type: str,
    score: float | None = None,
) -> AddQuestionPreview:
    version = session.get(Paper, paper_id)
    canonical_type = canonical_question_type(question_type)

    if version is None:
        return AddQuestionPreview(
            ok=False,
            paper_id=paper_id,
            question_type=canonical_type,
            blocking_errors=["paper_not_found"],
        )
    if canonical_type not in PAPER_QUESTION_TYPES:
        return AddQuestionPreview(
            ok=False,
            paper_id=paper_id,
            question_type=canonical_type,
            blocking_errors=["question_type_invalid"],
        )

    items = list(
        session.scalars(
            select(PaperItem)
            .where(PaperItem.paper_id == paper_id)
            .order_by(PaperItem.position)
        ).all()
    )
    analysis = analyze_paper(session, paper_id=paper_id)
    if not analysis.ok:
        return AddQuestionPreview(
            ok=False,
            paper_id=paper_id,
            question_type=canonical_type,
            blocking_errors=analysis.blocking_errors,
        )

    resolved_score, score_error = infer_add_score(
        items,
        question_type=canonical_type,
        explicit_score=score,
    )
    if score_error:
        questions: list[str] = []
        if score_error == "add_question_score_required":
            questions = ["当前试卷没有该题型，请明确新增题目的分值。"]
        elif score_error == "add_question_score_ambiguous":
            questions = ["当前该题型存在多种分值，请明确新增题目的分值。"]
        return AddQuestionPreview(
            ok=False,
            paper_id=paper_id,
            question_type=canonical_type,
            blocking_errors=[score_error],
            clarification_questions=questions,
        )

    scope_ids = _scope_ids(session, version)
    if not scope_ids:
        return AddQuestionPreview(
            ok=False,
            paper_id=paper_id,
            question_type=canonical_type,
            score=resolved_score,
            blocking_errors=["invalid_scope"],
        )

    profiles = _profiles(session)
    knowledge, names = _knowledge(session)
    occupied = {item.question_id for item in items}

    candidates = list(
        session.scalars(
            select(Question)
            .join(QuestionDraft, QuestionDraft.id == Question.draft_id)
            .where(
                Question.review_status == "approved",
                Question.is_active.is_(True),
                Question.knowledge_match_status == "current",
                Question.id.in_(profiles),
                QuestionDraft.source_name.notin_(EXCLUDED_PAPER_SOURCE_NAMES),
            )
        ).all()
    )
    candidates = [
        question
        for question in candidates
        if question.id not in occupied
        and canonical_question_type(question.question_type) == canonical_type
        and bool(knowledge[question.id].intersection(scope_ids))
    ]
    candidates.sort(key=lambda question: question.id)

    if not candidates:
        return AddQuestionPreview(
            ok=False,
            paper_id=paper_id,
            question_type=canonical_type,
            score=resolved_score,
            blocking_errors=["add_question_candidate_not_found"],
        )

    selected = candidates[0]
    insert_position = section_insert_position(items, question_type=canonical_type)
    operation = AdjustmentOperation(
        type="add_question",
        position=insert_position,
        section=canonical_type,
        old_question_id=None,
        new_question_id=selected.id,
        score_before=0,
        score_after=resolved_score,
    )

    errors = validate_adjustment_operations(
        session,
        version=version,
        items=items,
        operations=[operation],
        require_source_match=False,
    )
    errors = list(dict.fromkeys(errors))

    planned_items = _planned_items_with_addition(items, operation=operation)
    after = _summary(planned_items, profiles, knowledge, names)

    satisfied = (
        [
            "question_added",
            "scope_preserved",
            "question_type_preserved",
            "existing_scores_preserved",
            "no_duplicate_question_ids",
            "approved_active_current_profile",
        ]
        if not errors else []
    )
    status = "blocked" if errors else "pending"

    record = AdjustmentPlanRecord(
        paper_id=version.root_paper_id or version.id,
        base_paper_version_id=version.id,
        operations_json=[operation.model_dump(mode="json")],
        before_summary_json=analysis.model_dump(
            include={
                "score_total",
                "question_count",
                "question_type_distribution",
                "difficulty_distribution",
                "knowledge_distribution",
            }
        ),
        after_summary_json=after.model_dump(mode="json"),
        satisfied_constraints_json=satisfied,
        warnings_json=["adjustment_preview_only"],
        blocking_errors_json=errors,
        status=status,
    )
    session.add(record)
    session.flush()

    plan = AdjustmentPlan(
        plan_id=record.id,
        paper_id=record.paper_id,
        base_paper_version_id=record.base_paper_version_id,
        operations=[
            AdjustmentOperation.model_validate(value)
            for value in record.operations_json
        ],
        before_summary=PaperSummary.model_validate(record.before_summary_json),
        after_summary=PaperSummary.model_validate(record.after_summary_json),
        satisfied_constraints=record.satisfied_constraints_json,
        warnings=record.warnings_json,
        blocking_errors=record.blocking_errors_json,
        status=record.status,
        created_at=record.created_at,
    )

    return AddQuestionPreview(
        ok=not errors,
        paper_id=paper_id,
        plan=plan,
        question_type=canonical_type,
        score=resolved_score,
        selected_question_id=selected.id,
        insert_position=insert_position,
        candidate_count=len(candidates),
        warnings=plan.warnings,
        blocking_errors=errors,
    )
