"""Adapt requirement-level intent to the existing executable PaperBlueprint."""

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.models import CurriculumNode, KnowledgeNode
from calculus_agent.questions.chapter_assignment import (
    resolve_scope_chapter_ids,
    scope_labels_are_whole_chapters,
)
from calculus_agent.schemas import PaperBlueprint, SectionRequirement

from .schemas import (
    GenerationConstraints,
    PaperGenerationRequest,
    RequirementBlueprint,
)


def _scope_number(value: str) -> str:
    if value.isdigit():
        return value
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if value in digits:
        return str(digits[value])
    return value


def _same_scope_code(code: str | None, number: str) -> bool:
    """Accept curriculum imports that retain Chinese or Arabic node codes."""
    return code is not None and _scope_number(code) == number


# This mirrors the application's existing PaperWorkspace initial blueprint.
# Keep this in one place until the application exposes a shared template API.
CHAPTER_TEST_TEMPLATE = (
    ("选择题", 4, 5),
    ("填空题", 2, 10),
    ("计算题", 4, 15),
)


class BlueprintBuildResult(BaseModel):
    ok: bool
    paper_blueprint: PaperBlueprint | None = None
    paper_type: Literal["chapter_test", "homework", "midterm", "final"] | None = None
    resolved_scope: list[str] = Field(default_factory=list)
    resolved_difficulty: str | None = None
    warnings: list[str] = Field(default_factory=list)
    blocking_errors: list[str] = Field(default_factory=list)


def _sections(template: tuple[tuple[str, int, int], ...]) -> list[SectionRequirement]:
    return [
        SectionRequirement(
            question_type=question_type,
            count=count,
            score_per_question=score,
            total_score=count * score,
        )
        for question_type, count, score in template
    ]


def build_paper_blueprint(requirement: RequirementBlueprint) -> BlueprintBuildResult:
    """Build a validated executable blueprint without selecting questions.

    Scope and difficulty are reported explicitly because the current
    ``PaperBlueprint``/selector contract has no fields for either constraint.
    """
    base = dict(
        paper_type=requirement.paper_type,
        resolved_scope=requirement.scope,
        resolved_difficulty=requirement.difficulty,
    )
    if requirement.need_clarification:
        errors = []
        if requirement.paper_type == "midterm" and not requirement.scope:
            errors.append("missing_exam_scope")
        if requirement.paper_type == "final" and not requirement.preferences.difficulty_ratio:
            errors.append("missing_difficulty_ratio")
        return BlueprintBuildResult(ok=False, blocking_errors=errors or ["needs_clarification"], **base)

    if requirement.paper_type == "midterm" and not requirement.scope:
        return BlueprintBuildResult(ok=False, blocking_errors=["missing_exam_scope"], **base)
    if requirement.paper_type == "final" and not requirement.preferences.difficulty_ratio:
        return BlueprintBuildResult(ok=False, blocking_errors=["missing_difficulty_ratio"], **base)
    if not requirement.scope and requirement.paper_type in {"chapter_test", "homework"}:
        return BlueprintBuildResult(ok=False, blocking_errors=["invalid_scope"], **base)

    warnings: list[str] = []
    if requirement.preferences.more_question_types:
        warnings.append("question_type_preference_is_soft")
    if requirement.difficulty != "normal":
        warnings.append("difficulty_preference_approximated")
    if requirement.scope:
        warnings.append("scope_not_enforced_by_existing_paper_blueprint")

    if requirement.paper_type == "homework":
        # Existing PaperBlueprint requires a positive integer total_score.
        # Homework has no requested score, so use 10 points per default item.
        sections = _sections((("计算题", 3, 10), ("证明题", 2, 10)))
        total_score = requirement.total_score or 50
    else:
        sections = _sections(CHAPTER_TEST_TEMPLATE)
        total_score = requirement.total_score or 100
        if total_score != 100:
            warnings.append("custom_total_score_uses_existing_question_structure")

    try:
        blueprint = PaperBlueprint(
            title="高等数学测试卷",
            total_questions=sum(section.count for section in sections),
            total_score=total_score,
            sections=sections,
        )
    except ValueError as exc:
        return BlueprintBuildResult(ok=False, blocking_errors=["invalid_paper_blueprint"], **base)
    return BlueprintBuildResult(ok=True, paper_blueprint=blueprint, warnings=warnings, **base)


def dry_run_requirement(requirement: RequirementBlueprint) -> BlueprintBuildResult:
    """Named dry-run alias; this never calls the paper selector."""
    return build_paper_blueprint(requirement)


def build_generation_request(
    requirement: RequirementBlueprint,
    blueprint_result: BlueprintBuildResult,
) -> tuple[PaperGenerationRequest | None, list[str], list[str]]:
    """Bridge a validated requirement to generation constraints.

    Scope is a hard label constraint. Difficulty is an ordered preference with
    an explicit fallback, and is resolved against QuestionProfile at query time.
    """
    if not blueprint_result.ok or blueprint_result.paper_blueprint is None:
        return None, list(blueprint_result.warnings), list(blueprint_result.blocking_errors)
    difficulty = {
        "easy": ([1, 2, 3], [1, 2], [3]),
        "normal": ([2, 3, 4], [3], [2, 4]),
        "hard": ([3, 4, 5], [4, 5], [3]),
    }[requirement.difficulty]
    constraints = GenerationConstraints(
        scope=requirement.scope,
        allowed_difficulty_levels=difficulty[0],
        preferred_difficulty_levels=difficulty[1],
        fallback_difficulty_levels=difficulty[2],
    )
    warnings = list(blueprint_result.warnings)
    # Scope is now consumed by candidate filtering; this warning is obsolete.
    warnings = [item for item in warnings if item != "scope_not_enforced_by_existing_paper_blueprint"]
    return PaperGenerationRequest(blueprint=blueprint_result.paper_blueprint, constraints=constraints), warnings, []


def resolve_generation_scope(
    session: Session, request: PaperGenerationRequest
) -> tuple[PaperGenerationRequest | None, list[str]]:
    """Resolve human scope labels through the persisted curriculum tree.

    Returns structured error codes rather than silently falling back to all
    questions. The returned node IDs are the curriculum-backed knowledge nodes
    used by the candidate query.
    """
    if not request.constraints.scope:
        return request, []
    nodes = list(session.scalars(select(CurriculumNode)).all())
    by_id = {node.id: node for node in nodes}
    selected: set[str] = set()
    for label in request.constraints.scope:
        parts = label.replace(" ", "")
        chapter_match = __import__("re").fullmatch(r"第([一二三四五六七八九十百0-9]+)章", parts)
        section_match = __import__("re").fullmatch(
            r"第([一二三四五六七八九十百0-9]+)章第([一二三四五六七八九十百0-9]+)节", parts
        )
        section_only = __import__("re").fullmatch(r"第([一二三四五六七八九十百0-9]+)节", parts)
        if not (chapter_match or section_match or section_only):
            return None, ["invalid_scope"]
        number = _scope_number((chapter_match or section_match or section_only).group(1))
        chapter_candidates = [
            node for node in nodes
            if node.node_type == "chapter" and _same_scope_code(node.code, number)
        ]
        if section_only:
            section_candidates = [
                node for node in nodes
                if node.node_type == "section" and _same_scope_code(node.code, number)
            ]
            if len(section_candidates) != 1:
                return None, ["ambiguous_scope" if section_candidates else "invalid_scope"]
            chapter_candidates = [by_id.get(section_candidates[0].parent_id)]
            section_targets = section_candidates
        elif section_match:
            section_number = _scope_number(section_match.group(2))
            if len(chapter_candidates) != 1:
                return None, ["ambiguous_scope" if chapter_candidates else "invalid_scope"]
            section_targets = [
                node for node in nodes
                if node.node_type == "section" and node.parent_id == chapter_candidates[0].id
                and _same_scope_code(node.code, section_number)
            ]
            if len(section_targets) != 1:
                return None, ["ambiguous_scope" if section_targets else "invalid_scope"]
        else:
            section_targets = []
        chapter_candidates = [node for node in chapter_candidates if node is not None]
        if len(chapter_candidates) != 1:
            return None, ["ambiguous_scope" if chapter_candidates else "invalid_scope"]
        roots = chapter_candidates if not section_targets else section_targets
        pending = [node.id for node in roots]
        while pending:
            current = pending.pop()
            selected.add(current)
            children = [node.id for node in nodes if node.parent_id == current]
            pending.extend(child for child in children if child not in selected)
    knowledge_ids = list(session.scalars(
        select(KnowledgeNode.id).where(KnowledgeNode.curriculum_node_id.in_(selected))
    ).all())
    if not knowledge_ids:
        return None, ["invalid_scope"]
    scope_chapter_ids = resolve_scope_chapter_ids(
        session,
        request.constraints.scope,
        knowledge_ids,
    )
    if not scope_chapter_ids:
        return None, ["invalid_scope"]
    constraints = request.constraints.model_copy(update={
        "scope_node_ids": knowledge_ids,
        "scope_chapter_ids": scope_chapter_ids,
        "scope_knowledge_node_ids": (
            [] if scope_labels_are_whole_chapters(
                session, request.constraints.scope
            )
            else knowledge_ids
        ),
    })
    return request.model_copy(update={"constraints": constraints}), []
