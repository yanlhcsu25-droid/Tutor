"""Table-driven regressions for teacher-owned generation constraints."""

from __future__ import annotations

import pytest

from calculus_agent.runtime.request_guards import (
    _apply_explicit_opt_in_guards,
    explicit_generation_constraint_mismatches,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("共8题，全部为计算题。", {"计算题": 8}),
        ("总共十二题，均为证明题。", {"证明题": 12}),
        ("一共10题，全都是填空题。", {"填空题": 10}),
        ("选择题4道，计算题6道。", {"选择题": 4, "计算题": 6}),
        ("4道选择题，六道计算题。", {"选择题": 4, "计算题": 6}),
        ("选择题十道、证明题两道。", {"选择题": 10, "证明题": 2}),
    ],
)
def test_explicit_type_phrasings_are_checked(message: str, expected: dict[str, int]) -> None:
    requirements = [
        {"question_type": question_type, "count": count}
        for question_type, count in expected.items()
    ]

    mismatches = explicit_generation_constraint_mismatches(
        {"question_type_requirements": requirements},
        message,
    )

    assert mismatches == []


@pytest.mark.parametrize(
    ("message", "arguments", "expected"),
    [
        (
            "共8题，选择题4道，计算题4道。",
            {"question_type_requirements": [
                {"question_type": "选择题", "count": 5},
                {"question_type": "计算题", "count": 3},
            ]},
            [{"question_type": "选择题", "count": 4}, {"question_type": "计算题", "count": 4}],
        ),
        (
            "共8题，选择题4道，计算题4道。",
            {"question_type_requirements": [
                {"question_type": "选择题", "count": 4},
                {"question_type": "计算题", "count": 4},
                {"question_type": "证明题", "count": 1},
            ]},
            [{"question_type": "选择题", "count": 4}, {"question_type": "计算题", "count": 4}],
        ),
        (
            "生成10道计算题、3道证明题，共13题。",
            {"question_type_requirements": [{"question_type": "计算题", "count": 10}]},
            [{"question_type": "计算题", "count": 10}, {"question_type": "证明题", "count": 3}],
        ),
    ],
)
def test_changed_missing_or_extra_types_are_reported(
    message: str,
    arguments: dict,
    expected: list[dict],
) -> None:
    assert explicit_generation_constraint_mismatches(arguments, message) == [{
        "field": "question_type_requirements",
        "expected": expected,
    }]


def test_partial_type_request_allows_unspecified_types() -> None:
    arguments = {"question_type_requirements": [
        {"question_type": "选择题", "count": 4},
        {"question_type": "计算题", "count": 6},
    ]}

    assert explicit_generation_constraint_mismatches(
        arguments,
        "选择题4道，其他题型按默认安排。",
    ) == []


@pytest.mark.parametrize(
    ("message", "expected_count"),
    [
        ("第一章生成一套12题练习。", 12),
        ("生成10道计算题、3道证明题，共13题。", 13),
        ("请出8题，全部为计算题。", 8),
    ],
)
def test_explicit_whole_paper_count_wins_over_embedded_type_counts(
    message: str,
    expected_count: int,
) -> None:
    guarded = _apply_explicit_opt_in_guards(
        tool_name="prepare_generation_plan",
        arguments={"question_count": 99},
        message=message,
    )

    assert guarded["question_count"] == expected_count


def test_model_invented_score_and_distribution_are_removed_together() -> None:
    guarded = _apply_explicit_opt_in_guards(
        tool_name="prepare_generation_plan",
        arguments={
            "question_count": 10,
            "total_score": 100,
            "question_type_requirements": [
                {"question_type": "选择题", "count": 5},
                {"question_type": "计算题", "count": 5},
            ],
        },
        message="帮我出一套第一章练习。",
    )

    assert "question_count" not in guarded
    assert "total_score" not in guarded
    assert "question_type_requirements" not in guarded
