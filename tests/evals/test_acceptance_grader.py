from tests.evals.graders.acceptance_grader import grade_acceptance


def test_required_lifecycle_tool_is_a_hard_acceptance_boundary():
    result = grade_acceptance(
        object(),
        {"status": "completed", "trace": {"tool_calls": []}},
        {"required_tools": ["confirm_generation"], "statuses": ["completed"]},
    )

    assert not result["passed"]
    assert "required tools not called" in result["errors"][0]


def test_acceptance_grades_tool_order_counts_errors_and_backend_replay():
    actual = {
        "status": "failed",
        "backend_calls": 1,
        "trace": {"tool_calls": [
            {"tool_name": "read_paper", "result": {"ok": True}},
            {"tool_name": "preview_paper_changes", "result": {
                "ok": False, "code": "tool_timeout",
            }},
        ]},
    }

    result = grade_acceptance(object(), actual, {
        "tool_order": ["read_paper", "preview_paper_changes"],
        "tool_counts": {"preview_paper_changes": 1},
        "required_error_codes": ["tool_timeout"],
        "max_backend_calls": 1,
    })

    assert result["passed"]
