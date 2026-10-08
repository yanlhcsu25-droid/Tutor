from tests.evals.teaching_quality.grader.models import (
    JudgeOutput,
    StructuralAssertionResult,
)
from tests.evals.teaching_quality.grader.scoring import score_case


def structural(passed=True, critical=True):
    return StructuralAssertionResult(
        assertion_id="a", type="exact_count", passed=passed, critical=critical,
        reason="test", observed=1, expected=1,
    )


def judge(scores, critical=False):
    return JudgeOutput(
        case_id="TQ-TD-001", scores=scores, critical_quality_failure=critical,
        failure_tags=[], rationale={axis: "evidence" for axis in scores}, overall_comment="ok",
    )


def test_pass_requires_structural_and_four_point_behavioral_score():
    result = score_case(
        [structural()], judge({"objective_alignment": 4, "feasibility": 4}),
        {"objective_alignment": 0.5, "feasibility": 0.5},
    )
    assert result.status == "PASS"


def test_partial_for_three_to_four_score():
    result = score_case(
        [structural()], judge({"objective_alignment": 3, "feasibility": 4}),
        {"objective_alignment": 0.5, "feasibility": 0.5},
    )
    assert result.status == "PARTIAL"


def test_critical_structural_failure_forces_fail():
    result = score_case(
        [structural(passed=False)], judge({"objective_alignment": 5}),
        {"objective_alignment": 1.0},
    )
    assert result.status == "FAIL"


def test_axis_score_one_forces_fail():
    result = score_case(
        [structural()], judge({"objective_alignment": 5, "feasibility": 1}),
        {"objective_alignment": 0.75, "feasibility": 0.25},
    )
    assert result.status == "FAIL"
