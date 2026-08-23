from tests.evals.graders.acceptance_grader import grade_acceptance


def test_required_lifecycle_tool_is_a_hard_acceptance_boundary():
    result = grade_acceptance(
        object(),
        {"status": "completed", "trace": {"tool_calls": []}},
        {"required_tools": ["confirm_generation"], "statuses": ["completed"]},
    )

    assert not result["passed"]
    assert "required tools not called" in result["errors"][0]
