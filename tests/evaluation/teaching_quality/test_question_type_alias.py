"""Teacher-defined synonym: 解答题 maps to canonical 计算题."""
from calculus_agent.question_types import PAPER_QUESTION_TYPES, canonical_question_type
from calculus_agent.runtime.request_guards import (
    _apply_explicit_opt_in_guards,
    _explicit_question_type_counts,
)
from calculus_agent.runtime.paper_request import _explicit_question_addresses
from calculus_agent.schemas import PaperBlueprint
from evaluation.teaching_quality.evaluators.structural import StructuralAssertionGrader


def test_canonical_alias_only_folds_requested_forms():
    assert canonical_question_type("解答题") == "计算题"
    assert canonical_question_type(" 解答 ") == "计算题"
    assert canonical_question_type("简答题") == "unknown"
    assert PAPER_QUESTION_TYPES == ("选择题", "填空题", "计算题", "证明题")


def test_explicit_type_counts_aggregate_calculation_and_answer():
    text = "第三章出100分测试：选择4、填空4、计算2、解答2、证明2。"
    assert _explicit_question_type_counts(text) == {
        "选择题": 4, "填空题": 4, "计算题": 4, "证明题": 2,
    }


def test_generation_tool_preserves_aggregated_teacher_counts():
    message = "第三章出一份试卷，共14题：选择4、填空4、计算2、解答2、证明2。"
    guarded = _apply_explicit_opt_in_guards(
        tool_name="prepare_generation_plan",
        arguments={"question_count": 14, "question_type_requirements": []},
        message=message,
    )
    assert guarded["question_count"] == 14
    counts = {item["question_type"]: item["count"] for item in guarded["question_type_requirements"]}
    assert counts == {"选择题": 4, "填空题": 4, "计算题": 4, "证明题": 2}


def test_section_question_address_accepts_answer_alias():
    addresses = _explicit_question_addresses("把解答题第1题换掉")
    assert len(addresses) == 1
    assert addresses[0].section_type == "计算题"
    assert addresses[0].section_order == 1


def test_blueprint_merges_duplicate_canonical_counts():
    blueprint = PaperBlueprint.model_validate({
        "total_questions": 4,
        "question_type_counts": {"计算题": 2, "解答题": 2},
    })
    assert blueprint.question_type_counts == {"计算题": 4}


def test_frozen_expected_types_are_compared_after_alias_merge():
    actual = {"paper": {"questions": [
        {"question_type": "选择题"} for _ in range(4)
    ] + [
        {"question_type": "填空题"} for _ in range(4)
    ] + [
        {"question_type": "计算题"} for _ in range(4)
    ] + [
        {"question_type": "证明题"} for _ in range(2)
    ]}}
    assertion = {
        "id": "types", "type": "required_question_type", "critical": True,
        "target": "paper.question_type_counts",
        "expected": {"选择题": 4, "填空题": 4, "计算题": 2, "解答题": 2, "证明题": 2},
    }
    result = StructuralAssertionGrader().grade(assertion, actual, {})
    assert result.passed is True
    assert result.observed == {"选择题": 4, "填空题": 4, "计算题": 4, "证明题": 2}
