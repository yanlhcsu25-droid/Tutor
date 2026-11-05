from calculus_agent.evaluations.reliability_report import markdown, summarize


def _case(*, passed=True, status="completed", category="generation", tools=2):
    return {
        "passed": passed,
        "category": category,
        "expected": {"status": status},
        "actual": {
            "status": status,
            "trace": {"tool_calls": [{} for _ in range(tools)]},
        },
        "graders": [{"grader": "state", "passed": passed}],
    }


def test_reliability_summary_uses_observable_eval_outcomes():
    report = {"results": [
        _case(status="waiting_confirmation", tools=1),
        _case(status="needs_clarification", category="error_handling", tools=3),
        _case(passed=False, status="completed", tools=2),
    ]}
    summary = summarize(report, variant="state-policy")
    assert summary["task_success_rate"] == 0.6667
    assert summary["false_success_rate"] == 0.3333
    assert summary["confirmation_safety_rate"] == 1.0
    assert summary["recovery_rate"] == 1.0
    assert summary["average_tool_calls"] == 2.0
    assert len(summary["cases"]) == 3


def test_summary_aggregates_latency_tokens_and_breakdowns_without_zero_filling():
    first = _case(status="completed", tools=1)
    first.update({
        "task_success": True, "model_calls": 2, "tool_calls": 1, "total_turns": 1,
        "latency_ms": 100, "input_tokens": 80, "output_tokens": 20,
        "total_tokens": 100, "difficulty": "easy",
        "constraint_preserved": True, "side_effect_violation": False,
        "grounding_correct": True,
    })
    second = _case(passed=False, status="failed", tools=3)
    second.update({
        "task_success": False, "model_calls": 4, "tool_calls": 3, "total_turns": 2,
        "latency_ms": 300, "input_tokens": None, "output_tokens": None,
        "total_tokens": None, "difficulty": "hard",
        "constraint_preserved": False, "side_effect_violation": True,
        "grounding_correct": False,
    })

    summary = summarize({"results": [first, second]}, variant="state-policy")

    assert summary["average_latency_ms"] == 200
    assert summary["p50_latency_ms"] == 200
    assert summary["p95_latency_ms"] == 290
    assert summary["average_total_tokens"] == 100
    assert summary["token_usage_coverage"] == 0.5
    assert summary["tokens_per_successful_task"] is None
    assert summary["constraint_preservation_rate"] == 0.5
    assert summary["side_effect_violation_rate"] == 0.5
    assert summary["breakdowns"]["difficulty"]["hard"]["cases"] == 1
    assert summary["breakdowns"]["turn_type"]["multi-turn"]["cases"] == 1


def test_recovery_cohort_uses_expected_case_not_variant_outcome():
    case = _case(passed=False, status="needs_clarification", category="generation")
    case["actual"]["status"] = "completed"

    summary = summarize({"results": [case]}, variant="prompt-only")

    assert summary["recovery_rate"] == 0.0
    assert summary["false_success_rate"] == 1.0


def test_markdown_includes_run_metadata_and_raw_case_failures():
    summary = summarize({
        "metadata": {
            "git_sha": "abc123", "model_ids": ["model-a"], "temperature": 0,
            "dataset_path": "cases.yaml", "dataset_version": "deadbeef",
        },
        "results": [{
            **_case(passed=False, status="failed"),
            "case_id": "FI-001",
            "graders": [{"grader": "state", "passed": False, "errors": ["timeout"]}],
        }],
    }, variant="state-policy")

    rendered = markdown([summary])
    assert "abc123" in rendered
    assert "FI-001" in rendered
    assert "timeout" in rendered


def test_markdown_supports_real_variant_comparison_without_fake_baselines():
    rendered = markdown([{
        "variant": "state-policy", "case_count": 10,
        "task_success_rate": 0.9, "false_success_rate": 0.0,
        "confirmation_safety_rate": 1.0, "recovery_rate": 0.5,
        "grounding_rate": None, "state_consistency_rate": 1.0,
        "average_tool_calls": 2.9, "total_tool_calls": 29,
    }])
    assert "state-policy" in rendered
    assert "90.0%" in rendered
    assert "N/A" in rendered
