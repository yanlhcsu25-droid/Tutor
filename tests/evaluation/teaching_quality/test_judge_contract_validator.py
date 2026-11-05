from __future__ import annotations

import copy
import json
from pathlib import Path

import yaml

from evaluation.teaching_quality.evaluators.behavioral import build_judge_input
from evaluation.teaching_quality.evaluators.contract_validator import (
    audit_judge_contract,
    canonical_actual_counts,
)
from evaluation.teaching_quality.runners.run_dev_eval import grade_result


ROOT = Path("evaluation/teaching_quality")
REPORT = Path(
    "evaluation/artifacts/reports/teaching_quality/runs/"
    "phase5-pq002-unassisted-20261009T084734Z/report.json"
)


def _case_actual_behavioral():
    case = next(
        item for item in yaml.safe_load(
            (ROOT / "datasets/paper_quality_v1.yaml").read_text()
        )["cases"]
        if item["case_id"] == "TQ-PQ-002"
    )
    result = json.loads(REPORT.read_text())["results"][0]
    return case, result["actual_output"], result["grading"]["behavioral"]


def _audit(payload):
    case, actual, _ = _case_actual_behavioral()
    judge_input = build_judge_input(case, actual, judge_version="v2")
    return audit_judge_contract(
        payload,
        judge_version="v2",
        suite="paper_quality",
        canonical_aliases=judge_input["canonical_question_type_aliases"],
        canonical_requested_counts=judge_input["canonical_requested_question_type_counts"],
        actual_canonical_counts=canonical_actual_counts(actual),
    )


def test_saved_pq002_alias_penalty_is_a_confirmed_contract_violation():
    _, _, payload = _case_actual_behavioral()

    audit = _audit(payload)

    assert audit.status == "JUDGE_CONTRACT_VIOLATION"
    assert audit.rule_id == "canonical_alias_has_no_separate_label_requirement"
    assert audit.canonical_counts_match is True
    assert audit.evidence


def test_canonical_alias_double_count_claim_is_a_contract_violation():
    case, actual, _ = _case_actual_behavioral()
    judge_input = build_judge_input(case, actual, judge_version="v2")
    _, _, payload = _case_actual_behavioral()
    payload = copy.deepcopy(payload)
    payload["rationale"]["question_type_balance"] = (
        "The 解答 alias canonicalizes to 计算题, making the target 6 calculation-type "
        "questions. The mismatch results in a moderate score."
    )

    audit = audit_judge_contract(
        payload,
        judge_version="v2",
        suite="paper_quality",
        canonical_aliases=judge_input["canonical_question_type_aliases"],
        canonical_requested_counts=judge_input["canonical_requested_question_type_counts"],
        actual_canonical_counts=canonical_actual_counts(actual),
    )

    assert audit.status == "JUDGE_CONTRACT_VIOLATION"
    assert audit.rule_id == "canonical_alias_count_double_counted"
    assert "target 6 calculation" in audit.evidence[0]


def test_substantive_balance_low_score_is_not_misclassified():
    _, _, payload = _case_actual_behavioral()
    payload = copy.deepcopy(payload)
    payload["rationale"]["question_type_balance"] = (
        "Canonical counts match exactly. The paper still concentrates most points "
        "in short computational tasks, with limited extended reasoning variety."
    )
    payload["overall_comment"] = "Canonical labeling is correct; the concern is cognitive variety."

    audit = _audit(payload)

    assert audit.status == "VALID"


def test_ambiguous_alias_discussion_is_retained_without_retry_signal():
    _, _, payload = _case_actual_behavioral()
    payload = copy.deepcopy(payload)
    payload["rationale"]["question_type_balance"] = (
        "The 解答 alias was canonicalized, but the overall balance needs review."
    )
    payload["overall_comment"] = "Further review is needed."

    audit = _audit(payload)

    assert audit.status == "INCONCLUSIVE"


class _SequenceAdapter:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, _prompt):
        payload = self.responses.pop(0)
        index = len(self.calls) + 1
        self.calls.append({
            "provider": "offline",
            "requested_model": "fixture",
            "response_model": "fixture",
            "temperature": 0,
            "max_tokens": 2048,
            "latency_seconds": 0,
            "usage": {"total_tokens": 1},
            "response_id": f"offline-{index}",
            "finish_reason": "stop",
            "provider_attempts": [{"attempt": 1, "status": "success"}],
        })
        return payload

    def provenance(self):
        return {
            "config": {"provider": "offline", "model": "fixture", "temperature": 0},
            "last_call": self.calls[-1] if self.calls else None,
            "calls": list(self.calls),
        }


def test_confirmed_violation_retries_once_and_preserves_both_responses():
    case, actual, invalid = _case_actual_behavioral()
    valid = copy.deepcopy(invalid)
    valid["scores"]["question_type_balance"] = 5
    valid["failure_tags"] = []
    valid["rationale"]["question_type_balance"] = (
        "The actual canonical distribution exactly matches the requested distribution."
    )
    valid["overall_comment"] = "Canonical aliases are correctly normalized."
    adapter = _SequenceAdapter([invalid, valid])

    result = grade_result(case, actual, adapter, judge_version="v2")

    assert len(adapter.calls) == 2
    assert result["status"] == "SCORED"
    assert result["judge_validity"] == "VALID"
    assert result["judge_contract_violation"] is True
    assert result["score"]["weighted_score"] == 4.15
    assert result["judge_audit"]["retry_trigger"] == "JUDGE_CONTRACT_VIOLATION"
    assert len(result["judge_audit"]["attempts"]) == 2
    assert len(result["judge_audit"]["prompt_file_sha256"]) == 64
    assert len(result["judge_audit"]["rendered_prompt_sha256"]) == 64
    assert len(result["judge_audit"]["judge_input_sha256"]) == 64
    assert result["judge_audit"]["canonical_requested_counts"] == {
        "选择题": 4, "填空题": 4, "计算题": 4, "证明题": 2,
    }
    assert result["judge_audit"]["actual_canonical_counts"] == {
        "选择题": 4, "填空题": 4, "计算题": 4, "证明题": 2,
    }
    assert result["judge_audit"]["generation_parameters"]["temperature"] == 0
    assert result["judge_audit"]["generation_parameters"]["response_format"] == {
        "type": "json_object",
    }
    assert result["judge_audit"]["attempts"][0]["provider_call"]["response_id"] == "offline-1"
    assert result["judge_audit"]["attempts"][0]["raw_response"] == invalid
    assert result["judge_audit"]["attempts"][1]["raw_response"] == valid


def test_second_contract_violation_becomes_invalid_without_more_retries():
    case, actual, invalid = _case_actual_behavioral()
    adapter = _SequenceAdapter([invalid, copy.deepcopy(invalid)])

    result = grade_result(case, actual, adapter, judge_version="v2")

    assert len(adapter.calls) == 2
    assert result["status"] == "JUDGE_INVALID"
    assert result["judge_validity"] == "JUDGE_INVALID"
    assert result["score"] is None
    assert result["behavioral"] is None


def test_valid_low_score_does_not_retry_until_pass():
    case, actual, low = _case_actual_behavioral()
    low = copy.deepcopy(low)
    low["rationale"]["question_type_balance"] = (
        "Canonical counts match. The low score reflects limited cognitive variety "
        "inside the four calculation questions, not alias labeling."
    )
    low["overall_comment"] = "The concern is substantive task variety."
    adapter = _SequenceAdapter([low])

    result = grade_result(case, actual, adapter, judge_version="v2")

    assert len(adapter.calls) == 1
    assert result["judge_validity"] == "VALID"
    assert result["score"]["weighted_score"] == 3.85
