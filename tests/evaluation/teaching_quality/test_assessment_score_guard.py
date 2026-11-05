"""Regression: minutes are not assessment points in TeachingDesign guards."""
from copy import deepcopy

import pytest

from calculus_agent.runtime.request_guards import _apply_explicit_opt_in_guards


@pytest.fixture
def assessment_args():
    return {
        "content": {
            "title": "章节复习课",
            "assessment_plan": {
                "total_score": 100,
                "question_type_requirements": [
                    {"question_type": "计算题", "count": 3, "score_each": 20},
                    {"question_type": "选择题", "count": 2, "score_each": 10},
                ],
            },
        },
    }


@pytest.mark.parametrize("user_message", [
    "用三节45分钟课复习前三章，第一章最多占总时间25%，第二、三章作为重点。",
    "学生有极限换元错误，请设计一节60分钟针对性复习课。",
    "学校刚讲完导数定义，请设计下一节45分钟巩固课。",
    "设计45 分钟的复习课，题量不限。",
    "45分钟课，安排5道例题。",
])
def test_teaching_minutes_do_not_authorize_score_each(user_message, assessment_args):
    initial = deepcopy(assessment_args)
    result = _apply_explicit_opt_in_guards(
        tool_name="create_teaching_design", arguments=assessment_args, message=user_message,
    )
    reqs = result["content"]["assessment_plan"]["question_type_requirements"]
    assert all("score_each" not in item for item in reqs)
    assert assessment_args == initial


@pytest.mark.parametrize("user_message", [
    "设计一节45分钟课，配一份每题5分的练习。",
    "设计一节45分钟课，配100分的测试卷。",
    "课堂45分钟，测试卷满分100分。",
    "教学复习，题目总分100分。",
])
def test_explicit_assessment_points_preserved(user_message, assessment_args):
    result = _apply_explicit_opt_in_guards(
        tool_name="create_teaching_design", arguments=assessment_args, message=user_message,
    )
    reqs = result["content"]["assessment_plan"]["question_type_requirements"]
    assert [item["score_each"] for item in reqs] == [20, 10]


def test_revise_design_applies_same_guard(assessment_args):
    result = _apply_explicit_opt_in_guards(
        tool_name="revise_teaching_design",
        arguments={"patch": assessment_args["content"]},
        message="修改成60分钟的复习课",
    )
    reqs = result["patch"]["assessment_plan"]["question_type_requirements"]
    assert all("score_each" not in item for item in reqs)


def test_non_teaching_tools_not_modified(assessment_args):
    for name in ("inspect_curriculum", "inspect_question_bank"):
        result = _apply_explicit_opt_in_guards(
            tool_name=name, arguments=assessment_args, message="45分钟课",
        )
        assert result == assessment_args
