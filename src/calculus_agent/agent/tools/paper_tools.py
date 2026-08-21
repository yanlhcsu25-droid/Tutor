"""Deterministic execution boundaries for fallback and LLM-structured generation."""

from collections import Counter
from math import isclose
import secrets
from typing import Literal

from pydantic import BaseModel, Field, model_validator
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
from calculus_agent.generation_diagnosis import (
    GenerationDiagnosis,
    RecoveryAction,
    decide_recovery,
    diagnose_generation_error,
    diagnose_generation_failure,
)
from calculus_agent.knowledge.classification import current_taxonomy_knowledge_nodes
from calculus_agent.application.scope_resolution import (
    resolve_deterministic_scope_labels,
)
from calculus_agent.knowledge.normalization import normalize_name
from calculus_agent.models import CurriculumNode, KnowledgeAlias, KnowledgeNode
from calculus_agent.papers.selector import compose_paper_with_evidence
from calculus_agent.papers.persistence import create_paper_draft
from calculus_agent.papers.workflow import validate_paper
from calculus_agent.question_types import (
    ALLOWED_QUESTION_TYPES,
    canonical_question_type,
)
from calculus_agent.questions.chapter_assignment import (
    resolve_chapter_reference,
    resolve_scope_chapter_ids,
    scope_labels_are_whole_chapters,
)
from calculus_agent.schemas import (
    KnowledgeQuota,
    PaperBlueprint,
    SectionRequirement,
    ValidationReportRead,
)


# CP-SAT accepts a signed 32-bit seed. Keep generation inside the positive
# range so persisted seeds are easy to inspect/debug.
_MAX_GENERATION_SEED = 2_147_483_647


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
    diagnosis: GenerationDiagnosis | None = None
    recovery_action: RecoveryAction | None = None

    @model_validator(mode="after")
    def attach_recovery_action(self) -> "GeneratePaperToolResult":
        if self.diagnosis is not None and self.recovery_action is None:
            object.__setattr__(self, "recovery_action", decide_recovery(self.diagnosis))
        return self


def _technical_generation_failure(exc: BaseException) -> GeneratePaperToolResult:
    return GeneratePaperToolResult(
        ok=False,
        blocking_errors=["technical_failure"],
        diagnosis=diagnose_generation_error(
            "technical_failure",
            technical_error=exc,
        ),
    )


def _knowledge_ids_under_curriculum_node(
    curriculum: list[CurriculumNode],
    knowledge: list[KnowledgeNode],
    root_id: str,
) -> list[str]:
    """Return knowledge IDs attached to a curriculum node and its descendants."""
    selected_curriculum: set[str] = set()
    pending = [root_id]

    while pending:
        current = pending.pop()
        if current in selected_curriculum:
            continue
        selected_curriculum.add(current)
        pending.extend(
            node.id
            for node in curriculum
            if node.parent_id == current
        )

    return [
        node.id
        for node in knowledge
        if node.curriculum_node_id in selected_curriculum
    ]


def _scope_node_ids(
    session: Session,
    labels: list[str],
) -> tuple[list[str], list[str]]:
    """Resolve teacher-facing scope labels without accepting model-invented IDs."""
    if not labels:
        return [], []

    deterministic = resolve_deterministic_scope_labels(session, labels)
    if not deterministic.ok:
        return [], ["scope_not_found"]
    labels = deterministic.validated_scope_names

    curriculum = list(session.scalars(select(CurriculumNode)))
    knowledge = list(session.scalars(select(KnowledgeNode)))
    resolved: set[str] = set()

    for label in labels:
        # Whole-chapter labels must share the same canonical resolver used by
        # curriculum / question-bank inspection. In particular, an inspection
        # output such as "第一章 函数与极限" is a display label and must remain a
        # valid downstream generation scope.
        chapter = resolve_chapter_reference(
            session,
            label=label,
        )
        if chapter is not None:
            matches = _knowledge_ids_under_curriculum_node(
                curriculum,
                knowledge,
                chapter.id,
            )
            if not matches:
                return [], ["scope_not_found"]
            resolved.update(matches)
            continue

        normalized = normalize_name(label)
        curriculum_matches = [
            node
            for node in curriculum
            if normalize_name(node.title) == normalized
        ]

        if len(curriculum_matches) > 1:
            return [], ["scope_ambiguous"]

        if curriculum_matches:
            matches = _knowledge_ids_under_curriculum_node(
                curriculum,
                knowledge,
                curriculum_matches[0].id,
            )
            if not matches:
                return [], ["scope_not_found"]
            resolved.update(matches)
            continue

        knowledge_matches = [
            node
            for node in knowledge
            if (
                normalize_name(node.name) == normalized
                or node.normalized_name == normalized
            )
        ]

        if len(knowledge_matches) > 1:
            return [], ["scope_ambiguous"]

        if knowledge_matches:
            resolved.add(knowledge_matches[0].id)
            continue

        # Keep the legacy resolver only for non-chapter labels such as explicit
        # section references. Whole chapters were already handled above.
        placeholder = PaperGenerationRequest(
            blueprint=PaperBlueprint(
                title="scope resolver",
                total_questions=1,
                total_score=1,
                question_type_counts={"计算题": 1},
                # Explicit seed keeps this resolver-only placeholder deterministic.
                seed=42,
            ),
            constraints=GenerationConstraints(scope=[label]),
        )
        legacy_resolved, errors = resolve_generation_scope(
            session,
            placeholder,
        )
        if errors or legacy_resolved is None:
            return [], [
                "scope_ambiguous"
                if "ambiguous_scope" in errors
                else "scope_not_found"
            ]

        resolved.update(legacy_resolved.constraints.scope_node_ids)

    return sorted(resolved), []


def _chapter_title(
    curriculum_by_id: dict,
    curriculum_node_id: str | None,
) -> str | None:
    """Climb a knowledge node's curriculum link to the owning chapter title."""
    current = curriculum_node_id

    while current is not None:
        node = curriculum_by_id.get(current)
        if node is None:
            return None
        if node.node_type == "chapter":
            return node.title
        current = node.parent_id

    return None


def _knowledge_preferences(
    session: Session,
    labels: list[str],
    scope_ids: list[str] | None = None,
    scope_labels: list[str] | None = None,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Resolve teacher knowledge preferences against the taxonomy.

    Outcomes:
      - resolved
      - knowledge_unknown
      - knowledge_ambiguous
      - knowledge_scope_conflict
      - knowledge_scope_uncertain

    Repeated imports may create duplicate KnowledgeNode rows with the same name
    under the same chapter. They represent one concept and are collapsed before
    ambiguity is evaluated.
    """
    nodes = current_taxonomy_knowledge_nodes(session)
    aliases = list(session.scalars(select(KnowledgeAlias)))
    valid_node_ids = {node.id for node in nodes}

    alias_nodes: dict[str, set[str]] = {}
    for alias in aliases:
        if alias.node_id not in valid_node_ids:
            continue
        alias_nodes.setdefault(
            alias.normalized_alias,
            set(),
        ).add(alias.node_id)

    curriculum_by_id = {
        node.id: node
        for node in session.scalars(select(CurriculumNode))
    }

    # scope_ids are knowledge-node ids resolved from the requested scope.
    # Convert them back to curriculum ancestry for preference validation.
    scope_curriculum_ids: set[str] = set()
    nodes_by_id = {node.id: node for node in nodes}

    for knowledge_id in scope_ids or []:
        linked = nodes_by_id.get(knowledge_id)
        current = (
            linked.curriculum_node_id
            if linked is not None
            else None
        )

        while current is not None:
            scope_curriculum_ids.add(current)
            parent = curriculum_by_id.get(current)
            current = (
                parent.parent_id
                if parent is not None
                else None
            )

    scope_text = (
        "、".join(scope_labels)
        if scope_labels
        else "当前章节"
    )

    def ancestry_in_scope(node: KnowledgeNode) -> bool:
        if node.curriculum_node_id is None:
            return False

        current = node.curriculum_node_id
        while current is not None:
            if current in scope_curriculum_ids:
                return True
            parent = curriculum_by_id.get(current)
            current = (
                parent.parent_id
                if parent is not None
                else None
            )

        return False

    preferred_names: list[str] = []
    preferred_ids: list[str] = []
    errors: list[str] = []
    clarifications: list[str] = []

    for label in labels:
        normalized = normalize_name(label)

        matches = {
            node.id
            for node in nodes
            if (
                normalize_name(node.name) == normalized
                or node.normalized_name == normalized
            )
        }
        matches.update(alias_nodes.get(normalized, set()))

        if not matches:
            errors.append("knowledge_unknown")
            clarifications.append(
                f"未能在知识点库中识别“{label}”，"
                "请确认名称是否正确，或换一种更具体的表述。"
            )
            continue

        match_nodes = [
            node
            for node in nodes
            if node.id in matches
        ]

        deduplicated: list[KnowledgeNode] = []
        seen_keys: set[tuple[str, str | None]] = set()

        for node in match_nodes:
            key = (
                node.name,
                _chapter_title(
                    curriculum_by_id,
                    node.curriculum_node_id,
                ),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduplicated.append(node)

        if len(deduplicated) > 1:
            scoped = [
                node
                for node in deduplicated
                if ancestry_in_scope(node)
            ]

            if len(scoped) == 1:
                chosen = scoped[0]
            else:
                errors.append("knowledge_ambiguous")
                clarifications.append(
                    f"“{label}”对应多个不同章节的知识点，无法确定唯一目标，"
                    "请补充更具体的名称（例如带上所属章节）。"
                )
                continue
        else:
            chosen = deduplicated[0]

        if chosen.curriculum_node_id is None:
            errors.append("knowledge_scope_uncertain")
            clarifications.append(
                f"“{label}”已识别为知识点，但当前知识点库中没有明确的章节归属，"
                f"无法确认它是否属于{scope_text}。"
                f"请确认是否仍希望将它作为{scope_text}的重点知识点。"
            )
            continue

        if (
            scope_curriculum_ids
            and not ancestry_in_scope(chosen)
        ):
            chapter_title = (
                _chapter_title(
                    curriculum_by_id,
                    chosen.curriculum_node_id,
                )
                or "其他章节"
            )
            errors.append("knowledge_scope_conflict")
            clarifications.append(
                f"当前选择的是{scope_text}，但“{label}”属于"
                f"{chapter_title}范围，不在当前章节内。"
                f"请确认：1）将章节改为{chapter_title}；"
                f"2）保留{scope_text}并重新选择重点知识点。"
            )
            continue

        if chosen.id not in preferred_ids:
            preferred_ids.append(chosen.id)
            preferred_names.append(chosen.name)

    if errors:
        return [], [], errors, clarifications

    return (
        list(dict.fromkeys(preferred_names)),
        list(dict.fromkeys(preferred_ids)),
        [],
        [],
    )


def resolve_advisory_knowledge_preferences(
    session: Session,
    labels: list[str],
    *,
    scope_labels: list[str],
) -> tuple[list[str], list[str]]:
    """Resolve soft TeachingDesign knowledge without turning prose into a blocker."""
    scope_ids, scope_errors = _scope_node_ids(session, scope_labels)
    if scope_errors:
        # Scope validation remains enforced by the normal generation compiler.
        return [], [f"advisory_knowledge_unresolved:{label}" for label in labels]

    resolved: list[str] = []
    warnings: list[str] = []
    for label in dict.fromkeys(labels):
        names, _ids, errors, _questions = _knowledge_preferences(
            session, [label], scope_ids, scope_labels
        )
        if errors:
            warnings.append(f"advisory_knowledge_unresolved:{label}")
        else:
            resolved.extend(names)
    return list(dict.fromkeys(resolved)), warnings


def _resolved_knowledge_priority_weights(
    raw: dict[str, int],
    resolved_names: list[str],
) -> dict[str, int]:
    result: dict[str, int] = {}
    for resolved in resolved_names:
        normalized = normalize_name(resolved)
        for source, weight in raw.items():
            if normalize_name(source) == normalized:
                result[resolved] = int(weight)
                break
    return result


def _difficulty(
    level: str | None,
) -> tuple[list[int], list[int], list[int]]:
    return {
        "easy": ([1, 2, 3], [1, 2], [3]),
        "normal": ([2, 3, 4], [3], [2, 4]),
        "hard": ([3, 4, 5], [4, 5], [3]),
    }[level or "normal"]


def build_structured_generation_request(
    session: Session,
    request: GeneratePaperInput,
) -> tuple[
    PaperGenerationRequest | None,
    list[str],
    list[str],
    list[str],
]:
    """Build executable schemas from validated LLM tool arguments."""
    paper_type = (
        "homework"
        if request.paper_type == "chapter_exercise"
        else (request.paper_type or "chapter_test")
    )
    scopes = list(dict.fromkeys(request.scope_names or []))

    if (
        paper_type in {"chapter_test", "homework", "midterm"}
        and not scopes
    ):
        question = (
            "请确认本次期中考试的知识范围。"
            if paper_type == "midterm"
            else "请确认要组卷的章节或知识范围。"
        )
        return (
            None,
            [],
            [
                "missing_exam_scope"
                if paper_type == "midterm"
                else "scope_not_found"
            ],
            [question],
        )

    if (
        paper_type == "final"
        and not request.difficulty_ratio
    ):
        return (
            None,
            [],
            ["missing_difficulty_ratio"],
            ["请确认本次期末考试的难度占比。"],
        )

    scope_ids, scope_errors = _scope_node_ids(
        session,
        scopes,
    )
    if scope_errors:
        return (
            None,
            [],
            scope_errors,
            ["请确认组卷范围名称是否与当前课程目录一致。"],
        )

    # Chapter ownership is the hard scope source of truth.
    scope_chapter_ids = resolve_scope_chapter_ids(
        session,
        scopes,
        scope_ids,
    )
    if scopes and not scope_chapter_ids:
        return (
            None,
            [],
            ["scope_not_found"],
            ["请确认组卷范围名称是否与当前课程目录一致。"],
        )

    # Whole-chapter generation does NOT use knowledge links as chapter
    # membership. Section/knowledge scopes may still use them as a refinement.
    scope_knowledge_node_ids = (
        []
        if scope_labels_are_whole_chapters(
            session,
            scopes,
        )
        else scope_ids
    )

    (
        preferred_names,
        preferred_ids,
        knowledge_errors,
        knowledge_questions,
    ) = _knowledge_preferences(
        session,
        request.knowledge_preferences or [],
        scope_ids,
        scopes,
    )

    if knowledge_errors:
        return (
            None,
            [],
            knowledge_errors,
            knowledge_questions,
        )

    (
        required_names,
        required_ids,
        required_errors,
        required_questions,
    ) = _knowledge_preferences(
        session,
        request.required_knowledge_names or [],
        scope_ids,
        scopes,
    )

    if required_errors:
        return (
            None,
            [],
            required_errors,
            required_questions,
        )

    combined_preferred_names = list(
        dict.fromkeys([*required_names, *preferred_names])
    )
    combined_preferred_ids = list(
        dict.fromkeys([*required_ids, *preferred_ids])
    )
    knowledge_quotas = [
        KnowledgeQuota(name=name, count=1)
        for name in required_names
    ]
    total_score_provenance = request.constraint_provenance.get(
        "total_score"
    )
    total_score_teacher_explicit = bool(
        total_score_provenance is None
        or (
            total_score_provenance.get("teacher_explicit", False)
            if isinstance(total_score_provenance, dict)
            else total_score_provenance.teacher_explicit
        )
    )
    resolved_priority_weights = (
        _resolved_knowledge_priority_weights(
            request.knowledge_priority_weights or {},
            combined_preferred_names,
        )
    )

    warnings: list[str] = []
    requirements = (
        request.question_type_requirements
        or []
    )

    if required_names and not requirements:
        # Generic hard-knowledge-coverage plan.  A trusted caller can specify
        # required_knowledge_names without inventing a question-type split.
        required_count = len(required_names)
        desired_count = (
            request.question_count
            if request.question_count is not None
            else required_count
        )

        if desired_count < required_count:
            return (
                None,
                [],
                ["question_count_below_required_knowledge_coverage"],
                [
                    f"当前要求至少覆盖{required_count}个知识点，"
                    f"但总题数只有{desired_count}题。"
                    "请增加题量或减少必覆盖知识点。"
                ],
            )

        default_total_score = max(
            desired_count,
            min(100, desired_count * 10),
        )

        blueprint = PaperBlueprint(
            title=(
                f"{scopes[0] if len(scopes) == 1 else '高等数学'}"
                "专项巩固练习"
            ),
            total_questions=desired_count,
            total_score=(
                request.total_score
                or default_total_score
            ),
            soft_knowledge_preferences=list(
                dict.fromkeys(
                    [
                        *preferred_names,
                        *required_names,
                    ]
                )
            ),
            # New papers receive the actual fresh seed only at execution.
            seed=None,
        )

    elif requirements:
        canonical = [
            canonical_question_type(
                item.question_type
            )
            for item in requirements
        ]

        if any(
            question_type
            not in ALLOWED_QUESTION_TYPES
            for question_type in canonical
        ):
            return (
                None,
                [],
                ["question_type_invalid"],
                ["请使用当前系统支持的题型名称。"],
            )

        if len(set(canonical)) != len(canonical):
            return (
                None,
                [],
                ["question_type_invalid"],
                ["同一题型请只配置一次。"],
            )

        derived_count = sum(
            item.count
            for item in requirements
        )

        # Complete per-type distribution is authoritative.
        request = request.model_copy(
            update={
                "question_count": derived_count
            }
        )

        section_values: list[
            SectionRequirement
        ] = []
        score_completeness: list[bool] = []

        for item, question_type in zip(
            requirements,
            canonical,
        ):
            score_each = item.score_each
            section_total = item.total_score

            if (
                score_each is not None
                and section_total is not None
                and not isclose(
                    item.count * score_each,
                    section_total,
                )
            ):
                return (
                    None,
                    [],
                    ["score_total_mismatch"],
                    [
                        f"{question_type}的题数、"
                        "每题分值与小计不一致。"
                    ],
                )

            if (
                score_each is None
                and section_total is not None
            ):
                score_each = (
                    section_total / item.count
                )

            if (
                score_each is not None
                and section_total is None
            ):
                section_total = (
                    item.count * score_each
                )

            score_completeness.append(
                score_each is not None
            )

            if (
                score_each is not None
                and section_total is not None
            ):
                section_values.append(
                    SectionRequirement(
                        question_type=question_type,
                        count=item.count,
                        score_per_question=score_each,
                        total_score=section_total,
                    )
                )

        if (
            any(score_completeness)
            and not all(score_completeness)
        ):
            return (
                None,
                [],
                ["score_total_mismatch"],
                [
                    "请补充所有题型的每题分值，"
                    "或全部省略分值。"
                ],
            )

        if section_values:
            derived_score = sum(
                section.total_score
                for section in section_values
            )

            if (
                request.total_score is not None
                and not isclose(request.total_score, derived_score)
            ):
                if total_score_teacher_explicit:
                    return (
                        None,
                        [],
                        ["score_total_mismatch"],
                        [
                            f"各题型分值合计为"
                            f"{derived_score:g}分，"
                            f"与总分{request.total_score}"
                            "分不一致。"
                        ],
                    )
                # A TeachingDesign/system value is a recommendation, not a
                # teacher-owned hard total. Preserve the explicit structure and
                # derive the effective total deterministically.
                request = request.model_copy(update={
                    "total_score": round(derived_score),
                })

            blueprint = PaperBlueprint(
                title=(
                    f"{scopes[0] if scopes else '高等数学'}"
                    "测试卷"
                ),
                total_questions=derived_count,
                total_score=round(
                    derived_score
                ),
                sections=section_values,
                knowledge_quotas=knowledge_quotas,
                soft_knowledge_preferences=(
                    combined_preferred_names
                ),
                # New papers receive the actual fresh seed only at execution.
                seed=None,
            )
        else:
            blueprint = PaperBlueprint(
                title=(
                    f"{scopes[0] if scopes else '高等数学'}"
                    "测试卷"
                ),
                total_questions=derived_count,
                total_score=(
                    request.total_score
                    or (
                        50
                        if paper_type == "homework"
                        else 100
                    )
                ),
                question_type_counts=dict(
                    zip(
                        canonical,
                        [
                            item.count
                            for item in requirements
                        ],
                    )
                ),
                knowledge_quotas=knowledge_quotas,
                soft_knowledge_preferences=(
                    combined_preferred_names
                ),
                seed=None,
            )

    else:
        requirement = RequirementBlueprint(
            paper_type=paper_type,
            scope=scopes,
            total_score=(
                request.total_score
                if request.total_score
                is not None
                else (
                    None
                    if paper_type == "homework"
                    else 100
                )
            ),
            difficulty=(
                request.difficulty_level
                or "normal"
            ),
            preferences=RequirementPreferences(
                difficulty_ratio=(
                    request.difficulty_ratio
                    or {}
                )
            ),
        )

        built = build_paper_blueprint(
            requirement
        )

        if (
            not built.ok
            or built.paper_blueprint
            is None
        ):
            return (
                None,
                built.warnings,
                built.blocking_errors,
                ["请补充完整的组卷要求。"],
            )

        blueprint = (
            built.paper_blueprint.model_copy(
                update={
                    "knowledge_quotas": knowledge_quotas,
                    "soft_knowledge_preferences": (
                        combined_preferred_names
                    ),
                    "title": (
                        f"{scopes[0] if scopes else '高等数学'}"
                        "测试卷"
                    ),
                    # build_paper_blueprint may still come from older paths
                    # that do not explicitly set seed.
                    "seed": None,
                }
            )
        )

        warnings.extend(built.warnings)

        if (
            request.question_count
            is not None
            and request.question_count
            != blueprint.total_questions
        ):
            return (
                None,
                warnings,
                ["question_count_mismatch"],
                [
                    "当前默认题型模板与指定总题数不一致，"
                    "请同时说明各题型数量。"
                ],
            )

    # Hard required-knowledge coverage compiled deterministically.
    # The existing selector / CP-SAT path already enforces knowledge_quotas.
    if required_names:
        if blueprint.total_questions < len(required_names):
            return (
                None,
                warnings,
                ["question_count_below_required_knowledge_coverage"],
                [
                    f"当前要求至少覆盖{len(required_names)}个知识点，"
                    f"但蓝图只有{blueprint.total_questions}题。"
                    "请增加题量或减少必覆盖知识点。"
                ],
            )

        blueprint = blueprint.model_copy(
            update={
                "knowledge_quotas": [
                    KnowledgeQuota(
                        name=name,
                        count=1,
                    )
                    for name in required_names
                ],
                "strict_knowledge": True,
                "soft_knowledge_preferences": list(
                    dict.fromkeys(
                        [
                            *preferred_names,
                            *required_names,
                        ]
                    )
                ),
            }
        )

    (
        allowed,
        preferred,
        fallback,
    ) = _difficulty(
        request.difficulty_level
    )

    if request.difficulty_preference:
        warnings.append(
            "difficulty_progression_is_soft"
        )

    if request.diversity_preference:
        warnings.append(
            "diversity_preference_is_soft"
        )

    constraints = GenerationConstraints(
        scope=scopes,
        scope_chapter_ids=scope_chapter_ids,
        scope_node_ids=scope_ids,
        scope_knowledge_node_ids=(
            scope_knowledge_node_ids
        ),
        allowed_difficulty_levels=allowed,
        preferred_difficulty_levels=preferred,
        fallback_difficulty_levels=fallback,
        preferred_knowledge_node_ids=(
            combined_preferred_ids
        ),
        knowledge_priority_weights=(
            resolved_priority_weights
        ),
        target_duration_min=request.target_duration_min,
        duration_tolerance_min=(
            request.duration_tolerance_min
            if request.duration_tolerance_min is not None
            else 5
        ),
        ability_weights=(request.ability_weights or {}),
        constraint_provenance=request.constraint_provenance,
        audience=request.audience,
        difficulty_preference_text=(
            request.difficulty_preference
        ),
        diversity_preference=(
            request.diversity_preference
        ),
    )

    return (
        PaperGenerationRequest(
            blueprint=blueprint,
            constraints=constraints,
        ),
        warnings,
        [],
        [],
    )


def _with_execution_seed(
    request: PaperGenerationRequest,
) -> PaperGenerationRequest:
    """Assign one fresh seed only when a new paper is actually generated.

    Production:
        seed=None -> fresh cryptographic random 32-bit-positive seed

    Reproduction / eval:
        explicit seed (for example 42) -> preserved exactly

    The selected seed is carried in request.blueprint and is therefore persisted
    by create_paper_draft(), allowing the exact paper to be reproduced later.
    """
    if request.blueprint.seed is not None:
        return request

    seed = 1 + secrets.randbelow(
        _MAX_GENERATION_SEED - 1
    )

    return request.model_copy(
        update={
            "blueprint": (
                request.blueprint.model_copy(
                    update={"seed": seed}
                )
            )
        }
    )


def _execute_generation_request(
    session: Session,
    request: PaperGenerationRequest,
    *,
    warnings: list[str],
) -> GeneratePaperToolResult:
    # Important boundary:
    # preview/plan construction stays deterministic and does not consume
    # randomness. A fresh seed is created only at actual generation time.
    request = _with_execution_seed(
        request
    )

    preview, selection_evidence = compose_paper_with_evidence(
        session,
        request,
    )

    type_counts = Counter(
        item.question_type
        for item in preview.items
    )

    unsatisfied = [
        f"constraint_unsatisfied:{warning}"
        for warning in preview.warnings
    ]

    selection_diagnosis = diagnose_generation_failure(
        blueprint=request.blueprint,
        evidence=selection_evidence,
        preview=preview,
    )

    if not preview.feasible:
        return GeneratePaperToolResult(
            ok=False,
            warnings=(
                warnings
                + preview.warnings
            ),
            blocking_errors=[
                "insufficient_candidates",
                *unsatisfied,
            ],
        
            diagnosis=selection_diagnosis,)

    persisted = create_paper_draft(
        session,
        preview,
        request.blueprint,
        generation_constraints=(
            request.constraints
        ),
    )

    if not persisted.ok:
        persistence_errors = persisted.blocking_errors or [
            "paper_persistence_failed"
        ]
        return GeneratePaperToolResult(
            ok=False,
            warnings=(
                warnings
                + persisted.warnings
            ),
            blocking_errors=(
                persistence_errors
            ),
            diagnosis=diagnose_generation_error(persistence_errors[0]),
        )

    validation_report = validate_paper(
        session,
        str(persisted.paper_id),
    )

    validation_diagnosis = (
        diagnose_generation_failure(
            blueprint=request.blueprint,
            evidence=selection_evidence,
            preview=preview,
            validation_report=validation_report,
        )
        if not validation_report.passed
        else None
    )

    validation_status: Literal[
        "passed",
        "failed",
    ] = (
        "passed"
        if validation_report.passed
        else "failed"
    )

    result_warnings = list(warnings)
    if not validation_report.passed:
        result_warnings.append(
            "paper_validation_failed"
        )

    return GeneratePaperToolResult(
        ok=validation_report.passed,
        paper_id=persisted.paper_id,
        version_id=persisted.version_id,
        warnings=result_warnings,
        blocking_errors=(
            []
            if validation_report.passed
            else ["paper_validation_failed"]
        ),
        summary=PaperSummary(
            total_questions=len(
                preview.items
            ),
            total_score=(
                preview.total_score
            ),
            question_type_counts=dict(
                type_counts
            ),
        ),
        validation_status=validation_status,
        validation_report=validation_report,
    
        diagnosis=validation_diagnosis,)


def generate_paper_from_input(
    session: Session,
    request: GeneratePaperInput,
    *,
    excluded_question_ids: list[str] | None = None,
) -> GeneratePaperToolResult:
    (
        generation_request,
        warnings,
        errors,
        questions,
    ) = build_structured_generation_request(
        session,
        request,
    )

    if generation_request is None:
        diagnosis = diagnose_generation_error(
            errors[0] if errors else "unknown_generation_failure"
        )
        return GeneratePaperToolResult(
            ok=False,
            warnings=warnings,
            blocking_errors=errors,
            needs_clarification=bool(
                questions
            ),
            clarification_questions=(
                questions
            ),
            diagnosis=diagnosis,
        )

    if excluded_question_ids:
        blueprint = generation_request.blueprint.model_copy(
            update={
                "excluded_question_ids": list(dict.fromkeys([
                    *generation_request.blueprint.excluded_question_ids,
                    *excluded_question_ids,
                ])),
            }
        )
        generation_request = generation_request.model_copy(
            update={"blueprint": blueprint}
        )

    try:
        return _execute_generation_request(
            session,
            generation_request,
            warnings=warnings,
        )
    except Exception as exc:
        return _technical_generation_failure(exc)


def generate_paper_tool(
    session: Session,
    requirement: RequirementBlueprint,
) -> GeneratePaperToolResult:
    """Run deterministic adapter, scope resolution, selection and persistence.

    The paper selector remains deterministic for a given explicit seed.
    Production generation uses a fresh seed only when the blueprint has
    seed=None.
    """
    blueprint_result = (
        build_paper_blueprint(
            requirement
        )
    )

    if not blueprint_result.ok:
        diagnosis = diagnose_generation_error(
            blueprint_result.blocking_errors[0]
            if blueprint_result.blocking_errors
            else "invalid_paper_blueprint"
        )
        return GeneratePaperToolResult(
            ok=False,
            warnings=(
                blueprint_result.warnings
            ),
            blocking_errors=(
                blueprint_result
                .blocking_errors
            ),
            diagnosis=diagnosis,
        )

    request, warnings, errors = (
        build_generation_request(
            requirement,
            blueprint_result,
        )
    )

    if request is None:
        diagnosis = diagnose_generation_error(
            errors[0] if errors else "invalid_paper_blueprint"
        )
        return GeneratePaperToolResult(
            ok=False,
            warnings=warnings,
            blocking_errors=errors,
            diagnosis=diagnosis,
        )

    request, scope_errors = (
        resolve_generation_scope(
            session,
            request,
        )
    )

    if (
        scope_errors
        or request is None
    ):
        errors = scope_errors or ["scope_not_found"]
        return GeneratePaperToolResult(
            ok=False,
            warnings=warnings,
            blocking_errors=(
                errors
            ),
            diagnosis=diagnose_generation_error(errors[0]),
        )

    # Legacy tool path must follow the same fresh-seed rule as the structured
    # Teacher Agent path. Do not keep a default 42 that would regenerate the
    # same paper forever.
    request = request.model_copy(
        update={
            "blueprint": (
                request.blueprint.model_copy(
                    update={
                        "seed": (
                            request.blueprint.seed
                        )
                    }
                )
            )
        }
    )

    try:
        return _execute_generation_request(
            session,
            request,
            warnings=warnings,
        )
    except Exception as exc:
        return _technical_generation_failure(exc)
