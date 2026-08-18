"""Pure business contracts for the TeachingDesign domain."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


TeachingDesignStatus = Literal[
    "draft",
    "awaiting_confirmation",
    "confirmed",
    "superseded",
]

KnowledgeRole = Literal["required", "optional", "prerequisite"]


class EvidenceReference(BaseModel):
    """A durable reference to evidence used when a design was created.

    ``ref_id`` is intentionally opaque. Future evidence tools may point it to
    question-bank snapshots, curriculum snapshots, solver reports, etc.
    The design stores a compact summary for auditability, not the whole source.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=80)
    ref_id: str | None = Field(default=None, max_length=120)
    summary: str = Field(min_length=1, max_length=3000)
    observed_by_run_id: str | None = Field(default=None, max_length=36)


class KnowledgePlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    role: KnowledgeRole = "required"
    priority: int = Field(default=3, ge=1, le=5)
    introduction: str | None = Field(default=None, max_length=5000)
    teaching_focus: str | None = Field(default=None, max_length=2000)


class TeachingStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=1, le=100)
    title: str = Field(min_length=1, max_length=255)
    objectives: list[str] = Field(default_factory=list)
    knowledge_points: list[str] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=3000)


class LecturePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    structure: list[str] = Field(default_factory=list)
    emphasis: list[str] = Field(default_factory=list)
    example_strategy: str | None = Field(default=None, max_length=3000)
    notes: list[str] = Field(default_factory=list)


class AssessmentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_type: Literal[
        "chapter_test",
        "chapter_exercise",
        "homework",
        "midterm",
        "final",
    ] = "chapter_test"
    total_score: int = Field(default=100, ge=1, le=300)
    duration_minutes: int | None = Field(default=None, ge=1, le=600)
    difficulty: Literal["easy", "normal", "hard"] = "normal"
    coverage_strategy: str | None = Field(default=None, max_length=3000)
    ability_weights: dict[str, int] = Field(default_factory=dict)
    question_design_ideas: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_ability_weights(self) -> "AssessmentPlan":
        if any(value < 0 for value in self.ability_weights.values()):
            raise ValueError("ability_weights values must be non-negative")
        if self.ability_weights and sum(self.ability_weights.values()) != 100:
            raise ValueError("ability_weights must sum to 100")
        return self


class TeachingDesignContent(BaseModel):
    """Immutable content of one TeachingDesign version."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    objective: str = Field(min_length=1, max_length=5000)
    scope_names: list[str] = Field(min_length=1)

    knowledge_plan: list[KnowledgePlanItem] = Field(default_factory=list)
    teaching_priorities: list[str] = Field(default_factory=list)
    teaching_sequence: list[TeachingStep] = Field(default_factory=list)

    lecture_plan: LecturePlan = Field(default_factory=LecturePlan)
    assessment_plan: AssessmentPlan = Field(default_factory=AssessmentPlan)

    evidence_refs: list[EvidenceReference] = Field(default_factory=list)
    feasibility_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_sequence(self) -> "TeachingDesignContent":
        if self.teaching_sequence:
            orders = [item.order for item in self.teaching_sequence]
            if len(set(orders)) != len(orders):
                raise ValueError("teaching_sequence order values must be unique")
            self.teaching_sequence = sorted(
                self.teaching_sequence,
                key=lambda item: item.order,
            )
        return self


class TeachingDesignPatch(BaseModel):
    """A patch never mutates a version; it creates the next version."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    objective: str | None = Field(default=None, min_length=1, max_length=5000)
    scope_names: list[str] | None = None
    knowledge_plan: list[KnowledgePlanItem] | None = None
    teaching_priorities: list[str] | None = None
    teaching_sequence: list[TeachingStep] | None = None
    lecture_plan: LecturePlan | None = None
    assessment_plan: AssessmentPlan | None = None
    evidence_refs: list[EvidenceReference] | None = None
    feasibility_warnings: list[str] | None = None


class TeachingDesignRead(BaseModel):
    """Read model carrying both business content and full audit provenance."""

    model_config = ConfigDict(extra="forbid")

    version_id: str
    design_key: str
    owner_key: str
    source_conversation_id: str
    parent_version_id: str | None

    version: int
    status: TeachingDesignStatus
    content: TeachingDesignContent

    created_by_run_id: str | None = None
    source_user_message: str | None = None
    change_reason: str | None = None
    created_at: str

    confirmed_by_run_id: str | None = None
    confirmed_at: str | None = None

    superseded_by_version_id: str | None = None
    superseded_at: str | None = None
