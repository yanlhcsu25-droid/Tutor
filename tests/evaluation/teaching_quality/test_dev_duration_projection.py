"""Dev-only offline tests for actual teaching-design time observations."""

from copy import deepcopy

from evaluation.teaching_quality.evaluators.structural import StructuralAssertionGrader
from evaluation.teaching_quality.runners.dev_contract import adapt_dev_observation


ASSERTION = {
    "id": "duration",
    "type": "total_duration_max",
    "target": "teaching_design.minutes",
    "expected": 45,
    "critical": True,
}


def test_four_explicit_lecture_allocations_sum_to_45():
    actual = {"teaching_design": {"lecture_plan": {"structure": [
        "概念回顾（10分钟）", "重点难点讲解（15分钟）", "例题演练（15分钟）", "小结与答疑（5分钟）",
    ]}}}
    snapshot = deepcopy(actual)
    modified, projected, provenance = adapt_dev_observation(ASSERTION, actual)
    assert projected["teaching_design"]["minutes"] == 45
    assert actual == snapshot
    assert len(provenance) == 1
    obs = StructuralAssertionGrader().grade(modified, projected, {})
    assert obs.passed is True


def test_ambiguous_or_missing_time_is_not_invented():
    samples = [
        ["概念回顾（10分钟）", "灵活练习（视情况）"],
        ["概念回顾", "小结"],
        ["10分钟复习和15分钟练习"],
        ["本节课45分钟"],
        ["本节课（45分钟）"],
        [],
    ]
    for entries in samples:
        actual = {"teaching_design": {"lecture_plan": {"structure": entries}}}
        _, projected, provenance = adapt_dev_observation(ASSERTION, actual)
        assert "minutes" not in projected["teaching_design"]
        assert not provenance


def test_case_setup_time_is_not_used_as_observation():
    actual = {"teaching_design": {"lesson_count": 1}}
    _, projected, provenance = adapt_dev_observation(ASSERTION, actual)
    assert "minutes" not in projected["teaching_design"]
    assert not provenance


def test_overlong_lecture_allocation_fails_without_overriding_expected():
    actual = {"teaching_design": {"lecture_plan": {"structure": ["概念回顾（20分钟）", "练习（30分钟）"]}}}
    modified, projected, _ = adapt_dev_observation(ASSERTION, actual)
    assert projected["teaching_design"]["minutes"] == 50
    assert StructuralAssertionGrader().grade(modified, projected, {}).passed is False


def test_explicit_stored_minutes_are_not_overwritten():
    actual = {"teaching_design": {"minutes": 50, "lecture_plan": {"structure": ["回顾（10分钟）"]}}}
    modified, projected, provenance = adapt_dev_observation(ASSERTION, actual)
    assert projected["teaching_design"]["minutes"] == 50
    assert not provenance
    assert StructuralAssertionGrader().grade(modified, projected, {}).passed is False
