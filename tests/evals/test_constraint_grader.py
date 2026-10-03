from types import SimpleNamespace

from tests.evals.graders.constraint_grader import grade_constraints


def _case(*, preserve, expected=None):
    return SimpleNamespace(preserve=preserve, expected=expected or {})


def test_explicit_question_count_scope_and_difficulty_are_preserved():
    before = {
        "question_count": 10,
        "scope_names": ["第一章"],
        "difficulty_level": "easy",
    }
    actual = {
        "before": {"generation": before},
        "generation": {**before, "question_count": 12, "scope_names": ["第二章"]},
    }

    result = grade_constraints(
        _case(preserve=["question_count", "scope", "difficulty"]), actual,
    )

    assert not result["passed"]
    assert any("question_count" in error for error in result["errors"])
    assert any("scope_names" in error for error in result["errors"])
    assert not any("difficulty_level" in error for error in result["errors"])


def test_unspecified_question_type_fields_cannot_be_lost():
    before = {
        "question_type_requirements": [
            {"question_type": "计算题", "count": 4, "score_each": 10},
            {"question_type": "证明题", "count": 2, "score_each": 20},
        ],
    }
    actual = {
        "before": {"generation": before},
        "generation": {"question_type_requirements": [
            {"question_type": "计算题", "count": 5},
            {"question_type": "证明题", "count": 2, "score_each": 20},
        ]},
    }
    case = _case(
        preserve=["unspecified_question_types"],
        expected={"pending_generation": {
            "question_type_requirements": [{"question_type": "计算题", "count": 5}],
        }},
    )

    result = grade_constraints(case, actual)

    assert not result["passed"]
    assert any("计算题.score_each should be preserved" in error for error in result["errors"])


def test_non_baseline_safety_rules_work_for_initial_preview():
    actual = {
        "before": {"generation": {}},
        "generation": {"question_count": 5},
        "evaluated_turns": [{"state": {"trace": {"tool_calls": []}}}],
    }

    result = grade_constraints(
        _case(preserve=["no_auto_confirm", "no-repeat"]), actual,
    )

    assert result["passed"]


def test_no_auto_confirm_rejects_confirmation_tool():
    actual = {
        "before": {"generation": {"question_count": 10}},
        "generation": {"question_count": 10},
        "evaluated_turns": [{
            "state": {"trace": {"tool_calls": [{"tool_name": "confirm_generation"}]}},
        }],
    }

    result = grade_constraints(_case(preserve=["no_auto_confirm"]), actual)

    assert not result["passed"]
    assert "automatic confirmation observed" in result["errors"][0]
