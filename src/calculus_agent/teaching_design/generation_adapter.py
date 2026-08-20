"""Compile confirmed TeachingDesign into executable generation intent.

This adapter is deliberately schema-only: it does not query the DB, call the
Agent runtime, or select questions. It classifies design semantics into:

- hard constraints: generation must satisfy;
- bounded constraints: hard range around a target;
- soft objectives: optimizer preferences;
- advisory constraints: preserved for explanation/lecture, but not Paper blockers;
- unsupported constraints: semantics that still cannot be represented safely.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from calculus_agent.schemas import ConstraintProvenance

from .schemas import TeachingDesignRead


SUPPORTED_ABILITY_KEYS = frozenset({
    "concept_understanding",
    "calculation",
    "reasoning",
    "application",
})


class GenerationProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    teaching_design_version_id: str
    payload: dict

    hard_constraints: list[str] = Field(default_factory=list)
    bounded_constraints: list[str] = Field(default_factory=list)
    soft_objectives: list[str] = Field(default_factory=list)
    advisory_constraints: list[str] = Field(default_factory=list)
    unsupported_design_constraints: list[str] = Field(default_factory=list)
    constraint_provenance: dict[str, ConstraintProvenance] = Field(default_factory=dict)


class TeachingDesignGenerationError(RuntimeError):
    pass


def _duration_tolerance(target: int) -> int:
    """Use a deterministic 10% band with a practical five-minute floor."""
    return max(5, round(target * 0.10))


def project_confirmed_design(
    design: TeachingDesignRead,
) -> GenerationProjection:
    if design.status != "confirmed":
        raise TeachingDesignGenerationError(
            "only_confirmed_teaching_design_can_generate"
        )

    content = design.content
    assessment = content.assessment_plan

    teaching_required_knowledge = [
        item.name
        for item in content.knowledge_plan
        if item.role == "required"
    ]
    assessment_required_knowledge = list(
        dict.fromkeys(assessment.assessment_required_knowledge)
    )
    optional_knowledge = [
        item.name
        for item in content.knowledge_plan
        if item.role == "optional"
    ]
    prerequisites = [
        item.name
        for item in content.knowledge_plan
        if item.role == "prerequisite"
    ]

    knowledge_priority_weights = {
        item.name: item.priority
        for item in content.knowledge_plan
        if item.role in {"required", "optional"}
    }

    default_sections = {
        "chapter_test": [("选择题", 4, 5), ("填空题", 2, 10), ("计算题", 4, 15)],
        "homework": [("计算题", 3, 10), ("证明题", 2, 10)],
        "midterm": [("选择题", 4, 5), ("填空题", 2, 10), ("计算题", 4, 15)],
        "final": [("选择题", 4, 5), ("填空题", 2, 10), ("计算题", 4, 15)],
    }[assessment.paper_type]
    explicit_sections = assessment.question_type_requirements
    sections = explicit_sections or default_sections
    question_type_requirements = [
        {
            "question_type": item.question_type if explicit_sections else item[0],
            "count": item.count if explicit_sections else item[1],
            "score_each": (
                item.score_each if explicit_sections else item[2]
            ),
        }
        for item in sections
    ] if explicit_sections or assessment.question_count is None else []

    # TeachingDesign is the only authoritative input here.  In particular,
    # AssessmentPlan's Pydantic default is not teacher-explicit merely because
    # it is present in the persisted content.
    provenance = {
        "paper_type": ConstraintProvenance(
            source="TeachingDesign.assessment_plan.paper_type",
            defaulted_by="AssessmentPlan.paper_type=chapter_test",
            teacher_explicit=False,
            strength="hard",
        ),
        "scope_names": ConstraintProvenance(
            source="TeachingDesignContent.scope_names",
            teacher_explicit=True,
            strength="hard",
        ),
        "total_score": ConstraintProvenance(
            source="TeachingDesign.assessment_plan.total_score",
            defaulted_by="AssessmentPlan.total_score=100",
            teacher_explicit=False,
            strength="hard",
            note="Persisted schema does not retain field-level explicitness.",
        ),
        "difficulty_level": ConstraintProvenance(
            source="TeachingDesign.assessment_plan.difficulty",
            defaulted_by="AssessmentPlan.difficulty=normal",
            teacher_explicit=False,
            strength="bounded",
        ),
    }
    payload: dict = {
        "paper_type": assessment.paper_type,
        "scope_names": content.scope_names,
        "total_score": assessment.total_score,
        "difficulty_level": assessment.difficulty,
        "constraint_provenance": provenance,
        "question_type_requirements": question_type_requirements or None,
        "question_count": (
            assessment.question_count
            if assessment.question_count is not None
            else sum(item["count"] for item in question_type_requirements)
        ),
    }

    hard = [
        "scope",
        "total_score",
    ]
    bounded = ["difficulty_band"]
    soft: list[str] = []
    advisory: list[str] = []
    unsupported: list[str] = []

    provenance["question_type_requirements"] = ConstraintProvenance(
        source="TeachingDesign.assessment_plan (no per-type distribution)",
        defaulted_by=(
            "TeachingDesign.assessment_plan.question_type_requirements"
            if explicit_sections
            else (
                "TeachingDesign.assessment_plan.question_count"
                if assessment.question_count is not None
                else "blueprint_adapter.CHAPTER_TEST_TEMPLATE"
            )
        ),
        merge_location="paper_tools.build_structured_generation_request",
        teacher_explicit=False,
        strength="hard",
        note=(
            "The default chapter-test template is 4/2/4; required knowledge "
            "without a distribution instead uses one question per required point."
        ),
    )

    if assessment_required_knowledge:
        payload["required_knowledge_names"] = assessment_required_knowledge
        hard.append("required_knowledge_coverage")
        provenance["required_knowledge_names"] = ConstraintProvenance(
            source="TeachingDesign.assessment_plan.assessment_required_knowledge",
            teacher_explicit=True,
            strength="hard",
        )

    provenance["question_count"] = ConstraintProvenance(
        source=(
            "TeachingDesign.assessment_plan.question_type_requirements"
            if explicit_sections
            else "TeachingDesign.assessment_plan.question_count"
            if assessment.question_count is not None
            else "blueprint_adapter.CHAPTER_TEST_TEMPLATE"
        ),
        defaulted_by=(
            None
            if explicit_sections or assessment.question_count is not None
            else "build_structured_generation_request.paper_type_template"
        ),
        merge_location="GenerationService._derive_question_count",
        teacher_explicit=bool(explicit_sections or assessment.question_count is not None),
        strength="hard",
        note="Knowledge-plan cardinality never determines paper question count.",
    )

    preferred_knowledge = list(
        dict.fromkeys([
            *teaching_required_knowledge,
            *optional_knowledge,
            *assessment_required_knowledge,
        ])
    )
    if preferred_knowledge:
        payload["knowledge_preferences"] = preferred_knowledge
        payload["knowledge_priority_weights"] = (
            knowledge_priority_weights
        )
        soft.append("knowledge_priority")

    if assessment.duration_minutes is not None:
        payload["target_duration_min"] = assessment.duration_minutes
        provenance["target_duration_min"] = ConstraintProvenance(
            source="TeachingDesign.assessment_plan.duration_minutes",
            teacher_explicit=True,
            strength="bounded",
        )
        provenance["duration_tolerance_min"] = ConstraintProvenance(
            source="_duration_tolerance(target_duration_min)",
            defaulted_by="generation_adapter._duration_tolerance",
            merge_location="build_structured_generation_request",
            teacher_explicit=False,
            strength="hard",
            note="For 90 minutes this is 90 ± 9; selector enforces this range as hard bounds.",
        )
        payload["duration_tolerance_min"] = _duration_tolerance(
            assessment.duration_minutes
        )
        hard.append("hard_duration_range")

    if assessment.ability_weights:
        unknown = sorted(
            set(assessment.ability_weights) - SUPPORTED_ABILITY_KEYS
        )
        if unknown:
            unsupported.extend(
                f"ability_weight:{name}"
                for name in unknown
            )
        else:
            payload["ability_weights"] = assessment.ability_weights
            soft.append("ability_profile")

    if prerequisites:
        advisory.append("prerequisite_knowledge")

    if assessment.coverage_strategy:
        # This is still free text. Preserve it for explanation and future
        # structured compilation, but do not convert arbitrary prose into a
        # fake hard Solver constraint.
        advisory.append("coverage_strategy")

    if assessment.question_design_ideas:
        advisory.append("question_design_ideas")

    if assessment.notes:
        advisory.append("assessment_notes")

    return GenerationProjection(
        teaching_design_version_id=design.version_id,
        payload=payload,
        hard_constraints=hard,
        bounded_constraints=bounded,
        soft_objectives=soft,
        advisory_constraints=advisory,
        unsupported_design_constraints=unsupported,
        constraint_provenance=provenance,
    )
