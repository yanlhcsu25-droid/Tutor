import pytest
from pydantic import ValidationError

from calculus_agent.teaching_design.schemas import AssessmentPlan


def test_assessment_rejects_inconsistent_explicit_scores():
    with pytest.raises(ValidationError, match="section scores"):
        AssessmentPlan(total_score=100, question_count=10, question_type_requirements=[
            {"question_type": t, "count": n, "score_each": s}
            for t, n, s in [("填空题", 3, 10), ("计算题", 4, 15), ("选择题", 2, 10), ("证明题", 1, 20)]
        ])


def test_assessment_rejects_count_conflict():
    with pytest.raises(ValidationError, match="question counts"):
        AssessmentPlan(question_count=10, question_type_requirements=[
            {"question_type": "计算题", "count": 9},
        ])


def test_assessment_accepts_consistent_scores_and_unspecified_scores():
    for score in (None, 10):
        plan = AssessmentPlan(total_score=100, question_count=10, question_type_requirements=[
            {"question_type": "计算题", "count": 10, "score_each": score},
        ])
        assert plan.question_type_requirements[0].score_each == score
