"""A recovery action must reach the public clarification contract intact."""

from types import SimpleNamespace

from calculus_agent.application.generation.workflow import GenerationWorkflow
from calculus_agent.agent.tools.paper_tools import GeneratePaperToolResult
from calculus_agent.generation_diagnosis.schemas import RecoveryAction


def test_ask_user_recovery_is_projected_without_mutating_service_result():
    original = GeneratePaperToolResult(
        ok=False,
        blocking_errors=["insufficient_candidates"],
        recovery_action=RecoveryAction(
            action_type="ask_user", reason="题库资源不足", options=["补充题库"],
        ),
    )
    workflow = GenerationWorkflow.__new__(GenerationWorkflow)
    workflow.context = SimpleNamespace(mark_workflow=lambda _: None)
    workflow.service = SimpleNamespace(confirm=lambda: original)

    executed = workflow.confirm()

    assert executed.status == "needs_clarification"
    assert executed.payload["needs_clarification"] is True
    assert executed.result_fields["clarification_questions"]
    assert executed.payload["recovery_action"]["options"] == ["补充题库"]
    assert executed.result_fields["paper"].paper_id is None
    assert original.needs_clarification is False
    assert original.clarification_questions == []
