from __future__ import annotations

from calculus_agent.agent.agent import _apply_explicit_opt_in_guards


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
