"""Application orchestration for previewing and confirming paper changes."""

from __future__ import annotations

from typing import Any, Literal

from calculus_agent.models import AdjustmentPlanRecord
from calculus_agent.agent.paper_change_service import (
    PaperChangeRequest,
    PaperChangeService,
    PaperChangeServiceError,
)
from calculus_agent.agent.services.adjustment import AdjustmentService, AdjustmentServiceError
from calculus_agent.agent.services.replacement import ReplacementService, ReplacementServiceError
from calculus_agent.agent.state.service import WorkspaceService
from calculus_agent.agent.tool_registry import AgentExecutionContext, ExecutedTool


class PaperWorkflow:
    """Coordinate paper-change pending state and version synchronization."""

    def __init__(self, context: AgentExecutionContext) -> None:
        self.context = context
        self.session = context.session
        self.store = context.state_store
        self.paper_change_service = PaperChangeService(
            session=self.session,
            store=self.store,
            conversation_id=context.conversation_id,
            paper_id=context.paper_id,
            version_id=context.version_id,
        )
        self.adjustment_service = AdjustmentService(
            session=self.session,
            store=self.store,
            conversation_id=context.conversation_id,
        )
        self.replacement_service = ReplacementService(
            session=self.session,
            store=self.store,
            conversation_id=context.conversation_id,
        )

    def _sync_workspace_version(self, version_id: str | None) -> None:
        if not self.context.conversation_id or not version_id:
            return
        WorkspaceService(self.session).update(
            self.context.conversation_id,
            {
                "active_type": "paper",
                "current_paper_id": self.context.paper_id,
                "current_version_id": version_id,
            },
        )

    def preview(self, raw: Any) -> ExecutedTool:
        self.context.mark_workflow("paper")
        self.paper_change_service.paper_id = self.context.paper_id
        self.paper_change_service.version_id = self.context.version_id
        try:
            result = self.paper_change_service.preview(
                PaperChangeRequest.model_validate(raw)
            )
        except PaperChangeServiceError as exc:
            return ExecutedTool(
                payload={"ok": False, "code": exc.code, "message": exc.message},
                status="failed",
                result_fields={"blocking_errors": [exc.code]},
            )
        status: Literal["completed", "needs_clarification", "waiting_confirmation", "failed"]
        if result.ok and result.plan is not None:
            status = "waiting_confirmation"
        elif result.ok:
            status = "completed"
        elif result.clarification_questions:
            status = "needs_clarification"
        else:
            status = "failed"
        return ExecutedTool(
            payload=result.model_dump(mode="json"),
            status=status,
            result_fields={
                "adjustment_preview": result,
                "warnings": result.warnings,
                "blocking_errors": result.blocking_errors,
                "clarification_questions": result.clarification_questions,
            },
        )

    def confirm(self) -> ExecutedTool:
        self.context.mark_workflow("paper")
        legacy_pending = (
            self.store.get(self.context.conversation_id)
            if self.store and self.context.conversation_id else None
        )
        plan_id = (
            self.store.get_adjustment(self.context.conversation_id)
            if self.store and self.context.conversation_id and hasattr(self.store, "get_adjustment")
            else None
        )
        if plan_id and legacy_pending is not None:
            return self._failed(
                "pending_state_conflict",
                "当前同时存在两种不兼容的待确认试卷修改状态；未执行任何确认操作。",
            )
        if not plan_id and legacy_pending is not None:
            try:
                result = self.replacement_service.confirm()
            except ReplacementServiceError as exc:
                return self._failed(exc.code, exc.message)
            if result.ok and result.new_version_id:
                self.context.paper_id = result.new_version_id
                self.context.version_id = result.new_version_id
                self._sync_workspace_version(result.new_version_id)
            return ExecutedTool(
                payload=result.model_dump(mode="json"),
                status="completed" if result.ok else "failed",
                result_fields={"replacement": result, "blocking_errors": result.blocking_errors},
            )
        if not plan_id:
            return self._failed("no_pending_action", "当前没有等待确认的试卷修改方案。")

        record = self.session.get(AdjustmentPlanRecord, plan_id)
        if record is None:
            return self._failed("adjustment_plan_not_found", "待确认的试卷修改方案不存在。")
        effective_version_id = self.context.version_id or self.context.paper_id or record.base_paper_version_id
        effective_paper_id = self.context.paper_id or effective_version_id
        self.paper_change_service.paper_id = effective_paper_id
        self.paper_change_service.version_id = effective_version_id
        contract_errors = self.paper_change_service.validate_confirmation_contracts(plan_id)
        if contract_errors:
            record.status = "failed"
            record.blocking_errors_json = contract_errors
            self.session.flush()
            return ExecutedTool(
                payload={"ok": False, "plan_id": plan_id, "blocking_errors": contract_errors},
                status="failed", result_fields={"blocking_errors": contract_errors},
            )
        try:
            result = self.adjustment_service.confirm(
                paper_id=effective_paper_id,
                version_id=effective_version_id,
            )
        except AdjustmentServiceError as exc:
            return self._failed(exc.code, exc.message)
        if result.ok:
            self.context.paper_id = result.new_version_id
            self.context.version_id = result.new_version_id
            self._sync_workspace_version(result.new_version_id)
            if self.store and self.context.conversation_id and hasattr(self.store, "clear_adjustment"):
                self.store.clear_adjustment(self.context.conversation_id)
                self.session.flush()
        return ExecutedTool(
            payload=result.model_dump(mode="json"),
            status="completed" if result.ok else "failed",
            result_fields={"adjustment": result, "blocking_errors": result.blocking_errors},
        )

    @staticmethod
    def _failed(code: str, message: str) -> ExecutedTool:
        return ExecutedTool(
            payload={"ok": False, "code": code, "message": message},
            status="failed", result_fields={"blocking_errors": [code]},
        )
