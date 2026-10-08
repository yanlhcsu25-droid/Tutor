from __future__ import annotations

import pytest

from tests.evals.teaching_quality.grader.structural import (
    SemanticEvaluationUnavailable,
    StructuralAssertionGrader,
    UnknownAssertionType,
)


class FixtureSemanticEvaluator:
    """Deterministic test double; production semantic evaluation remains unwired."""

    def evaluate(self, assertion, actual, setup):
        text = actual["response"]["text"]
        if assertion["type"] == "required_content":
            passed = "错误可能来自" in text and "下一节可以" in text
        elif assertion["type"] == "forbidden_content":
            passed = "重点学习定积分" not in text
        else:
            passed = True
        return passed, "fixture semantic decision", text


def test_required_content_is_semantic_not_heading_match():
    grader = StructuralAssertionGrader(semantic_evaluator=FixtureSemanticEvaluator())
    result = grader.grade(
        {
            "id": "diagnosis", "type": "required_content", "critical": True,
            "target": "response", "expected": ["原因分析", "教学建议"],
            "match_mode": "semantic",
        },
        {"response": {"text": "错误可能来自左右极限混淆，下一节可以先做分类辨析。"}},
        {},
    )
    assert result.passed


@pytest.mark.parametrize(
    ("text", "passed"),
    [("本节课不讲定积分。", True), ("下一节重点学习定积分。", False)],
)
def test_forbidden_content_instructional_use(text, passed):
    grader = StructuralAssertionGrader(semantic_evaluator=FixtureSemanticEvaluator())
    result = grader.grade(
        {
            "id": "later", "type": "forbidden_content", "critical": True,
            "target": "response", "forbidden": ["定积分"], "match_mode": "semantic",
            "violation_mode": "instructional_use", "mention_allowed": True,
        },
        {"response": {"text": text}},
        {},
    )
    assert result.passed is passed


def test_chapter_scope_is_derived_from_topics():
    grader = StructuralAssertionGrader(
        curriculum_mapping={"函数极限": "第一章", "导数定义": "第二章", "中值定理": "第三章", "不定积分": "第四章"}
    )
    assertion = {
        "id": "scope", "type": "allowed_scope_only", "critical": True,
        "target": "teaching_design.chapters", "expected": ["第一章", "第二章", "第三章"],
    }
    actual = {"teaching_design": {"topics": ["函数极限", "导数定义", "中值定理"]}}
    assert grader.grade(assertion, actual, {}).passed
    actual["teaching_design"]["topics"].append("不定积分")
    assert not grader.grade(assertion, actual, {}).passed


def test_td006_per_lesson_duration_is_derived_from_activities():
    grader = StructuralAssertionGrader()
    assertion = {
        "id": "lesson_duration", "type": "duration_max_per_lesson", "critical": True,
        "target": "teaching_design.lessons.minutes", "expected": 45,
    }
    actual = {"teaching_design": {"lessons": [
        {"activities": [{"minutes": 20}, {"minutes": 25}]},
        {"activities": [{"minutes": 46}]},
        {"activities": [{"minutes": 45}]},
    ]}}
    assert not grader.grade(assertion, actual, {}).passed


def test_td006_chapter_share_uses_actual_activity_minutes():
    grader = StructuralAssertionGrader(
        curriculum_mapping={"函数极限": "第一章", "导数": "第二章", "中值定理": "第三章"}
    )
    assertion = {
        "id": "chapter1_share", "type": "allocation_share_max", "critical": True,
        "target": "teaching_design.chapter_time_share.第一章", "expected": 0.25,
    }
    actual = {"teaching_design": {"chapter_time_share": {"第一章": 0.1}, "lessons": [
        {"activities": [
            {"minutes": 40, "topics": ["函数极限"]},
            {"minutes": 30, "topics": ["导数"]},
            {"minutes": 30, "topics": ["中值定理"]},
        ]}
    ]}}
    result = grader.grade(assertion, actual, {})
    assert not result.passed
    assert result.observed == pytest.approx(0.4)


@pytest.mark.parametrize("target,key", [("teaching_design.examples", "examples"), ("teaching_design.misconceptions", "misconceptions")])
def test_count_min_per_topic_counts_real_objects(target, key):
    grader = StructuralAssertionGrader()
    assertion = {
        "id": "per_topic", "type": "count_min_per_topic", "critical": True,
        "target": target, "expected": 1,
    }
    setup = {"required_topics": ["函数极限", "连续性", "间断点"]}
    actual = {"teaching_design": {key: [
        {"topic": "函数极限", "content": "A"},
        {"topic": "连续性", "content": "B"},
        {"topic": "间断点", "content": "C"},
    ], "minimum_per_topic": 1}}
    assert grader.grade(assertion, actual, setup).passed
    actual["teaching_design"][key].pop()
    assert not grader.grade(assertion, actual, setup).passed


def test_one_generic_warning_does_not_cover_all_misconception_topics():
    grader = StructuralAssertionGrader()
    result = grader.grade(
        {
            "id": "misconceptions", "type": "count_min_per_topic", "critical": True,
            "target": "teaching_design.misconceptions", "expected": 1,
        },
        {"teaching_design": {"misconceptions": [{"content": "注意计算"}]}},
        {"required_topics": ["极限运算", "连续性", "导数计算"]},
    )
    assert not result.passed


def test_production_semantic_assertion_fails_explicitly_when_unwired():
    with pytest.raises(SemanticEvaluationUnavailable):
        StructuralAssertionGrader().grade(
            {
                "id": "grounded", "type": "source_grounded", "critical": True,
                "target": "response", "expected": "student_profile.evidence",
            },
            {"response": {"text": "建议"}},
            {"student_profile": {"evidence": ["错题"]}},
        )


def test_unknown_assertion_type_is_an_error():
    with pytest.raises(UnknownAssertionType):
        StructuralAssertionGrader().grade(
            {"id": "x", "type": "made_up", "critical": True}, {}, {}
        )
