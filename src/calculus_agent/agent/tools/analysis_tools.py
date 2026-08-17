"""Read-only paper analysis and persisted, non-executable adjustment plans."""

from collections import Counter, defaultdict
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from calculus_agent.models import AdjustmentPlanRecord, KnowledgeNode, Paper, PaperBlueprintRecord, PaperItem, PaperOperationHistory, Question, QuestionKnowledgeLink, QuestionProfile
from calculus_agent.papers.workflow import _clone_version, _state_snapshot, validate_paper
from calculus_agent.question_types import canonical_question_type


class PaperSummary(BaseModel):
    score_total: float = 0
    question_count: int = 0
    question_type_distribution: dict[str, int] = Field(default_factory=dict)
    difficulty_distribution: dict[str, int] = Field(default_factory=dict)
    knowledge_distribution: dict[str, int] = Field(default_factory=dict)


class PaperAnalysisResult(PaperSummary):
    ok: bool
    paper_id: str
    warnings: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    blocking_errors: list[str] = Field(default_factory=list)


class KnowledgePreference(BaseModel):
    node: str
    direction: Literal["more", "less"]


class AdjustmentOperation(BaseModel):
    type: Literal["replace_question", "remove_question", "change_score", "add_question"]
    position: int = Field(gt=0)
    section: str | None = None
    old_question_id: str | None = None
    new_question_id: str | None = None
    score_before: float
    score_after: float
    status: Literal["resolved", "blocked"] = "resolved"

class AdjustmentPlan(BaseModel):
    plan_id: str
    paper_id: str
    base_paper_version_id: str
    operations: list[AdjustmentOperation] = Field(default_factory=list)
    before_summary: PaperSummary
    after_summary: PaperSummary
    satisfied_constraints: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blocking_errors: list[str] = Field(default_factory=list)
    status: Literal["pending", "blocked", "applied", "stale", "failed"]
    created_at: datetime


class PaperAdjustmentPreview(BaseModel):
    ok: bool
    paper_id: str
    plan: AdjustmentPlan | None = None
    current_structure: dict[str, int] = Field(default_factory=dict)
    requested_question_type_changes: dict[str, int] = Field(default_factory=dict)
    requested_remove_positions: list[int] = Field(default_factory=list)
    requested_total_score: float | None = None
    knowledge_preferences: list[KnowledgePreference] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blocking_errors: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)


class AdjustmentPlanFreshness(BaseModel):
    ok: bool
    plan_id: str
    blocking_errors: list[str] = Field(default_factory=list)


class ConfirmAdjustmentResult(BaseModel):
    ok: bool
    plan_id: str
    new_version_id: str | None = None
    blocking_errors: list[str] = Field(default_factory=list)


def _profiles(session: Session) -> dict[str, int]:
    latest = select(QuestionProfile.question_id, func.max(QuestionProfile.profile_version).label("version")).where(QuestionProfile.profile_status == "approved").group_by(QuestionProfile.question_id).subquery()
    return dict(session.execute(select(QuestionProfile.question_id, QuestionProfile.difficulty).join(latest, (QuestionProfile.question_id == latest.c.question_id) & (QuestionProfile.profile_version == latest.c.version))).all())


def _knowledge(session: Session) -> tuple[dict[str, set[str]], dict[str, str]]:
    links = defaultdict(set)
    for question_id, node_id in session.execute(select(QuestionKnowledgeLink.question_id, QuestionKnowledgeLink.knowledge_node_id)):
        links[question_id].add(node_id)
    return links, dict(session.execute(select(KnowledgeNode.id, KnowledgeNode.name)).all())


def _summary(items: list[PaperItem], profiles: dict[str, int], knowledge: dict[str, set[str]], names: dict[str, str]) -> PaperSummary:
    return PaperSummary(score_total=sum(item.score for item in items), question_count=len(items), question_type_distribution=dict(Counter(canonical_question_type(item.section) for item in items)), difficulty_distribution=dict(Counter(str(profiles[item.question_id]) for item in items if item.question_id in profiles)), knowledge_distribution=dict(Counter(names.get(node, node) for item in items for node in knowledge[item.question_id])))


def analyze_paper(session: Session, *, paper_id: str) -> PaperAnalysisResult:
    paper = session.get(Paper, paper_id)
    if paper is None:
        return PaperAnalysisResult(ok=False, paper_id=paper_id, blocking_errors=["paper_not_found"])
    items = list(session.scalars(select(PaperItem).where(PaperItem.paper_id == paper_id).order_by(PaperItem.position)).all())
    profiles = _profiles(session)
    knowledge, names = _knowledge(session)
    summary = _summary(items, profiles, knowledge, names)
    warnings = []
    if len(summary.difficulty_distribution) == 1 and items:
        warnings.append("difficulty_distribution_single_level")
    if not profiles and items:
        warnings.append("question_difficulty_missing")
    return PaperAnalysisResult(ok=True, paper_id=paper_id, **summary.model_dump(), warnings=warnings)


def _scope_ids(session: Session, version: Paper) -> set[str]:
    from calculus_agent.models import PaperBlueprintRecord
    record = session.get(PaperBlueprintRecord, version.blueprint_id)
    return set((record.blueprint_json if record else {}).get("_agent_metadata", {}).get("scope_node_ids", []))


def _build_type_operations(session: Session, *, items: list[PaperItem], profiles: dict[str, int], knowledge: dict[str, set[str]], scope_ids: set[str], changes: dict[str, int]) -> tuple[list[AdjustmentOperation], list[str]]:
    errors: list[str] = []
    if sum(changes.values()) != 0:
        return [], ["question_count_change_not_balanced"]
    occupied = {item.question_id for item in items}
    candidates = list(session.scalars(select(Question).where(Question.review_status == "approved", Question.is_active.is_(True), Question.knowledge_match_status == "current", Question.id.in_(profiles))).all())
    operations: list[AdjustmentOperation] = []
    for source_type, delta in changes.items():
        if delta >= 0:
            continue
        source = [item for item in items if canonical_question_type(item.section) == canonical_question_type(source_type) and item.position not in {op.position for op in operations}]
        if len(source) < -delta:
            errors.append("insufficient_source_question_type")
            continue
        operations.extend(AdjustmentOperation(type="replace_question", position=item.position, old_question_id=item.question_id, score_before=item.score, score_after=item.score) for item in source[: -delta])
    targets = [(canonical_question_type(question_type), delta) for question_type, delta in changes.items() if delta > 0]
    pending_ops = iter(operations)
    for target_type, amount in targets:
        for _ in range(amount):
            try:
                operation = next(pending_ops)
            except StopIteration:
                errors.append("question_type_change_not_balanced")
                break
            matches = [question for question in candidates if question.id not in occupied and canonical_question_type(question.question_type) == target_type and knowledge[question.id].intersection(scope_ids)]
            if not matches:
                operation.status = "blocked"
                errors.append("replacement_candidate_not_found")
                continue
            matches.sort(key=lambda question: question.id)
            operation.new_question_id = matches[0].id
            occupied.add(matches[0].id)
    return operations, errors


def _after_items(items: list[PaperItem], operations: list[AdjustmentOperation], question_types: dict[str, str]) -> list[PaperItem]:
    by_position = {operation.position: operation for operation in operations}
    result = []
    for item in items:
        operation = by_position.get(item.position)
        if operation and operation.type == "remove_question":
            continue
        if operation and operation.type == "replace_question" and operation.new_question_id:
            result.append(item.__class__(paper_id=item.paper_id, question_id=operation.new_question_id, section=canonical_question_type(question_types[operation.new_question_id]), position=item.position, score=operation.score_after, locked=item.locked))
        elif operation and operation.type == "change_score":
            result.append(item.__class__(paper_id=item.paper_id, question_id=item.question_id, section=item.section, position=item.position, score=operation.score_after, locked=item.locked))
        else:
            result.append(
                item.__class__(
                    paper_id=item.paper_id,
                    question_id=item.question_id,
                    section=item.section,
                    position=item.position,
                    score=item.score,
                    locked=item.locked,
                )
            )
    for position, item in enumerate(result, 1):
        item.position = position
    return result


def _build_removal_operations(
    items: list[PaperItem],
    *,
    remove_positions: list[int],
    target_total_score: float | None,
) -> tuple[list[AdjustmentOperation], list[str]]:
    """Remove explicit positions; rebalance only when a target total is explicit."""
    by_position = {item.position: item for item in items}
    positions = list(dict.fromkeys(remove_positions))
    errors: list[str] = []
    if len(positions) != len(remove_positions):
        errors.append("duplicate_remove_position")
    if any(position not in by_position for position in positions):
        errors.append("remove_position_not_found")
    if len(positions) >= len(items):
        errors.append("cannot_remove_all_questions")
    if target_total_score is not None and target_total_score <= 0:
        errors.append("target_total_score_invalid")
    if errors:
        return [], errors
    removed = set(positions)
    operations = [
        AdjustmentOperation(
            type="remove_question", position=position,
            old_question_id=by_position[position].question_id,
            score_before=by_position[position].score, score_after=0,
        )
        for position in positions
    ]
    remaining = [item for item in items if item.position not in removed]
    if target_total_score is None:
        return operations, []

    difference = target_total_score - sum(item.score for item in remaining)
    if abs(difference) < 1e-9:
        return operations, []
    preferred = ["计算题", "证明题", "填空题", "选择题"]
    groups: dict[str, list[PaperItem]] = defaultdict(list)
    for item in remaining:
        groups[canonical_question_type(item.section)].append(item)
    ordered_types = sorted(groups, key=lambda name: preferred.index(name) if name in preferred else len(preferred))
    for question_type in ordered_types:
        group = groups[question_type]
        delta_each = difference / len(group)
        new_scores = [item.score + delta_each for item in group]
        if all(score > 0 and abs(score * 2 - round(score * 2)) < 1e-9 for score in new_scores):
            operations.extend(
                AdjustmentOperation(
                    type="change_score", position=item.position,
                    old_question_id=item.question_id,
                    score_before=item.score, score_after=new_score,
                )
                for item, new_score in zip(group, new_scores)
            )
            return operations, []
    return operations, ["score_rebalance_ambiguous"]


def validate_adjustment_operations(
    session: Session,
    *,
    version: Paper,
    items: list[PaperItem],
    operations: list[AdjustmentOperation],
    require_source_match: bool,
) -> list[str]:
    profiles = _profiles(session)
    knowledge, _names = _knowledge(session)
    scope_ids = _scope_ids(session, version)
    by_position = {item.position: item for item in items}
    errors: list[str] = []

    replacements = {
        operation.position: operation.new_question_id
        for operation in operations
        if operation.type == "replace_question"
    }
    removed = {
        operation.position
        for operation in operations
        if operation.type == "remove_question"
    }
    additions = [
        operation.new_question_id
        for operation in operations
        if operation.type == "add_question" and operation.new_question_id
    ]
    final_ids = [
        replacements.get(item.position, item.question_id)
        for item in items
        if item.position not in removed
    ] + additions

    if len(final_ids) != len(set(final_ids)):
        errors.append("duplicate_question_id")

    for operation in operations:
        if operation.type == "add_question":
            addition = (
                session.get(Question, operation.new_question_id)
                if operation.new_question_id
                else None
            )
            if operation.status != "resolved":
                errors.append("unresolved_adjustment_operation")
            elif operation.position < 1 or operation.position > len(items) + 1:
                errors.append("add_question_position_invalid")
            elif operation.score_after <= 0:
                errors.append("adjustment_score_invalid")
            elif addition is None:
                errors.append("add_question_invalid")
            elif (
                addition.review_status != "approved"
                or not addition.is_active
                or addition.knowledge_match_status != "current"
                or addition.id not in profiles
            ):
                errors.append("add_question_unavailable")
            elif not knowledge[addition.id].intersection(scope_ids):
                errors.append("add_question_out_of_scope")
            elif (
                operation.section is None
                or canonical_question_type(addition.question_type)
                != canonical_question_type(operation.section)
            ):
                errors.append("add_question_type_mismatch")
            continue

        source = by_position.get(operation.position)
        if operation.type in {"remove_question", "change_score"}:
            if source is None:
                errors.append("unresolved_adjustment_operation")
            elif require_source_match and source.question_id != operation.old_question_id:
                errors.append("operation_source_changed")
            elif operation.type == "change_score" and operation.score_after <= 0:
                errors.append("adjustment_score_invalid")
            continue

        replacement = (
            session.get(Question, operation.new_question_id)
            if operation.new_question_id else None
        )
        if operation.status != "resolved" or source is None:
            errors.append("unresolved_adjustment_operation")
        elif require_source_match and source.question_id != operation.old_question_id:
            errors.append("operation_source_changed")
        elif replacement is None or replacement.id == source.question_id:
            errors.append("replacement_question_invalid")
        elif not (
            replacement.review_status == "approved"
            and replacement.is_active
            and replacement.knowledge_match_status == "current"
        ) or replacement.id not in profiles:
            errors.append("replacement_question_unavailable")
        elif not knowledge[replacement.id].intersection(scope_ids):
            errors.append("replacement_question_out_of_scope")

    return list(dict.fromkeys(errors))

def preview_adjust_paper(session: Session, *, paper_id: str, knowledge_preferences: list[KnowledgePreference] | None = None, question_type_changes: dict[str, int] | None = None, remove_positions: list[int] | None = None, target_total_score: float | None = None) -> PaperAdjustmentPreview:
    version = session.get(Paper, paper_id)
    analysis = analyze_paper(session, paper_id=paper_id)
    if version is None or not analysis.ok:
        return PaperAdjustmentPreview(ok=False, paper_id=paper_id, blocking_errors=analysis.blocking_errors or ["paper_not_found"])
    items = list(session.scalars(select(PaperItem).where(PaperItem.paper_id == paper_id).order_by(PaperItem.position)).all())
    profiles = _profiles(session)
    knowledge, names = _knowledge(session)
    changes = question_type_changes or {}
    positions = remove_positions or []
    requested_total = target_total_score
    if positions or target_total_score is not None:
        if changes:
            operations, errors = [], ["mixed_adjustment_modes_not_supported"]
        else:
            operations, errors = _build_removal_operations(
                items,
                remove_positions=positions,
                target_total_score=target_total_score,
            )
    else:
        operations, errors = _build_type_operations(session, items=items, profiles=profiles, knowledge=knowledge, scope_ids=_scope_ids(session, version), changes=changes)
    question_types = dict(session.execute(select(Question.id, Question.question_type)).all())
    planned_items = _after_items(items, operations, question_types)
    after = _summary(planned_items, profiles, knowledge, names)
    if (
        target_total_score is not None
        and abs(after.score_total - target_total_score) > 1e-9
    ):
        errors.append("target_total_score_not_satisfied")
    if not positions and after.question_count != analysis.question_count:
        errors.append("question_count_changed")
    errors.extend(validate_adjustment_operations(session, version=version, items=items, operations=operations, require_source_match=False))
    errors = list(dict.fromkeys(errors))
    satisfied = []
    if not errors:
        satisfied = [
            "scope_preserved",
            "no_duplicate_question_ids",
            "approved_active_current_profile",
        ]
        if target_total_score is not None:
            satisfied.append("target_total_score_satisfied")
        elif positions:
            satisfied.append("removed_score_not_redistributed")
        satisfied.append(
            "question_count_preserved"
            if not positions
            else "requested_questions_removed"
        )
    status: Literal["pending", "blocked"] = "blocked" if errors else "pending"
    record = AdjustmentPlanRecord(paper_id=version.root_paper_id or version.id, base_paper_version_id=version.id, operations_json=[operation.model_dump(mode="json") for operation in operations], before_summary_json=analysis.model_dump(include={"score_total", "question_count", "question_type_distribution", "difficulty_distribution", "knowledge_distribution"}), after_summary_json=after.model_dump(mode="json"), satisfied_constraints_json=satisfied, warnings_json=["adjustment_preview_only"], blocking_errors_json=errors, status=status)
    session.add(record)
    session.flush()
    plan = AdjustmentPlan(plan_id=record.id, paper_id=record.paper_id, base_paper_version_id=record.base_paper_version_id, operations=[AdjustmentOperation.model_validate(value) for value in record.operations_json], before_summary=PaperSummary.model_validate(record.before_summary_json), after_summary=PaperSummary.model_validate(record.after_summary_json), satisfied_constraints=record.satisfied_constraints_json, warnings=record.warnings_json, blocking_errors=record.blocking_errors_json, status=record.status, created_at=record.created_at)
    questions = (["删除题目后，当前各题型无法按0.5分粒度自动平衡到目标总分。请明确希望调整哪一类题目的每题分值。"] if "score_rebalance_ambiguous" in errors else [])
    return PaperAdjustmentPreview(ok=not errors, paper_id=paper_id, plan=plan, current_structure=analysis.question_type_distribution, requested_question_type_changes=changes, requested_remove_positions=positions, requested_total_score=requested_total, knowledge_preferences=knowledge_preferences or [], warnings=plan.warnings, blocking_errors=errors, clarification_questions=questions)


def validate_adjustment_plan_freshness(session: Session, *, plan_id: str, current_version_id: str) -> AdjustmentPlanFreshness:
    plan = session.get(AdjustmentPlanRecord, plan_id)
    if plan is None:
        return AdjustmentPlanFreshness(ok=False, plan_id=plan_id, blocking_errors=["adjustment_plan_not_found"])
    if plan.base_paper_version_id != current_version_id:
        return AdjustmentPlanFreshness(ok=False, plan_id=plan_id, blocking_errors=["stale_adjustment_plan"])
    if plan.status != "pending":
        return AdjustmentPlanFreshness(ok=False, plan_id=plan_id, blocking_errors=["adjustment_plan_not_executable"])
    return AdjustmentPlanFreshness(ok=True, plan_id=plan_id)


def confirm_adjust_paper(
    session: Session,
    *,
    plan_id: str,
    paper_id: str,
    current_version_id: str,
) -> ConfirmAdjustmentResult:
    plan = session.get(AdjustmentPlanRecord, plan_id)
    if plan is None:
        return ConfirmAdjustmentResult(
            ok=False, plan_id=plan_id,
            blocking_errors=["adjustment_plan_not_found"],
        )
    if plan.status == "applied":
        return ConfirmAdjustmentResult(
            ok=False, plan_id=plan_id,
            blocking_errors=["adjustment_plan_already_applied"],
        )
    if plan.status != "pending":
        return ConfirmAdjustmentResult(
            ok=False, plan_id=plan_id,
            blocking_errors=["adjustment_plan_not_executable"],
        )

    current = session.get(Paper, current_version_id)
    requested = session.get(Paper, paper_id)
    if (
        current is None
        or requested is None
        or (current.root_paper_id or current.id) != plan.paper_id
        or (requested.root_paper_id or requested.id) != plan.paper_id
    ):
        return ConfirmAdjustmentResult(
            ok=False, plan_id=plan_id,
            blocking_errors=["adjustment_plan_paper_mismatch"],
        )

    if plan.base_paper_version_id != current_version_id:
        plan.status = "stale"
        plan.blocking_errors_json = ["stale_adjustment_plan"]
        session.flush()
        return ConfirmAdjustmentResult(
            ok=False, plan_id=plan_id,
            blocking_errors=["stale_adjustment_plan"],
        )

    operations = [
        AdjustmentOperation.model_validate(value)
        for value in plan.operations_json
    ]
    items = list(session.scalars(
        select(PaperItem)
        .where(PaperItem.paper_id == current.id)
        .order_by(PaperItem.position)
    ).all())

    errors = validate_adjustment_operations(
        session,
        version=current,
        items=items,
        operations=operations,
        require_source_match=True,
    )
    if errors:
        plan.status = "failed"
        plan.blocking_errors_json = list(dict.fromkeys(errors))
        session.flush()
        return ConfirmAdjustmentResult(
            ok=False, plan_id=plan_id,
            blocking_errors=plan.blocking_errors_json,
        )

    try:
        with session.begin_nested():
            child, clones = _clone_version(session, current, items)
            clone_by_position = {
                item.position: item for _old_id, item in clones
            }
            additions_by_position: dict[int, list[PaperItem]] = defaultdict(list)
            removed_positions: set[int] = set()

            for add_index, operation in enumerate(operations, 1):
                if operation.type == "add_question":
                    question = session.get(Question, operation.new_question_id)
                    if question is None:
                        raise ValueError("add_question_invalid")
                    added = PaperItem(
                        paper_id=child.id,
                        question_id=question.id,
                        section=canonical_question_type(
                            operation.section or question.question_type
                        ),
                        position=-(100000 + add_index),
                        score=operation.score_after,
                        locked=False,
                    )
                    session.add(added)
                    additions_by_position[operation.position].append(added)
                    continue

                clone = clone_by_position[operation.position]
                if operation.type == "remove_question":
                    removed_positions.add(operation.position)
                    session.delete(clone)
                elif operation.type == "change_score":
                    clone.score = operation.score_after
                elif operation.type == "replace_question":
                    replacement = session.get(
                        Question, operation.new_question_id
                    )
                    if replacement is None:
                        raise ValueError("replacement_question_invalid")
                    clone.question_id = replacement.id
                    clone.section = canonical_question_type(
                        replacement.question_type
                    )
                    clone.score = operation.score_after
                else:
                    raise ValueError("unsupported_adjustment_operation")
            session.flush()

            final: list[PaperItem] = []
            for source_position in range(1, len(items) + 2):
                final.extend(additions_by_position.get(source_position, []))
                if (
                    source_position <= len(items)
                    and source_position not in removed_positions
                ):
                    final.append(clone_by_position[source_position])

            for temp_position, item in enumerate(final, 1):
                item.position = -temp_position
            session.flush()

            for position, item in enumerate(final, 1):
                item.position = position
            session.flush()

            profiles = _profiles(session)
            knowledge, names = _knowledge(session)
            after = _summary(final, profiles, knowledge, names)
            expected = PaperSummary.model_validate(plan.after_summary_json)
            if (
                abs(after.score_total - expected.score_total) > 1e-9
                or after.question_count != expected.question_count
            ):
                raise ValueError("adjustment_final_validation_failed")

            child.total_score = after.score_total

            source_blueprint = session.get(
                PaperBlueprintRecord, current.blueprint_id
            )
            if source_blueprint is None:
                raise ValueError("adjustment_source_blueprint_missing")

            final_ids = {item.question_id for item in final}
            grouped: dict[str, list[PaperItem]] = defaultdict(list)
            for item in final:
                grouped[canonical_question_type(item.section)].append(item)

            uniform_sections = all(
                all(
                    abs(item.score - group[0].score) < 1e-9
                    for item in group
                )
                for group in grouped.values()
            )

            blueprint_json = dict(source_blueprint.blueprint_json)
            blueprint_json.update({
                "total_questions": after.question_count,
                "total_score": round(after.score_total),
                "question_type_counts": after.question_type_distribution,
                "sections": [
                    {
                        "question_type": question_type,
                        "count": len(group),
                        "score_per_question": group[0].score,
                        "total_score": sum(item.score for item in group),
                    }
                    for question_type, group in grouped.items()
                ] if uniform_sections else [],
                "score_overrides": (
                    {}
                    if uniform_sections
                    else {item.question_id: item.score for item in final}
                ),
                "locked_question_ids": [
                    question_id
                    for question_id in blueprint_json.get(
                        "locked_question_ids", []
                    )
                    if question_id in final_ids
                ],
                "manual_question_ids": [
                    question_id
                    for question_id in blueprint_json.get(
                        "manual_question_ids", []
                    )
                    if question_id in final_ids
                ],
                "question_order": (
                    [item.question_id for item in final]
                    if blueprint_json.get("question_order")
                    else []
                ),
            })

            snapshot = PaperBlueprintRecord(
                title=source_blueprint.title,
                blueprint_json=blueprint_json,
                status="used",
            )
            session.add(snapshot)
            session.flush()

            child.blueprint_id = snapshot.id
            child.status = "validating"
            child.validation_status = "pending"
            report = validate_paper(session, child.id)
            if not report.passed:
                raise ValueError("adjustment_blueprint_validation_failed")

            session.add(PaperOperationHistory(
                root_paper_id=child.root_paper_id or child.id,
                source_paper_id=current.id,
                result_paper_id=child.id,
                operation_type="adjust_paper",
                operations_json=[
                    operation.model_dump(mode="json")
                    for operation in operations
                ],
                before_state_json=_state_snapshot(session, current),
                after_state_json=_state_snapshot(session, child),
            ))
            plan.status = "applied"
            plan.applied_version_id = child.id
            session.flush()

    except Exception:
        session.refresh(plan)
        plan.status = "failed"
        plan.blocking_errors_json = ["adjustment_persistence_failed"]
        session.flush()
        return ConfirmAdjustmentResult(
            ok=False, plan_id=plan_id,
            blocking_errors=plan.blocking_errors_json,
        )

    return ConfirmAdjustmentResult(
        ok=True, plan_id=plan_id,
        new_version_id=child.id,
    )

