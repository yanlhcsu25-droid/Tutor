from __future__ import annotations

from calculus_agent.agent.agent import _apply_explicit_opt_in_guards
from calculus_agent.runtime.request_guards import (
    explicit_generation_constraint_omissions,
)


def _invented_generation_args() -> dict:
    return {
        "paper_type": "chapter_test",
        "scope_names": ["第一章 函数与极限"],
        "question_count": 10,
        "total_score": 110,
        "question_type_requirements": [
            {
                "question_type": "选择题",
                "count": 5,
                "score_each": 10,
            },
            {
                "question_type": "填空题",
                "count": 3,
                "score_each": 10,
            },
            {
                "question_type": "计算题",
                "count": 2,
                "score_each": 15,
            },
        ],
    }


def test_all_one_type_reports_omitted_tool_constraint() -> None:
    omissions = explicit_generation_constraint_omissions(
        {"question_count": 8},
        "共8题，全部为计算题，难度逐步递增。",
    )

    assert omissions == [{
        "field": "question_type_requirements",
        "expected": [{"question_type": "计算题", "count": 8}],
    }]


def test_all_one_type_accepts_matching_tool_constraint() -> None:
    omissions = explicit_generation_constraint_omissions(
        {
            "question_count": 8,
            "question_type_requirements": [
                {"question_type": "计算题", "count": 8},
            ],
        },
        "共8题，全部为计算题。",
    )

    assert omissions == []


def test_explicit_mixed_distribution_reports_changed_tool_constraint() -> None:
    omissions = explicit_generation_constraint_omissions(
        {
            "question_count": 8,
            "question_type_requirements": [
                {"question_type": "选择题", "count": 5},
                {"question_type": "计算题", "count": 3},
            ],
        },
        "共8题，选择题4道，计算题4道。",
    )

    assert omissions == [{
        "field": "question_type_requirements",
        "expected": [
            {"question_type": "选择题", "count": 4},
            {"question_type": "计算题", "count": 4},
        ],
    }]


def test_explicit_mixed_distribution_accepts_matching_tool_constraint() -> None:
    omissions = explicit_generation_constraint_omissions(
        {
            "question_count": 8,
            "question_type_requirements": [
                {"question_type": "选择题", "count": 4},
                {"question_type": "计算题", "count": 4},
            ],
        },
        "共8题，4道选择题，四道计算题。",
    )

    assert omissions == []


def test_mixed_distribution_uses_explicit_whole_paper_total() -> None:
    arguments = {
        "question_count": 10,
        "question_type_requirements": [
            {"question_type": "计算题", "count": 10},
            {"question_type": "证明题", "count": 3},
        ],
    }
    guarded = _apply_explicit_opt_in_guards(
        tool_name="prepare_generation_plan",
        arguments=arguments,
        message="生成10道计算题、3道证明题，共13题。",
    )

    assert guarded["question_count"] == 13
    assert explicit_generation_constraint_omissions(
        guarded,
        "生成10道计算题、3道证明题，共13题。",
    ) == []


def test_default_generation_strips_model_invented_structure() -> None:
    guarded = _apply_explicit_opt_in_guards(
        tool_name="prepare_generation_plan",
        arguments=_invented_generation_args(),
        message="帮我出一套高数第一章测试卷。",
    )

    assert guarded["paper_type"] == "chapter_test"
    assert guarded["scope_names"] == ["第一章 函数与极限"]
    assert "total_score" not in guarded
    assert "question_count" not in guarded
    assert "question_type_requirements" not in guarded
    assert "question_type_patches" not in guarded


def test_repeated_behavior_cannot_be_inferred_as_long_term_teacher_preference() -> None:
    guarded = _apply_explicit_opt_in_guards(
        tool_name="prepare_generation_plan",
        arguments={"paper_type": "chapter_test", "total_score": 100},
        message="再出一套类似的。",
    )

    assert "total_score" not in guarded


def test_explicit_total_score_keeps_score_but_not_invented_structure() -> None:
    guarded = _apply_explicit_opt_in_guards(
        tool_name="prepare_generation_plan",
        arguments={
            **_invented_generation_args(),
            "total_score": 60,
        },
        message="第一章测试卷，总分60分。",
    )

    assert guarded["total_score"] == 60
    assert "question_count" not in guarded
    assert "question_type_requirements" not in guarded


def test_explicit_per_type_counts_keep_structure() -> None:
    requirements = [
        {"question_type": "选择题", "count": 4},
        {"question_type": "填空题", "count": 2},
        {"question_type": "计算题", "count": 4},
    ]
    guarded = _apply_explicit_opt_in_guards(
        tool_name="prepare_generation_plan",
        arguments={
            "paper_type": "chapter_test",
            "scope_names": ["第一章 函数与极限"],
            "question_count": 10,
            "question_type_requirements": requirements,
        },
        message="第一章测试卷，选择题4道，填空题2道，计算题4道。",
    )

    assert guarded["question_type_requirements"] == requirements
    # The teacher did not separately state whole-paper count; Python derives it
    # deterministically from the complete per-type distribution.
    assert "question_count" not in guarded


def test_explicit_whole_paper_count_keeps_question_count() -> None:
    guarded = _apply_explicit_opt_in_guards(
        tool_name="prepare_generation_plan",
        arguments={
            "paper_type": "chapter_test",
            "scope_names": ["第一章 函数与极限"],
            "question_count": 12,
        },
        message="第一章测试卷，一共12题。",
    )

    assert guarded["question_count"] == 12


def test_explicit_compact_count_and_score_restore_model_omissions() -> None:
    guarded = _apply_explicit_opt_in_guards(
        tool_name="prepare_generation_plan",
        arguments={"paper_type": "midterm", "scope_names": ["第一章", "第二章"]},
        message="第一章和第二章生成一套12题100分期中练习卷。",
    )

    assert guarded["question_count"] == 12
    assert guarded["total_score"] == 100


def test_pending_numeric_type_patch_is_preserved() -> None:
    patches = [
        {"question_type": "计算题", "count": 5},
    ]
    guarded = _apply_explicit_opt_in_guards(
        tool_name="prepare_generation_plan",
        arguments={
            "question_type_patches": patches,
        },
        message="计算题改成5道。",
    )

    assert guarded["question_type_patches"] == patches


def test_fuzzy_type_preference_cannot_become_exact_count() -> None:
    guarded = _apply_explicit_opt_in_guards(
        tool_name="prepare_generation_plan",
        arguments={
            "question_type_patches": [
                {"question_type": "计算题", "count": 6},
            ],
        },
        message="计算题多一点。",
    )

    assert "question_type_patches" not in guarded
