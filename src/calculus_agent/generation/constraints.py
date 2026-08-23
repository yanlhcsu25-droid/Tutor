"""Canonical deterministic constraints for paper generation."""

from __future__ import annotations

from pydantic import BaseModel, Field

from calculus_agent.schemas import ConstraintProvenance


class GenerationConstraints(BaseModel):
    """Generation-domain contract consumed by selection and solver code.

    The legacy fields remain intact while semantic fields provide the stable
    boundary for future callers.  Runtime and Tool schemas do not own this
    model.
    """

    # Existing executable selection constraints.
    scope: list[str] = Field(default_factory=list)
    scope_chapter_ids: list[str] = Field(default_factory=list)
    scope_node_ids: list[str] = Field(default_factory=list)
    scope_knowledge_node_ids: list[str] = Field(default_factory=list)
    allowed_difficulty_levels: list[int] = Field(default_factory=list)
    preferred_difficulty_levels: list[int] = Field(default_factory=list)
    fallback_difficulty_levels: list[int] = Field(default_factory=list)
    preferred_knowledge_node_ids: list[str] = Field(default_factory=list)
    knowledge_priority_weights: dict[str, int] = Field(default_factory=dict)
    target_duration_min: int | None = Field(default=None, ge=1, le=600)
    duration_tolerance_min: int = Field(default=5, ge=0, le=120)
    ability_weights: dict[str, int] = Field(default_factory=dict)
    audience: str | None = None
    difficulty_preference_text: str | None = None
    diversity_preference: str | None = None
    constraint_provenance: dict[str, ConstraintProvenance] = Field(default_factory=dict)

    # Canonical semantic boundary for new generation use cases.
    chapter_scope: list[str] = Field(default_factory=list)
    knowledge_points: list[str] = Field(default_factory=list)
    question_count: int | None = Field(default=None, ge=1, le=100)
    difficulty_range: list[str] = Field(default_factory=list)
    question_type_distribution: dict[str, int] = Field(default_factory=dict)
    total_score: int | None = Field(default=None, ge=1, le=300)
    # Stored and applied by candidate filtering; no A/B workflow is introduced.
    excluded_question_ids: set[str] = Field(default_factory=set)

    @classmethod
    def from_generation_input(cls, request: object) -> "GenerationConstraints":
        requirements = getattr(request, "question_type_requirements", None) or []
        knowledge = (
            getattr(request, "required_knowledge_names", None)
            or getattr(request, "knowledge_preferences", None)
            or []
        )
        difficulty = [value for value in (
            getattr(request, "difficulty_level", None),
            getattr(request, "difficulty_preference", None),
        ) if value]
        scope = list(getattr(request, "scope_names", None) or [])
        return cls(
            scope=scope,
            chapter_scope=scope,
            knowledge_points=list(knowledge),
            question_count=getattr(request, "question_count", None),
            difficulty_range=difficulty,
            question_type_distribution={
                item.question_type: item.count for item in requirements
            },
            total_score=getattr(request, "total_score", None),
        )

    def merged_exclusions(self, existing: list[str]) -> list[str]:
        return list(dict.fromkeys([*existing, *sorted(self.excluded_question_ids)]))
