"""Schemas for the requirement-understanding layer.

This is intentionally separate from ``calculus_agent.schemas.PaperBlueprint``:
the latter is the executable, question-selection contract.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from calculus_agent.papers.addressing import QuestionAddress
from calculus_agent.schemas import ConstraintProvenance, PaperBlueprint


class RequirementPreferences(BaseModel):
    more_question_types: list[str] = Field(default_factory=list)
    difficulty_ratio: dict[str, int] = Field(default_factory=dict)


class QuestionTypeRequirement(BaseModel):
    question_type: str = Field(min_length=1, max_length=40)
    count: int = Field(ge=1, le=100)
    score_each: float | None = Field(default=None, gt=0, le=300)
    total_score: float | None = Field(default=None, gt=0, le=300)


class QuestionTypePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_type: str = Field(min_length=1, max_length=40)
    count: int | None = Field(default=None, ge=1, le=100)
    score_each: float | None = Field(default=None, gt=0, le=300)


class GeneratePaperInput(BaseModel):
    """Teacher-facing concepts extracted by the LLM for paper generation."""
    model_config = ConfigDict(extra="forbid")

    paper_type: Literal[
        "chapter_test", "chapter_exercise", "homework", "midterm", "final"
    ] | None = None
    scope_names: list[str] | None = None
    audience: str | None = Field(default=None, max_length=100)
    question_count: int | None = Field(default=None, ge=1, le=100)
    total_score: int | None = Field(default=None, ge=1, le=300)
    question_type_requirements: list[QuestionTypeRequirement] | None = None
    knowledge_preferences: list[str] | None = None
    required_knowledge_names: list[str] | None = None
    knowledge_priority_weights: dict[str, int] | None = None
    difficulty_level: Literal["easy", "normal", "hard"] | None = None
    difficulty_ratio: dict[str, int] | None = None
    difficulty_preference: str | None = Field(default=None, max_length=500)
    diversity_preference: str | None = Field(default=None, max_length=500)
    target_duration_min: int | None = Field(default=None, ge=1, le=600)
    duration_tolerance_min: int | None = Field(default=None, ge=0, le=120)
    ability_weights: dict[str, int] | None = None
    constraint_provenance: dict[str, ConstraintProvenance] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_execution_targets(self) -> "GeneratePaperInput":
        if (
            self.duration_tolerance_min is not None
            and self.target_duration_min is None
        ):
            raise ValueError(
                "duration_tolerance_min requires target_duration_min"
            )
        if self.knowledge_priority_weights:
            if any(
                value < 1 or value > 5
                for value in self.knowledge_priority_weights.values()
            ):
                raise ValueError(
                    "knowledge priority weights must be 1..5"
                )
        if self.ability_weights:
            allowed = {
                "concept_understanding",
                "calculation",
                "reasoning",
                "application",
            }
            if set(self.ability_weights) - allowed:
                raise ValueError("unsupported ability weight key")
            if any(value < 0 for value in self.ability_weights.values()):
                raise ValueError("ability weight must be non-negative")
            if sum(self.ability_weights.values()) != 100:
                raise ValueError("ability weights must sum to 100")
        return self


class GenerationPlanPatch(GeneratePaperInput):
    question_type_patches: list[QuestionTypePatch] | None = None
    avoid_previous_paper_questions: bool | None = Field(
        default=None, description="Teacher preference only; currently unsupported by generation."
    )


class FeedbackItemInput(BaseModel):
    """One teacher-reported wrong item against the current concrete Paper version.

    Exactly one of ``address`` (section-local) or ``position`` (whole-paper)
    must be supplied. ``teacher_note`` is raw observation text only and never
    feeds reinforcement weight in V1.
    """

    model_config = ConfigDict(extra="forbid")

    address: QuestionAddress | None = None
    position: int | None = Field(default=None, ge=1)
    teacher_note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def address_or_position_exclusive(self) -> "FeedbackItemInput":
        if (self.address is None) == (self.position is None):
            raise ValueError(
                "必须且只能提供 address（题型内题号）或 position（全卷题号）之一。"
            )
        return self


class PrepareReinforcementPlanInput(BaseModel):
    """Tool arguments for ``prepare_reinforcement_plan``.

    The model must NOT supply any database id (paper_id, question_id,
    knowledge_node_id, weight, …); Python resolves everything from the real
    current Paper version.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[FeedbackItemInput] = Field(min_length=1, max_length=100)


class TeachingPlanningDraft(BaseModel):
    """Conversation-scoped, pre-curriculum teaching-planning artifact."""

    model_config = ConfigDict(extra="forbid")

    problem_analysis: str = Field(min_length=1, max_length=3000)
    learning_objectives: list[str] = Field(min_length=1, max_length=20)
    knowledge_focus: list[str] = Field(min_length=1, max_length=20)
    teaching_strategy: list[str] = Field(min_length=1, max_length=20)
    assessment_strategy: list[str] = Field(min_length=1, max_length=20)


class ActiveLearningContext(BaseModel):
    """Compact, conversation-scoped learning facts for the next teacher action.

    This is short-term workspace state, not long-term student memory. Values
    come from validated curriculum/question-bank observations, never solely
    from model-authored narrative.
    """

    scope_names: list[str] = Field(default_factory=list)
    knowledge_names: list[str] = Field(default_factory=list)
    learning_need: str | None = None
    evidence_refs: list[dict] = Field(default_factory=list)
    generation_diagnosis: dict | None = None
    source_run_id: str | None = None


class AgentWorkingMemory(BaseModel):
    active_task: dict = Field(default_factory=dict)
    active_learning_context: ActiveLearningContext | None = None
    generation_summary: dict = Field(default_factory=dict)
    last_clarification: dict | None = None
    last_completed_paper: dict | None = None
    unsupported_preferences: list[dict] = Field(default_factory=list)


class GenerationPlanPreview(BaseModel):
    ok: bool
    request: GeneratePaperInput
    title: str | None = None
    total_questions: int | None = None
    total_score: float | None = None
    total_score_source: Literal[
        "teacher_explicit",
        "teaching_design",
        "pending_inherited",
        "default_template",
        "system_rebalanced",
    ] | None = None
    pending_version: int | None = None
    sections: list[QuestionTypeRequirement] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blocking_errors: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    constraint_provenance: dict[str, ConstraintProvenance] = Field(default_factory=dict)


class GenerationConstraints(BaseModel):
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

class PaperGenerationRequest(BaseModel):
    blueprint: PaperBlueprint
    constraints: GenerationConstraints = Field(default_factory=GenerationConstraints)


class ReplacementIntent(BaseModel):
    action: Literal["replace_question"] = "replace_question"
    target_position: int = Field(gt=0, le=100)
    difficulty_direction: Literal["easier", "harder", "same"] | None = None
    target_difficulty: int | None = Field(default=None, ge=1, le=5)
    target_knowledge_node_ids: list[str] = Field(default_factory=list, description="候选题必须关联的知识点节点")
    avoid_knowledge_node_ids: list[str] = Field(default_factory=list, description="候选题不得关联的知识点节点")
    avoid_similarity_with_question_numbers: list[int] = Field(default_factory=list, description="避免与指定题号产生知识点重合；当前不是文本、解法或 embedding 语义相似度")
    need_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def clarification_is_consistent(self):
        if not self.need_clarification and not (self.difficulty_direction or self.target_difficulty):
            raise ValueError("换题必须指定难度方向或目标难度")
        if self.need_clarification and not self.clarification_questions:
            raise ValueError("需要追问时必须提供 clarification_questions")
        return self


class RequirementBlueprint(BaseModel):
    paper_type: Literal["chapter_test", "homework", "midterm", "final"]
    scope: list[str] = Field(default_factory=list)
    total_score: int | None = Field(default=100, ge=1, le=300)
    difficulty: Literal["easy", "normal", "hard"] = "normal"
    duration: int | None = Field(default=None, ge=1, le=600)
    preferences: RequirementPreferences = Field(default_factory=RequirementPreferences)
    need_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def clarification_is_consistent(self):
        if self.need_clarification and not self.clarification_questions:
            raise ValueError("需要追问时必须提供 clarification_questions")
        if not self.need_clarification and self.clarification_questions:
            raise ValueError("无需追问时 clarification_questions 必须为空")
        return self
