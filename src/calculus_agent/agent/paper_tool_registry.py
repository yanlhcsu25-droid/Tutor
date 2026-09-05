"""Stable paper tool surface for generation, reinforcement, reading, changes, and versions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from calculus_agent.models import AdjustmentPlanRecord
from .paper_change_service import PaperChangeRequest
from .schemas import GenerationPlanPatch, PrepareReinforcementPlanInput
from calculus_agent.application.generation import GenerationWorkflow
from calculus_agent.application.paper import PaperWorkflow
from .services.reinforcement import ReinforcementError, ReinforcementService
from .state.service import RuntimeStateService, WorkspaceService
from .tool_registry import AgentExecutionContext, AgentTool, EmptyInput, ExecutedTool
from .tools.analysis_tools import analyze_paper
from .tools.read_tools import ReadCurrentPaperInput, read_current_paper
from .tools.version_tools import run_version_operation
from .version_parser import VersionOperationIntent


PAPER_TOOL_NAMES: tuple[str, ...] = (
    "read_paper",
    "analyze_paper",
    "prepare_generation_plan",
    "prepare_reinforcement_plan",
    "confirm_generation",
    "preview_paper_changes",
    "confirm_paper_changes",
    "discard_pending_plan",
    "operate_paper_version",
)


class VersionOperationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["undo", "redo", "restore"]
    target_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def target_matches_action(self) -> "VersionOperationInput":
        if self.action == "restore" and self.target_version is None:
            raise ValueError("restore 必须提供 target_version")
        if self.action != "restore" and self.target_version is not None:
            raise ValueError("只有 restore 可以提供 target_version")
        return self


def _failed(code: str, message: str) -> ExecutedTool:
    return ExecutedTool(
        payload={"ok": False, "code": code, "message": message},
        status="failed",
        result_fields={"blocking_errors": [code]},
    )


def build_paper_tools(context: AgentExecutionContext) -> dict[str, AgentTool]:
    session = context.session
    store = context.state_store

    generation_workflow = GenerationWorkflow(context)
    reinforcement_service = ReinforcementService(
        session=session,
        generation_service=generation_workflow.service,
    )
    paper_workflow = PaperWorkflow(context)

    def sync_workspace_version(version_id: str | None) -> None:
        if not context.conversation_id or not version_id:
            return
        WorkspaceService(session).update(
            context.conversation_id,
            {
                "active_type": "paper",
                "current_paper_id": context.paper_id,
                "current_version_id": version_id,
            },
        )

    def read(raw: BaseModel) -> ExecutedTool:
        result = read_current_paper(
            session,
            current_paper_version_id=context.version_id or context.paper_id,
            request=ReadCurrentPaperInput.model_validate(raw),
        )
        return ExecutedTool(
            payload=result.model_dump(mode="json"),
            status="completed" if result.ok else "failed",
            result_fields={
                "paper_read": result,
                "blocking_errors": [] if result.ok else [result.code or "paper_read_failed"],
            },
        )

    def analyze(_raw: BaseModel) -> ExecutedTool:
        current = context.version_id or context.paper_id
        if not current:
            return _failed("no_current_paper", "当前还没有可分析的试卷。")
        result = analyze_paper(session, paper_id=current)
        return ExecutedTool(
            payload=result.model_dump(mode="json"),
            status="completed" if result.ok else "failed",
            result_fields={
                "analysis": result,
                "blocking_errors": result.blocking_errors,
            },
        )

    def prepare_generation(raw: BaseModel) -> ExecutedTool:
        return generation_workflow.prepare(raw)

    def prepare_reinforcement(raw: BaseModel) -> ExecutedTool:
        current_paper_id = context.version_id or context.paper_id
        if not current_paper_id:
            return _failed(
                "no_current_paper",
                "当前没有可操作的试卷版本，无法根据错题生成巩固卷。",
            )
        input_model = PrepareReinforcementPlanInput.model_validate(raw)
        try:
            result = reinforcement_service.prepare(
                current_paper_id,
                input_model.items,
            )
        except ReinforcementError as exc:
            if exc.code == "feedback_question_not_found":
                return ExecutedTool(
                    payload={
                        "ok": False,
                        "code": exc.code,
                        "message": exc.message,
                    },
                    status="needs_clarification",
                    result_fields={
                        "warnings": [],
                        "blocking_errors": [],
                        "clarification_questions": [
                            exc.message,
                        ],
                    },
                )
            return _failed(exc.code, exc.message)

        preview = result.preview
        return ExecutedTool(
            payload={
                "ok": preview.ok,
                "reinforcement_context": result.context.model_dump(mode="json"),
                "generation_preview": preview.model_dump(mode="json"),
            },
            status="waiting_confirmation" if preview.ok else "needs_clarification",
            result_fields={
                "reinforcement_context": result.context,
                "generation_preview": preview,
                "warnings": [*preview.warnings, *result.context.warnings],
                "blocking_errors": preview.blocking_errors,
                "clarification_questions": preview.clarification_questions,
            },
        )

    def confirm_generation(_raw: BaseModel) -> ExecutedTool:
        return generation_workflow.confirm()

    def preview_changes(raw: BaseModel) -> ExecutedTool:
        return paper_workflow.preview(raw)

    def confirm_changes(_raw: BaseModel) -> ExecutedTool:
        return paper_workflow.confirm()

    def discard_pending(_raw: BaseModel) -> ExecutedTool:
        discarded: list[str] = []
        if store and context.conversation_id:
            if hasattr(store, "get_generation") and store.get_generation(context.conversation_id) is not None:
                store.clear_generation(context.conversation_id)
                discarded.append("generation")
            if hasattr(store, "get_adjustment"):
                plan_id = store.get_adjustment(context.conversation_id)
                if plan_id:
                    record = session.get(AdjustmentPlanRecord, plan_id)
                    if record is not None and record.status == "pending":
                        record.status = "stale"
                    store.clear_adjustment(context.conversation_id)
                    discarded.append("paper_change")
            legacy = store.get(context.conversation_id)
            if legacy is not None:
                store.clear(context.conversation_id)
                discarded.append("legacy_replacement")
            session.flush()
            # Phase 2: discarding an uncommitted plan returns the Agent to idle.
            if discarded:
                RuntimeStateService(session).transition(
                    context.conversation_id, "idle"
                )
        return ExecutedTool(
            payload={
                "ok": True,
                "discarded": bool(discarded),
                "discarded_types": discarded,
                "paper_unchanged": True,
            },
            status="completed",
        )

    def version_operation(raw: BaseModel) -> ExecutedTool:
        values = VersionOperationInput.model_validate(raw)
        if not context.paper_id or not context.version_id:
            return _failed("no_current_paper", "当前还没有可操作的试卷。")
        result = run_version_operation(
            session,
            paper_id=context.paper_id,
            version_id=context.version_id,
            intent=VersionOperationIntent(
                action=values.action,
                target_version=values.target_version,
            ),
        )
        if result.ok:
            context.paper_id = result.current_version_id
            context.version_id = result.current_version_id
            sync_workspace_version(result.current_version_id)
            if store and context.conversation_id:
                if store.get(context.conversation_id) is not None:
                    store.clear(context.conversation_id)
                if hasattr(store, "clear_adjustment"):
                    store.clear_adjustment(context.conversation_id)
        return ExecutedTool(
            payload=result.model_dump(mode="json"),
            status="completed" if result.ok else "failed",
            result_fields={
                "version_operation": result,
                "warnings": result.warnings,
                "blocking_errors": result.blocking_errors,
            },
        )

    tools = [
        AgentTool(
            "read_paper",
            "Read the concrete current paper. Use teacher-facing addresses with section_type + section_order. Read-only.",
            ReadCurrentPaperInput,
            read,
        ),
        AgentTool(
            "analyze_paper",
            "Deterministically analyze score, question-type, difficulty, and knowledge distributions of the current paper. Read-only.",
            EmptyInput,
            analyze,
        ),
        AgentTool(
            "prepare_generation_plan",
            "Create or patch a validated plan for a NEW paper without selecting or mutating questions. knowledge_preferences must use canonical knowledge names observed from inspect_curriculum/question-bank tools, not unverified teacher wording. Preserve teacher-explicit question types/counts and semantically mapped goals exactly even when observed supply is insufficient; report the shortage instead of substituting another question type. This is the generation preview boundary.",
            GenerationPlanPatch,
            prepare_generation,
        ),
        AgentTool(
            "prepare_reinforcement_plan",
            "Prepare a new reinforcement paper generation plan from wrong-question feedback on the current concrete Paper version. Python resolves the referenced PaperItems and derives knowledge priorities deterministically. This tool creates/updates the existing pending generation preview but does NOT create a Paper. Use confirm_generation only after explicit teacher confirmation.",
            PrepareReinforcementPlanInput,
            prepare_reinforcement,
        ),
        AgentTool(
            "confirm_generation",
            "Create a new paper from the currently pending validated generation plan. Call only after explicit teacher confirmation.",
            EmptyInput,
            confirm_generation,
        ),
        AgentTool(
            "preview_paper_changes",
            "Preview one coherent change plan for an EXISTING paper. operations may combine replace_question, remove_question, add_questions, change_question_score, and change_question_type_distribution. target_total_score is optional. The tool never mutates Paper state; Python resolves and validates the whole plan and later confirmation applies it atomically.",
            PaperChangeRequest,
            preview_changes,
        ),
        AgentTool(
            "confirm_paper_changes",
            "Atomically apply the currently pending paper-change plan to the current paper version after explicit teacher confirmation. Never claim changes were applied without this tool observation.",
            EmptyInput,
            confirm_changes,
        ),
        AgentTool(
            "discard_pending_plan",
            "Discard the current uncommitted paper/generation plan. This only clears pending state and never rolls back or changes the current paper.",
            EmptyInput,
            discard_pending,
        ),
        AgentTool(
            "operate_paper_version",
            "Operate the current paper version chain with action=undo, redo, or restore. restore requires target_version.",
            VersionOperationInput,
            version_operation,
        ),
    ]
    return {tool.name: tool for tool in tools}
