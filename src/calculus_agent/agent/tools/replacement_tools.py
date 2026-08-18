"""Read-only deterministic single-question replacement recommendations."""

from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from calculus_agent.agent.schemas import ReplacementIntent
from calculus_agent.models import (
    Paper, PaperBlueprintRecord, PaperItem, Question, QuestionDraft,
    PaperOperationHistory, QuestionKnowledgeLink, QuestionProfile,
)
from calculus_agent.questions.eligibility import (
    EXCLUDED_PAPER_SOURCE_NAMES,
)
from calculus_agent.papers.workflow import _clone_version, _state_snapshot
from calculus_agent.question_types import canonical_question_type


class ReplacementQuestionSummary(BaseModel):
    question_id: str
    position: int | None = None
    question_type: str
    score: float
    difficulty: int | None = None
    knowledge_point_ids: list[str] = Field(default_factory=list)


class ReplacementConstraintCheck(BaseModel):
    scope_preserved: bool
    question_type_preserved: bool
    score_preserved: bool
    knowledge_point_preserved: bool
    difficulty_direction_satisfied: bool


class ReplacementDryRunResult(BaseModel):
    ok: bool
    paper_id: str
    version_id: str
    action: Literal["replace_question"] = "replace_question"
    target_position: int
    current_question: ReplacementQuestionSummary | None = None
    recommended_question: ReplacementQuestionSummary | None = None
    constraints: ReplacementConstraintCheck | None = None
    candidate_count: int = 0
    candidate_stats: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    blocking_errors: list[str] = Field(default_factory=list)


def replacement_requires_target_knowledge(session: Session, *, version_id: str, intent: ReplacementIntent) -> bool:
    """Hard product guard for "exclude current topic" requests.

    The rule is intentionally narrower than any avoid-list: it applies only
    when the avoid list intersects the target question's current knowledge.
    """
    if intent.target_knowledge_node_ids or not intent.avoid_knowledge_node_ids:
        return False
    item = session.scalar(select(PaperItem).where(PaperItem.paper_id == version_id, PaperItem.position == intent.target_position))
    if item is None:
        return False
    current = set(session.scalars(select(QuestionKnowledgeLink.knowledge_node_id).where(QuestionKnowledgeLink.question_id == item.question_id)))
    return bool(current.intersection(intent.avoid_knowledge_node_ids))


class ApplyReplacementResult(BaseModel):
    ok: bool
    paper_id: str | None = None
    source_version_id: str | None = None
    new_version_id: str | None = None
    target_position: int | None = None
    old_question_id: str | None = None
    new_question_id: str | None = None
    warnings: list[str] = Field(default_factory=list)
    blocking_errors: list[str] = Field(default_factory=list)


def _latest_profiles(session: Session) -> dict[str, int]:
    latest = select(QuestionProfile.question_id, func.max(QuestionProfile.profile_version).label("version")).where(
        QuestionProfile.profile_status == "approved"
    ).group_by(QuestionProfile.question_id).subquery()
    return dict(session.execute(select(QuestionProfile.question_id, QuestionProfile.difficulty).join(
        latest, (QuestionProfile.question_id == latest.c.question_id) & (QuestionProfile.profile_version == latest.c.version)
    )).all())


def dry_run_replace_question(
    session: Session, *, paper_id: str, version_id: str, intent: ReplacementIntent
) -> ReplacementDryRunResult:
    base = dict(paper_id=paper_id, version_id=version_id, target_position=intent.target_position)
    if intent.need_clarification:
        return ReplacementDryRunResult(ok=False, blocking_errors=["invalid_replacement_intent"], **base)
    paper = session.get(Paper, paper_id)
    version = session.get(Paper, version_id)
    if paper is None:
        return ReplacementDryRunResult(ok=False, blocking_errors=["paper_not_found"], **base)
    if version is None:
        return ReplacementDryRunResult(ok=False, blocking_errors=["version_not_found"], **base)
    if (paper.root_paper_id or paper.id) != (version.root_paper_id or version.id):
        return ReplacementDryRunResult(ok=False, blocking_errors=["paper_version_mismatch"], **base)
    items = list(session.scalars(select(PaperItem).where(PaperItem.paper_id == version.id).order_by(PaperItem.position)).all())
    target = next((item for item in items if item.position == intent.target_position), None)
    if target is None:
        return ReplacementDryRunResult(ok=False, blocking_errors=["invalid_question_position"], **base)
    profiles = _latest_profiles(session)
    current_difficulty = profiles.get(target.question_id)
    if current_difficulty is None:
        return ReplacementDryRunResult(ok=False, blocking_errors=["current_question_difficulty_missing"], **base)
    if intent.target_difficulty is not None and intent.target_difficulty == current_difficulty:
        return ReplacementDryRunResult(ok=False, blocking_errors=["target_difficulty_same_as_current"], **base)
    if intent.target_difficulty is None and intent.difficulty_direction == "easier" and current_difficulty == 1:
        return ReplacementDryRunResult(ok=False, blocking_errors=["no_easier_candidate"], **base)
    if intent.target_difficulty is None and intent.difficulty_direction == "harder" and current_difficulty == 5:
        return ReplacementDryRunResult(ok=False, blocking_errors=["no_harder_candidate"], **base)
    record = session.get(PaperBlueprintRecord, version.blueprint_id)
    metadata = (record.blueprint_json if record else {}).get("_agent_metadata", {})
    scope_chapter_ids = set(metadata.get("scope_chapter_ids", []))
    scope_knowledge_node_ids = set(
        metadata.get("scope_knowledge_node_ids", [])
    )
    legacy_scope_ids = set(metadata.get("scope_node_ids", []))
    if not scope_chapter_ids and not legacy_scope_ids:
        return ReplacementDryRunResult(
            ok=False, blocking_errors=["invalid_scope"], **base
        )
    knowledge_by_question: dict[str, set[str]] = defaultdict(set)
    for question_id, knowledge_id in session.execute(select(QuestionKnowledgeLink.question_id, QuestionKnowledgeLink.knowledge_node_id)):
        knowledge_by_question[question_id].add(knowledge_id)
    candidates = list(session.scalars(select(Question).join(
        QuestionDraft,
        QuestionDraft.id == Question.draft_id,
    ).where(
        Question.review_status == "approved", Question.is_active.is_(True),
        Question.knowledge_match_status == "current",
        Question.id.in_(profiles),
        QuestionDraft.source_name.not_in(EXCLUDED_PAPER_SOURCE_NAMES),
    )).all())
    stats = {"approved_current": len(candidates)}
    candidates = [
        question for question in candidates
        if canonical_question_type(question.question_type) == target.section
    ]
    stats["same_canonical_question_type"] = len(candidates)
    if scope_chapter_ids:
        candidates = [
            q for q in candidates
            if q.curriculum_chapter_id in scope_chapter_ids
        ]
        if scope_knowledge_node_ids:
            candidates = [
                q for q in candidates
                if knowledge_by_question[q.id].intersection(
                    scope_knowledge_node_ids
                )
            ]
    else:
        # Compatibility for paper versions created before chapter metadata.
        candidates = [
            q for q in candidates
            if knowledge_by_question[q.id].intersection(legacy_scope_ids)
        ]
    stats["in_scope"] = len(candidates)
    candidates = [q for q in candidates if q.id not in {item.question_id for item in items}]
    stats["not_already_in_paper"] = len(candidates)
    if intent.target_knowledge_node_ids:
        candidates = [q for q in candidates if set(intent.target_knowledge_node_ids).issubset(knowledge_by_question[q.id])]
    if intent.avoid_knowledge_node_ids:
        candidates = [q for q in candidates if not set(intent.avoid_knowledge_node_ids).intersection(knowledge_by_question[q.id])]
    if intent.avoid_similarity_with_question_numbers:
        references = {item.question_id for item in items if item.position in intent.avoid_similarity_with_question_numbers}
        reference_knowledge = set().union(*(knowledge_by_question[qid] for qid in references)) if references else set()
        candidates = [q for q in candidates if not knowledge_by_question[q.id].intersection(reference_knowledge)]
    stats["knowledge_constraints"] = len(candidates)

    def overlap(question: Question) -> int:
        return len(knowledge_by_question[question.id] & knowledge_by_question[target.question_id])

    if intent.target_difficulty is not None:
        candidates = [q for q in candidates if profiles[q.id] == intent.target_difficulty]
        unavailable = "target_difficulty_candidate_not_found"

        def key(question: Question):
            return (-overlap(question), question.id)
    elif intent.difficulty_direction == "easier":
        candidates = [q for q in candidates if profiles[q.id] < current_difficulty]
        unavailable = "no_easier_candidate"

        def key(question: Question):
            return (current_difficulty - profiles[question.id], -overlap(question), question.id)
    elif intent.difficulty_direction == "harder":
        candidates = [q for q in candidates if profiles[q.id] > current_difficulty]
        unavailable = "no_harder_candidate"

        def key(question: Question):
            return (profiles[question.id] - current_difficulty, -overlap(question), question.id)
    else:
        candidates = [q for q in candidates if profiles[q.id] == current_difficulty]
        unavailable = "same_difficulty_candidate_not_found"

        def key(question: Question):
            return (-overlap(question), question.id)
    stats["difficulty_constraint"] = len(candidates)
    if not candidates:
        return ReplacementDryRunResult(ok=False, blocking_errors=[unavailable], candidate_stats=stats, **base)
    candidates.sort(key=key)
    selected = candidates[0]
    current_knowledge = knowledge_by_question[target.question_id]
    selected_knowledge = knowledge_by_question[selected.id]
    required_knowledge = set(intent.target_knowledge_node_ids)
    knowledge_preserved = (
        required_knowledge.issubset(selected_knowledge)
        if required_knowledge
        else bool(current_knowledge.intersection(selected_knowledge))
    )
    warnings = [] if knowledge_preserved else ["knowledge_point_constraint_relaxed"]
    current_summary = ReplacementQuestionSummary(question_id=target.question_id, position=target.position, question_type=target.section, score=target.score, difficulty=current_difficulty, knowledge_point_ids=sorted(current_knowledge))
    replacement_summary = ReplacementQuestionSummary(question_id=selected.id, question_type=target.section, score=target.score, difficulty=profiles[selected.id], knowledge_point_ids=sorted(selected_knowledge))
    return ReplacementDryRunResult(
        ok=True, current_question=current_summary, recommended_question=replacement_summary,
        candidate_count=len(candidates), candidate_stats=stats, warnings=warnings,
        constraints=ReplacementConstraintCheck(
            scope_preserved=True, question_type_preserved=True, score_preserved=True,
            knowledge_point_preserved=knowledge_preserved,
            difficulty_direction_satisfied=True,
        ), **base,
    )


def run_replacement_dry_run(
    session: Session, *, paper_id: str, version_id: str, user_message: str
) -> ReplacementDryRunResult:
    """Parse and execute the read-only dry-run in one explicit-paper context."""
    from calculus_agent.agent.replacement_parser import parse_replacement_intent

    try:
        intent = parse_replacement_intent(user_message)
    except ValueError:
        return ReplacementDryRunResult(
            ok=False, paper_id=paper_id, version_id=version_id, target_position=0,
            blocking_errors=["invalid_replacement_intent"],
        )
    return dry_run_replace_question(
        session, paper_id=paper_id, version_id=version_id, intent=intent
    )


def apply_question_replacement(
    session: Session,
    *,
    paper_id: str,
    source_version_id: str,
    target_position: int,
    replacement_question_id: str,
    difficulty_direction: Literal["easier", "harder", "same"] | None,
    target_difficulty: int | None = None,
    preserve_knowledge_points: bool = False,
    required_knowledge_node_ids: list[str] | None = None,
) -> ApplyReplacementResult:
    """Revalidate and persist a one-item child version; never mutate source.

    When ``preserve_knowledge_points`` is true, the required IDs are the
    preview-time confirmation contract.  Confirmation re-reads current DB
    links and refuses mutation if either the source question or replacement
    no longer satisfies that contract.
    """
    base = dict(
        paper_id=paper_id, source_version_id=source_version_id,
        target_position=target_position, new_question_id=replacement_question_id,
    )
    paper = session.get(Paper, paper_id)
    source = session.get(Paper, source_version_id)
    if paper is None:
        return ApplyReplacementResult(ok=False, blocking_errors=["paper_not_found"], **base)
    if source is None:
        return ApplyReplacementResult(ok=False, blocking_errors=["version_not_found"], **base)
    root_id = paper.root_paper_id or paper.id
    if root_id != (source.root_paper_id or source.id):
        return ApplyReplacementResult(ok=False, blocking_errors=["paper_version_mismatch"], **base)
    newest_version = session.scalar(select(func.max(Paper.version)).where(Paper.root_paper_id == root_id))
    if newest_version is not None and source.version != newest_version:
        return ApplyReplacementResult(ok=False, blocking_errors=["source_version_not_current"], **base)
    items = list(session.scalars(select(PaperItem).where(PaperItem.paper_id == source.id).order_by(PaperItem.position)).all())
    target = next((item for item in items if item.position == target_position), None)
    if target is None:
        return ApplyReplacementResult(ok=False, blocking_errors=["invalid_question_position"], **base)
    replacement = session.get(Question, replacement_question_id)
    if replacement is None:
        return ApplyReplacementResult(ok=False, blocking_errors=["replacement_question_not_found"], **base)
    if not (replacement.review_status == "approved" and replacement.is_active and replacement.knowledge_match_status == "current"):
        return ApplyReplacementResult(ok=False, blocking_errors=["replacement_question_unavailable"], **base)
    if replacement_question_id in {item.question_id for item in items}:
        return ApplyReplacementResult(ok=False, blocking_errors=["replacement_question_already_in_paper"], **base)
    if canonical_question_type(replacement.question_type) != target.section:
        return ApplyReplacementResult(ok=False, blocking_errors=["replacement_question_type_mismatch"], **base)
    record = session.get(PaperBlueprintRecord, source.blueprint_id)
    metadata = (record.blueprint_json if record else {}).get("_agent_metadata", {})
    scope_ids = set(metadata.get("scope_node_ids", []))
    if not scope_ids:
        return ApplyReplacementResult(ok=False, blocking_errors=["paper_scope_metadata_missing"], **base)
    knowledge_by_question: dict[str, set[str]] = defaultdict(set)
    for question_id, knowledge_id in session.execute(select(QuestionKnowledgeLink.question_id, QuestionKnowledgeLink.knowledge_node_id)):
        knowledge_by_question[question_id].add(knowledge_id)
    if not knowledge_by_question[replacement.id].intersection(scope_ids):
        return ApplyReplacementResult(ok=False, blocking_errors=["replacement_question_out_of_scope"], **base)

    if preserve_knowledge_points:
        required_knowledge = set(required_knowledge_node_ids or [])
        if not required_knowledge:
            return ApplyReplacementResult(
                ok=False,
                blocking_errors=["replacement_knowledge_constraint_unverifiable"],
                **base,
            )
        current_target_knowledge = knowledge_by_question[target.question_id]
        current_replacement_knowledge = knowledge_by_question[replacement.id]
        if (
            not required_knowledge.issubset(current_target_knowledge)
            or not required_knowledge.issubset(current_replacement_knowledge)
        ):
            return ApplyReplacementResult(
                ok=False,
                blocking_errors=["replacement_knowledge_constraint_no_longer_valid"],
                **base,
            )

    profiles = _latest_profiles(session)
    current_difficulty = profiles.get(target.question_id)
    replacement_difficulty = profiles.get(replacement.id)
    if current_difficulty is None:
        return ApplyReplacementResult(ok=False, blocking_errors=["current_question_difficulty_missing"], **base)
    if replacement_difficulty is None:
        return ApplyReplacementResult(ok=False, blocking_errors=["replacement_difficulty_no_longer_valid"], **base)
    valid_direction = (
        replacement_difficulty == target_difficulty if target_difficulty is not None
        else replacement_difficulty < current_difficulty if difficulty_direction == "easier"
        else replacement_difficulty > current_difficulty if difficulty_direction == "harder"
        else replacement_difficulty == current_difficulty
    )
    if not valid_direction:
        return ApplyReplacementResult(ok=False, blocking_errors=["replacement_difficulty_no_longer_valid"], **base)
    warnings = []
    if (
        not preserve_knowledge_points
        and not knowledge_by_question[target.question_id].intersection(knowledge_by_question[replacement.id])
    ):
        warnings.append("knowledge_point_constraint_relaxed")
    try:
        with session.begin_nested():
            child, clones = _clone_version(session, source, items)
            replacement_item = next(item for old_id, item in clones if old_id == target.id)
            replacement_item.question_id = replacement.id
            child.status = "draft"
            child.validation_status = "pending"
            session.flush()
            session.add(PaperOperationHistory(
                root_paper_id=child.root_paper_id or child.id,
                source_paper_id=source.id,
                result_paper_id=child.id,
                operation_type="replace_question",
                operations_json=[{
                    "action": "replace_question", "position": target_position,
                    "old_question_id": target.question_id, "new_question_id": replacement.id,
                    "source": "teacher_agent",
                }],
                before_state_json=_state_snapshot(session, source),
                after_state_json=_state_snapshot(session, child),
            ))
            session.flush()
    except Exception:
        return ApplyReplacementResult(ok=False, blocking_errors=["replacement_persistence_failed"], **base)
    return ApplyReplacementResult(
        ok=True, new_version_id=child.id, old_question_id=target.question_id,
        warnings=warnings, **base,
    )
