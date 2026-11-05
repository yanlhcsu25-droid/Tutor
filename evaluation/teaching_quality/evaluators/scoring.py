from __future__ import annotations

from .models import JudgeOutput, ScoreResult, StructuralAssertionResult


def score_case(
    structural: list[StructuralAssertionResult],
    judge: JudgeOutput,
    axis_weights: dict[str, float],
) -> ScoreResult:
    if set(judge.scores) != set(axis_weights):
        raise ValueError("judge scores and case-local axes do not match")
    if abs(sum(axis_weights.values()) - 1.0) > 1e-9:
        raise ValueError("case-local axis weights must sum to 1.0")

    weighted = sum(judge.scores[axis] * weight for axis, weight in axis_weights.items())
    reasons: list[str] = []
    critical_failures = [r.assertion_id for r in structural if r.critical and not r.passed]
    if critical_failures:
        reasons.append(f"critical structural failures: {', '.join(critical_failures)}")
    if judge.critical_quality_failure:
        reasons.append("judge reported a critical quality failure")
    if weighted < 3.0:
        reasons.append("weighted behavioral score is below 3.0")
    if any(score == 1 for score in judge.scores.values()):
        reasons.append("a core behavioral axis scored 1")

    if reasons:
        status = "FAIL"
    elif weighted >= 4.0 and all(score >= 3 for score in judge.scores.values()):
        status = "PASS"
    else:
        status = "PARTIAL"
        if weighted >= 4.0:
            reasons.append("a core behavioral axis is below 3")
        else:
            reasons.append("weighted behavioral score is below 4.0")

    return ScoreResult(status=status, weighted_score=round(weighted, 4), reasons=reasons)
