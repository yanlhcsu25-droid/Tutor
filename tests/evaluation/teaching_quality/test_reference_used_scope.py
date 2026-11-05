from __future__ import annotations

import json
from evaluation.teaching_quality.evaluators.models import JudgeOutput, StructuralAssertionResult
from evaluation.teaching_quality.evaluators.scoring import score_case
from evaluation.teaching_quality.evaluators.semantic import ModelBackedSemanticAssertionEvaluator


class RecordingBackend:
    def __init__(self, passed=True):
        self.passed = passed
        self.payload = None
        self.prompt = ''

    def complete(self, prompt):
        self.prompt = prompt
        self.payload = json.loads(prompt.split('INPUT_JSON\n', 1)[1])
        return {'passed': self.passed, 'evidence': ['observed output'], 'reason': 'test verdict'}


def test_reference_used_scope_strips_unrelated_profile():
    backend = RecordingBackend()
    evaluator = ModelBackedSemanticAssertionEvaluator(backend)
    assertion = {'id': 'plan_reference', 'type': 'reference_used', 'target': 'response', 'expected': '直接做20道难题'}
    setup = {'proposed_plan': '直接做20道难题', 'student_profile': {'weaknesses': ['极限']}}
    actual = {'response': '直接做20道难题可能不合理，题目过难。'}
    assert evaluator.evaluate(assertion, actual, setup).passed
    assert backend.payload['setup'] == {'reference': '直接做20道难题'}
    assert backend.payload['actual_output'] == actual
    assert 'strictly scoped presence-of-engagement check' in backend.prompt


def test_reference_path_and_other_assertion_behavior():
    backend = RecordingBackend()
    evaluator = ModelBackedSemanticAssertionEvaluator(backend)
    setup = {'student_profile': {'current_performance': {'basic_accuracy': 0.90}}, 'other': 'irrelevant'}
    assertion = {'id': 'state', 'type': 'reference_used', 'expected': 'student_profile.current_performance'}
    evaluator.evaluate(assertion, {'response': '90%以后提高难度'}, setup)
    assert backend.payload['setup'] == {'reference': {'basic_accuracy': .9}}
    evaluator.evaluate({'id': 'ground', 'type': 'source_grounded', 'expected': 'student_profile'}, {'response': 'test'}, setup)
    assert backend.payload['setup'] == setup


def test_regrading_a_corrected_reference_does_not_make_39_a_pass():
    result = StructuralAssertionResult(assertion_id='plan_reference', type='reference_used', passed=True,
                                      critical=True, reason='reviewed semantic decision')
    judge = JudgeOutput(case_id='TQ-CQ-006',
                        scores={'correctness': 5, 'actionability': 3, 'pedagogical_quality': 4, 'grounding': 3, 'clarity': 5},
                        rationale={axis: 'ok' for axis in ('correctness', 'actionability', 'pedagogical_quality', 'grounding', 'clarity')},
                        critical_quality_failure=False, failure_tags=[], overall_comment='ok')
    weights = {'correctness': .25, 'actionability': .25, 'pedagogical_quality': .20, 'grounding': .20, 'clarity': .10}
    score = score_case([result], judge, weights)
    assert score.status == 'PARTIAL'
    assert score.weighted_score == 3.9
