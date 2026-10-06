from tests.evals.graders.acceptance_grader import grade_acceptance


def test_required_lifecycle_tool_is_a_hard_acceptance_boundary():
    result = grade_acceptance(
        object(),
        {"status": "completed", "trace": {"tool_calls": []}},
        {"required_tools": ["confirm_generation"], "statuses": ["completed"]},
    )

    assert not result["passed"]
    assert "required tools not called" in result["errors"][0]


def test_required_tool_must_have_successful_observation():
    result = grade_acceptance(
        object(),
        {"trace": {"tool_calls": [
            {"tool_name": "confirm_generation", "result": {"ok": False}},
        ]}},
        {"required_tools": ["confirm_generation"]},
    )

    assert not result["passed"]
    assert "no successful observation" in result["errors"][0]


def test_required_tool_business_rejection_satisfies_clarification_case():
    result = grade_acceptance(
        object(),
        {
            "status": "needs_clarification",
            "trace": {"tool_calls": [
                {"tool_name": "prepare_generation_plan", "result": {"ok": False}},
            ]},
        },
        {
            "required_tools": ["prepare_generation_plan"],
            "statuses": ["needs_clarification"],
        },
    )

    assert result["passed"]


def test_expected_failed_tool_can_satisfy_failure_case():
    result = grade_acceptance(
        object(),
        {"trace": {"tool_calls": [
            {"tool_name": "confirm_generation", "result": {"ok": False}},
        ]}},
        {"required_tools": ["confirm_generation"], "require_failed_tool": True},
    )

    assert result["passed"]


def test_acceptance_finds_nested_generation_diagnosis_or_blocking_error():
    actual = {"trace": {"tool_calls": [
        {"tool_name": "prepare_generation_plan", "result": {"ok": True}},
        {
            "tool_name": "confirm_generation",
            "result": {
                "ok": False,
                "blocking_errors": ["insufficient_candidates"],
                "diagnosis": {"code": "type_supply_shortage"},
            },
        },
    ]}}

    result = grade_acceptance(object(), actual, {
        "required_successful_tools": ["prepare_generation_plan"],
        "required_failed_tools": ["confirm_generation"],
        "required_error_codes_any": ["type_supply_shortage", "insufficient_candidates"],
    })

    assert result["passed"]


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
