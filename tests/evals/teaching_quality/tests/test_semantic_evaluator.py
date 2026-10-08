from __future__ import annotations

import pytest

from tests.evals.teaching_quality.grader.semantic import (
    ModelBackedSemanticAssertionEvaluator,
    SemanticEvaluatorError,
)
from tests.evals.teaching_quality.grader.structural import StructuralAssertionGrader


class ScriptedBackend:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return self.response


@pytest.mark.parametrize(
    "assertion_type",
    [
        "required_content", "forbidden_content", "source_grounded",
        "reference_used", "insufficiency_disclosed",
    ],
)
def test_semantic_evaluator_supports_structural_fact_types(assertion_type):
    backend = ScriptedBackend(
        {"passed": True, "evidence": ["回答中的具体证据"], "reason": "事实成立"}
    )
    evaluator = ModelBackedSemanticAssertionEvaluator(backend)
    decision = evaluator.evaluate(
        {"id": "a", "type": assertion_type, "critical": True, "target": "response"},
        {"response": {"text": "回答中的具体证据"}},
        {"student_profile": {"evidence": ["已知事实"]}},
    )
    assert decision.passed
    assert decision.evidence == ["回答中的具体证据"]
    prompt = backend.prompts[0]
    assert '"actual_output"' in prompt
    assert '"setup"' in prompt
    assert "Do not score teaching quality" in prompt


def test_semantic_output_rejects_quality_scores():
    evaluator = ModelBackedSemanticAssertionEvaluator(
        ScriptedBackend(
            {
                "passed": True, "evidence": ["证据"], "reason": "成立",
                "score": 5,
            }
        )
    )
    with pytest.raises(SemanticEvaluatorError, match="schema"):
        evaluator.evaluate(
            {"id": "a", "type": "required_content", "critical": True}, {}, {}
        )


def test_semantic_output_rejects_malformed_json():
    evaluator = ModelBackedSemanticAssertionEvaluator(ScriptedBackend("not-json"))
    with pytest.raises(SemanticEvaluatorError, match="malformed JSON"):
        evaluator.evaluate(
            {"id": "a", "type": "required_content", "critical": True}, {}, {}
        )


def test_semantic_decision_flows_into_structural_result_with_evidence():
    evaluator = ModelBackedSemanticAssertionEvaluator(
        ScriptedBackend(
            {
                "passed": False,
                "evidence": ["下一节重点学习定积分"],
                "reason": "把禁止内容作为教学任务",
            }
        )
    )
    result = StructuralAssertionGrader(semantic_evaluator=evaluator).grade(
        {
            "id": "later", "type": "forbidden_content", "critical": True,
            "target": "response", "forbidden": ["定积分"], "match_mode": "semantic",
            "violation_mode": "instructional_use", "mention_allowed": True,
        },
        {"response": {"text": "下一节重点学习定积分"}},
        {},
    )
    assert not result.passed
    assert result.observed == ["下一节重点学习定积分"]
    assert result.reason == "把禁止内容作为教学任务"
