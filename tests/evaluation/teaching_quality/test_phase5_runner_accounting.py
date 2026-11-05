from evaluation.teaching_quality.runners.run_dev_eval import constraint_assistance
from evaluation.teaching_quality.runners.run_paper_benchmark import _run_metrics, summarize


def test_assistance_is_generated_from_business_constraint_contract():
    message = constraint_assistance(
        "第三章出100分测试：选择4、填空4、计算2、解答2、证明2。"
    )

    assert "选择题4道" in message
    assert "计算题4道" in message
    assert "证明题2道" in message
    assert "共14题" in message
    assert "TQ-PQ-002" not in message


def test_runner_uses_saved_judge_usage_and_requires_real_paper_completion():
    result = {
        "request_completed": False,
        "execution_outcome": "recovery_failure",
        "actual_output": {},
        "workflow_checks": {"paper_persisted": False},
        "turns": [],
        "grading": {
            "score": {"status": "PASS", "weighted_score": 5},
            "judge_provider": {
                "calls": [{"usage": {"total_tokens": 17}}],
            },
        },
    }

    metrics = _run_metrics(result)

    assert metrics["judge_tokens"] == 17
    assert metrics["quality_score_raw"] is None
    assert metrics["judge_valid"] is False
    assert metrics["e2e_success"] is False


def test_failure_classes_are_reported_separately():
    records = []
    for index, outcome in enumerate((
        "clarification", "constraint_violation", "recovery_failure", "provider_error",
    )):
        records.append({
            "case_id": "dynamic-case",
            "run_id": str(index),
            "result": {"elapsed_seconds": 1, "actual_output": {}},
            "metrics": {
                "quality_score_raw": None,
                "agent_tokens": None,
                "judge_tokens": None,
                "tool_calls": 0,
                "judge_outcome": None,
                "e2e_success": False,
                "execution_outcome": outcome,
                "structural_pass": False,
                "critical_quality_failure": False,
                "execution_mode": "unassisted",
                "clarification_required": outcome == "clarification",
            },
        })

    summary = summarize(records)

    assert summary["cases"]["dynamic-case"]["execution_outcomes"] == {
        "clarification": 1,
        "constraint_violation": 1,
        "recovery_failure": 1,
        "provider_error": 1,
    }
    assert summary["overall"]["reliability"]["e2e_pass_rate"] == 0


def test_reliability_rates_keep_invalid_judges_in_strict_denominator():
    states = [
        # workflow, structural, judge valid, violation, quality pass
        (False, False, False, False, False),
        (True, False, False, False, False),
        (True, True, False, True, False),
        (True, True, True, False, False),
        (True, True, True, False, True),
    ]
    records = []
    for index, (workflow, structural, valid, violation, quality_pass) in enumerate(states):
        records.append({
            "case_id": "rate-case",
            "result": {"elapsed_seconds": 1, "actual_output": {}},
            "metrics": {
                "quality_score_raw": 4.2 if valid else None,
                "agent_tokens": None,
                "judge_tokens": None,
                "tool_calls": 0,
                "judge_outcome": "PASS" if quality_pass else "PARTIAL" if valid else None,
                "workflow_completed": workflow,
                "request_completed": workflow,
                "structural_pass": structural,
                "judge_valid": valid,
                "judge_contract_violation": violation,
                "quality_pass": quality_pass,
                "strict_e2e_pass": workflow and structural and valid and quality_pass,
                "e2e_success": workflow and structural and valid and quality_pass,
                "final_outcome": "complete_pass" if quality_pass else "not_complete",
                "critical_quality_failure": False,
                "execution_mode": "unassisted",
                "clarification_required": False,
            },
        })

    reliability = summarize(records)["overall"]["reliability"]

    assert reliability["workflow_completion_rate"] == 4 / 5
    assert reliability["structural_pass_rate"] == 3 / 5
    assert reliability["judge_valid_rate"] == 2 / 5
    assert reliability["judge_contract_violation_rate"] == 1 / 5
    assert reliability["quality_pass_rate"] == 1 / 2
    assert reliability["strict_e2e_pass_rate"] == 1 / 5
    assert reliability["total_runs"] == 5
