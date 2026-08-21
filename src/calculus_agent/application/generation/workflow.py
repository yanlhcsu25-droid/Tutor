"""Application orchestration for the Generation lifecycle."""

from __future__ import annotations

from typing import Any

from calculus_agent.agent.schemas import GenerationPlanPatch
from calculus_agent.agent.services.generation import GenerationService, NoPendingGenerationError
from calculus_agent.agent.state.service import RuntimeStateService
from calculus_agent.agent.tool_registry import AgentExecutionContext, ExecutedTool


class GenerationWorkflow:
    """Keep generation lifecycle orchestration outside the Tool adapter."""

    def __init__(self, context: AgentExecutionContext) -> None:
        self.context = context
        self.service = GenerationService(
            session=context.session,
            store=context.state_store,
            conversation_id=context.conversation_id,
            expected_pending_generation_version=context.expected_pending_generation_version,
            runtime_state_service=RuntimeStateService(context.session),
        )

    def prepare(self, raw: Any) -> ExecutedTool:
        self.context.mark_workflow("generation")
        preview = self.service.preview(GenerationPlanPatch.model_validate(raw))
        return ExecutedTool(
            payload=preview.model_dump(mode="json"),
            status="waiting_confirmation" if preview.ok else "needs_clarification",
            result_fields={
                "generation_preview": preview,
                "warnings": preview.warnings,
                "blocking_errors": preview.blocking_errors,
                "clarification_questions": preview.clarification_questions,
            },
        )

    def confirm(self) -> ExecutedTool:
        self.context.mark_workflow("generation")
        try:
            result = self.service.confirm()
        except NoPendingGenerationError:
            return ExecutedTool(
                payload={
                    "ok": False,
                    "code": "no_pending_generation",
                    "message": "当前没有等待确认的组卷方案。",
                },
                status="failed",
                result_fields={"blocking_errors": ["no_pending_generation"]},
            )
        status = (
            "completed"
            if result.ok
            else "needs_clarification"
            if result.needs_clarification
            else "failed"
        )
        if result.ok:
            self.context.paper_id = str(result.paper_id)
            self.context.version_id = str(result.version_id)
        return ExecutedTool(
            payload=result.model_dump(mode="json"),
            status=status,
            result_fields={
                "paper": result,
                "warnings": result.warnings,
                "blocking_errors": result.blocking_errors,
                "clarification_questions": result.clarification_questions,
            },
        )
