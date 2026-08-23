"""Architecture contracts for the Runtime facade and coordinator boundary."""

from calculus_agent.runtime import agent_runtime, coordinator


def test_agent_runtime_is_a_compatibility_facade():
    assert agent_runtime.run_teacher_agent is coordinator.run_teacher_agent
    assert agent_runtime.build_teacher_agent_backend is coordinator.build_teacher_agent_backend
    # Historical private helper imports remain valid during the migration.
    assert agent_runtime._explicit_question_addresses is coordinator._explicit_question_addresses


def test_coordinator_uses_extracted_runtime_boundaries():
    source = open(coordinator.__file__).read()
    assert "calculus_agent.orchestration" not in source
    for module in (
        "grounding_policy", "tool_exposure_policy", "model_turn",
        "model_turn_executor", "tool_execution", "response_policy",
        "finalization_policy", "paper_request",
    ):
        assert f"runtime.{module}" in source
