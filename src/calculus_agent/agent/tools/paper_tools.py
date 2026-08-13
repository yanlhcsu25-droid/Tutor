"""Deterministic execution boundaries for fallback and LLM-structured generation."""

from collections import Counter
from math import isclose
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.agent.blueprint_adapter import (
    build_generation_request,
    build_paper_blueprint,
    resolve_generation_scope,
)
from calculus_agent.agent.schemas import (
    GeneratePaperInput,
    GenerationConstraints,
    PaperGenerationRequest,
    RequirementBlueprint,
    RequirementPreferences,
)
from calculus_agent.knowledge.normalization import normalize_name
from calculus_agent.models import CurriculumNode, KnowledgeAlias, KnowledgeNode
from calculus_agent.papers.selector import compose_paper
from calculus_agent.papers.persistence import create_paper_draft
from calculus_agent.papers.workflow import validate_paper
from calculus_agent.question_types import ALLOWED_QUESTION_TYPES, canonical_question_type
from calculus_agent.schemas import (
    PaperBlueprint,
    SectionRequirement,
    ValidationReportRead,
)


class PaperSummary(BaseModel):
    total_questions: int
    total_score: int | float
    question_type_counts: dict[str, int] = Field(default_factory=dict)


class GeneratePaperToolResult(BaseModel):
    ok: bool
    paper_id: int | str | None = None
    version_id: int | str | None = None
    warnings: list[str] = Field(default_factory=list)
    blocking_errors: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    summary: PaperSummary | None = None
    validation_status: Literal["passed", "failed"] | None = None
    validation_report: ValidationReportRead | None = None


def _scope_node_ids(session: Session, labels: list[str]) -> tuple[list[str], list[str]]:
    """Resolve teacher-facing scope labels without accepting model-invented IDs."""
    if not labels:
        return [], []
    curriculum = list(session.scalars(select(CurriculumNode)))
    knowledge = list(session.scalars(select(KnowledgeNode)))
    resolved: set[str] = set()
    for label in labels:
        normalized = normalize_name(label)
        curriculum_matches = [node for node in curriculum if normalize_name(node.title) == normalized]
        if len(curriculum_matches) > 1:
            return [], ["scope_ambiguous"]
        if curriculum_matches:
            selected_curriculum: set[str] = set()
            pending = [curriculum_matches[0].id]
            while pending:
                current = pending.pop()
                if current in selected_curriculum:
                    continue
                selected_curriculum.add(current)
                pending.extend(node.id for node in curriculum if node.parent_id == current)
            matches = [node.id for node in knowledge if node.curriculum_node_id in selected_curriculum]
            if not matches:
                return [], ["scope_not_found"]
            resolved.update(matches)
            continue
        knowledge_matches = [
            node for node in knowledge
            if normalize_name(node.name) == normalized or node.normalized_name == normalized
        ]
        if len(knowledge_matches) > 1:
            return [], ["scope_ambiguous"]
        if knowledge_matches:
            resolved.add(knowledge_matches[0].id)
            continue

        # Reuse the established chapter/section resolver for labels such as 第一章.
        placeholder = PaperGenerationRequest(
            blueprint=PaperBlueprint(
                title="scope resolver",
                total_questions=1,
                total_score=1,
                question_type_counts={"解答题": 1},
            ),
            constraints=GenerationConstraints(scope=[label]),
        )
        legacy_resolved, errors = resolve_generation_scope(session, placeholder)
        if errors or legacy_resolved is None:
            return [], ["scope_ambiguous" if "ambiguous_scope" in errors else "scope_not_found"]
        resolved.update(legacy_resolved.constraints.scope_node_ids)
    return sorted(resolved), []


def _knowledge_preferences(
    session: Session, labels: list[str]
) -> tuple[list[str], list[str], list[str]]:
    nodes = list(session.scalars(select(KnowledgeNode)))
    aliases = list(session.scalars(select(KnowledgeAlias)))
    alias_nodes: dict[str, set[str]] = {}
    for alias in aliases:
        alias_nodes.setdefault(alias.normalized_alias, set()).add(alias.node_id)
    canonical_names: list[str] = []
    node_ids: list[str] = []
    for label in labels:
        normalized = normalize_name(label)
        matches = {
            node.id for node in nodes
            if normalize_name(node.name) == normalized or node.normalized_name == normalized
        }
        matches.update(alias_nodes.get(normalized, set()))
        if not matches:
            return [], [], ["knowledge_not_found"]
        if len(matches) > 1:
            return [], [], ["knowledge_ambiguous"]
        node_id = next(iter(matches))
        node = next(node for node in nodes if node.id == node_id)
        node_ids.append(node.id)
        canonical_names.append(node.name)
    return list(dict.fromkeys(canonical_names)), list(dict.fromkeys(node_ids)), []


def _difficulty(level: str | None) -> tuple[list[int], list[int], list[int]]:
    return {
        "easy": ([1, 2, 3], [1, 2], [3]),
        "normal": ([2, 3, 4], [3], [2, 4]),
        "hard": ([3, 4, 5], [4, 5], [3]),
    }[level or "normal"]


def build_structured_generation_request(
    session: Session, request: GeneratePaperInput
) -> tuple[PaperGenerationRequest | None, list[str], list[str], list[str]]:
    """Build existing executable schemas from validated LLM tool arguments."""
    paper_type = "homework" if request.paper_type == "chapter_exercise" else (request.paper_type or "chapter_test")
    scopes = list(dict.fromkeys(request.scope_names or []))
    if paper_type in {"chapter_test", "homework", "midterm"} and not scopes:
        question = "请确认本次期中考试的知识范围。" if paper_type == "midterm" else "请确认要组卷的章节或知识范围。"
        return None, [], ["missing_exam_scope" if paper_type == "midterm" else "scope_not_found"], [question]
    if paper_type == "final" and not request.difficulty_ratio:
        return None, [], ["missing_difficulty_ratio"], ["请确认本次期末考试的难度占比。"]

    scope_ids, scope_errors = _scope_node_ids(session, scopes)
    if scope_errors:
        return None, [], scope_errors, ["请确认组卷范围名称是否与当前课程目录一致。"]
    preferred_names, preferred_ids, knowledge_errors = _knowledge_preferences(
        session, request.knowledge_preferences or []
    )
    if knowledge_errors:
        return None, [], knowledge_errors, ["请确认希望重点覆盖的知识点名称。"]

    warnings: list[str] = []
    requirements = request.question_type_requirements or []
    if requirements:
        canonical = [canonical_question_type(item.question_type) for item in requirements]
        if any(question_type not in ALLOWED_QUESTION_TYPES for question_type in canonical):
            return None, [], ["question_type_invalid"], ["请使用当前系统支持的题型名称。"]
        if len(set(canonical)) != len(canonical):
            return None, [], ["question_type_invalid"], ["同一题型请只配置一次。"]
        derived_count = sum(item.count for item in requirements)
        if request.question_count is not None and request.question_count != derived_count:
            return None, [], ["question_count_mismatch"], [
                f"题型数量合计为{derived_count}题，与总题数{request.question_count}题不一致。"
            ]

        section_values: list[SectionRequirement] = []
        score_completeness = []
        for item, question_type in zip(requirements, canonical):
            score_each = item.score_each
            section_total = item.total_score
            if score_each is not None and section_total is not None and not isclose(
                item.count * score_each, section_total
            ):
                return None, [], ["score_total_mismatch"], [
                    f"{question_type}的题数、每题分值与小计不一致。"
                ]
            if score_each is None and section_total is not None:
                score_each = section_total / item.count
            if score_each is not None and section_total is None:
                section_total = item.count * score_each
            score_completeness.append(score_each is not None)
            if score_each is not None and section_total is not None:
                section_values.append(SectionRequirement(
                    question_type=question_type,
                    count=item.count,
                    score_per_question=score_each,
                    total_score=section_total,
                ))
        if any(score_completeness) and not all(score_completeness):
            return None, [], ["score_total_mismatch"], ["请补充所有题型的每题分值，或全部省略分值。"]
        if section_values:
            derived_score = sum(section.total_score for section in section_values)
            if request.total_score is not None and not isclose(request.total_score, derived_score):
                return None, [], ["score_total_mismatch"], [
                    f"各题型分值合计为{derived_score:g}分，与总分{request.total_score}分不一致。"
                ]
            blueprint = PaperBlueprint(
                title=f"{scopes[0] if scopes else '高等数学'}测试卷",
                total_questions=derived_count,
                total_score=round(derived_score),
                sections=section_values,
                soft_knowledge_preferences=preferred_names,
            )
        else:
            blueprint = PaperBlueprint(
                title=f"{scopes[0] if scopes else '高等数学'}测试卷",
                total_questions=derived_count,
                total_score=request.total_score or (50 if paper_type == "homework" else 100),
                question_type_counts=dict(zip(canonical, [item.count for item in requirements])),
                soft_knowledge_preferences=preferred_names,
            )
    else:
        requirement = RequirementBlueprint(
            paper_type=paper_type,
            scope=scopes,
            total_score=request.total_score if request.total_score is not None else (None if paper_type == "homework" else 100),
            difficulty=request.difficulty_level or "normal",
            preferences=RequirementPreferences(difficulty_ratio=request.difficulty_ratio or {}),
        )
        built = build_paper_blueprint(requirement)
        if not built.ok or built.paper_blueprint is None:
            return None, built.warnings, built.blocking_errors, ["请补充完整的组卷要求。"]
        blueprint = built.paper_blueprint.model_copy(update={
            "soft_knowledge_preferences": preferred_names,
            "title": f"{scopes[0] if scopes else '高等数学'}测试卷",
        })
        warnings.extend(built.warnings)
        if request.question_count is not None and request.question_count != blueprint.total_questions:
            return None, warnings, ["question_count_mismatch"], [
                "当前默认题型模板与指定总题数不一致，请同时说明各题型数量。"
            ]

    allowed, preferred, fallback = _difficulty(request.difficulty_level)
    if request.difficulty_preference:
        warnings.append("difficulty_progression_is_soft")
    if request.diversity_preference:
        warnings.append("diversity_preference_is_soft")
    constraints = GenerationConstraints(
        scope=scopes,
        scope_node_ids=scope_ids,
        allowed_difficulty_levels=allowed,
        preferred_difficulty_levels=preferred,
        fallback_difficulty_levels=fallback,
        preferred_knowledge_node_ids=preferred_ids,
        audience=request.audience,
        difficulty_preference_text=request.difficulty_preference,
        diversity_preference=request.diversity_preference,
    )
    return PaperGenerationRequest(blueprint=blueprint, constraints=constraints), warnings, [], []


def _execute_generation_request(
    session: Session,
    request: PaperGenerationRequest,
    *,
    warnings: list[str],
) -> GeneratePaperToolResult:
    preview = compose_paper(session, request)
    type_counts = Counter(item.question_type for item in preview.items)
    unsatisfied = [f"constraint_unsatisfied:{warning}" for warning in preview.warnings]
    if not preview.feasible:
        return GeneratePaperToolResult(
            ok=False,
            warnings=warnings + preview.warnings,
            blocking_errors=["insufficient_candidates", *unsatisfied],
        )
    persisted = create_paper_draft(
        session, preview, request.blueprint, generation_constraints=request.constraints
    )
    if not persisted.ok:
        return GeneratePaperToolResult(
            ok=False,
            warnings=warnings + persisted.warnings,
            blocking_errors=persisted.blocking_errors,
        )
    validation_report = validate_paper(session, str(persisted.paper_id))
    validation_status: Literal["passed", "failed"] = (
        "passed" if validation_report.passed else "failed"
    )
    result_warnings = list(warnings)
    if not validation_report.passed:
        result_warnings.append("paper_validation_failed")
    return GeneratePaperToolResult(
        ok=True,
        paper_id=persisted.paper_id,
        version_id=persisted.version_id,
        warnings=result_warnings,
        summary=PaperSummary(
            total_questions=len(preview.items),
            total_score=preview.total_score,
            question_type_counts=dict(type_counts),
        ),
        validation_status=validation_status,
        validation_report=validation_report,
    )


def generate_paper_from_input(
    session: Session, request: GeneratePaperInput
) -> GeneratePaperToolResult:
    generation_request, warnings, errors, questions = build_structured_generation_request(
        session, request
    )
    if generation_request is None:
        return GeneratePaperToolResult(
            ok=False,
            warnings=warnings,
            blocking_errors=errors,
            needs_clarification=bool(questions),
            clarification_questions=questions,
        )
    return _execute_generation_request(session, generation_request, warnings=warnings)


def generate_paper_tool(
    session: Session, requirement: RequirementBlueprint
) -> GeneratePaperToolResult:
    """Run deterministic adapter, constraint resolution, and paper preview.

    The preview remains pure; this tool performs the separate draft persistence
    step and never renders a document.
    """
    blueprint_result = build_paper_blueprint(requirement)
    if not blueprint_result.ok:
        return GeneratePaperToolResult(
            ok=False,
            warnings=blueprint_result.warnings,
            blocking_errors=blueprint_result.blocking_errors,
        )
    request, warnings, errors = build_generation_request(requirement, blueprint_result)
    if request is None:
        return GeneratePaperToolResult(ok=False, warnings=warnings, blocking_errors=errors)
    request, scope_errors = resolve_generation_scope(session, request)
    if scope_errors:
        return GeneratePaperToolResult(
            ok=False, warnings=warnings, blocking_errors=scope_errors
        )
    return _execute_generation_request(session, request, warnings=warnings)
