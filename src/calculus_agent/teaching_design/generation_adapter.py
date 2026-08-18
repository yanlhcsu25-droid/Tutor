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

    required_knowledge = [
        item.name
        for item in content.knowledge_plan
        if item.role == "required"
    ]
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

    payload: dict = {
        "paper_type": assessment.paper_type,
        "scope_names": content.scope_names,
        "total_score": assessment.total_score,
        "difficulty_level": assessment.difficulty,
    }

    hard = [
        "scope",
        "total_score",
    ]
    bounded = ["difficulty_band"]
    soft: list[str] = []
    advisory: list[str] = []
    unsupported: list[str] = []

    if required_knowledge:
        payload["required_knowledge_names"] = required_knowledge
        hard.append("required_knowledge_coverage")

    preferred_knowledge = list(
        dict.fromkeys([*required_knowledge, *optional_knowledge])
    )
    if preferred_knowledge:
        payload["knowledge_preferences"] = preferred_knowledge
        payload["knowledge_priority_weights"] = (
            knowledge_priority_weights
        )
        soft.append("knowledge_priority")

    if assessment.duration_minutes is not None:
        payload["target_duration_min"] = assessment.duration_minutes
        payload["duration_tolerance_min"] = _duration_tolerance(
            assessment.duration_minutes
        )
        bounded.append("estimated_duration")

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
    )
