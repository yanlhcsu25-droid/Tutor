from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


FAILURE_TAGS = {
    "scope_violation", "objective_misalignment", "difficulty_mismatch",
    "poor_progression", "unrealistic_plan", "weak_assessment_alignment",
    "knowledge_coverage_gap", "question_type_imbalance", "score_inconsistency",
    "redundant_questions", "constraint_violation", "question_bank_hallucination",
    "incorrect_diagnosis", "generic_advice", "poor_actionability",
    "ungrounded_inference", "clarity_issue", "judge_error", "fixture_issue",
    "provider_failure",
}


class StructuralAssertionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assertion_id: str
    type: str
    passed: bool
    critical: bool
    reason: str
    observed: Any = None
    expected: Any = None


class JudgeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^TQ-(TD|PQ|CQ)-[0-9]{3}$")
    scores: dict[str, Annotated[int, Field(strict=True, ge=1, le=5)]]
    critical_quality_failure: bool
    failure_tags: list[str]
    rationale: dict[str, str]
    overall_comment: str


class ScoreResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["PASS", "PARTIAL", "FAIL"]
    weighted_score: float
    reasons: list[str]


def parse_judge_output(
    payload: dict[str, Any], *, case_id: str, axis_weights: dict[str, float]
) -> JudgeOutput:
    if abs(sum(axis_weights.values()) - 1.0) > 1e-9:
        raise ValueError("case-local axis weights must sum to 1.0")
    result = JudgeOutput.model_validate(payload)
    if result.case_id != case_id:
        raise ValueError(f"judge case_id mismatch: {result.case_id} != {case_id}")
    expected_axes = set(axis_weights)
    if set(result.scores) != expected_axes:
        raise ValueError(
            f"judge score axes mismatch: expected={sorted(expected_axes)}, "
            f"actual={sorted(result.scores)}"
        )
    if set(result.rationale) != expected_axes:
        raise ValueError(
            f"judge rationale axes mismatch: expected={sorted(expected_axes)}, "
            f"actual={sorted(result.rationale)}"
        )
    if len(result.failure_tags) != len(set(result.failure_tags)):
        raise ValueError("judge failure_tags must be unique")
    if any(not text.strip() for text in result.rationale.values()):
        raise ValueError("judge rationale values must be non-empty")
    unknown_tags = set(result.failure_tags) - FAILURE_TAGS
    if unknown_tags:
        raise ValueError(f"unknown failure tags: {sorted(unknown_tags)}")
    return result
