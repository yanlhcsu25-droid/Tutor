"""Tool boundary for lightweight TeachingPlanning drafts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from calculus_agent.application.teaching_scope import (
    TeachingScopeDecision,
    TeachingScopeValidationError,
    validate_teaching_scope_decision,
)

from ..schemas import TeachingPlanningDraft
from ..tool_registry import AgentExecutionContext, AgentTool, ExecutedTool


class SelectTeachingScopeInput(TeachingScopeDecision):
    # Backward-compatible isolation: stale models may still emit the removed
    # scope_level field. Ignore it and derive type exclusively from DB facts.
    model_config = ConfigDict(extra="ignore")


class PrepareTeachingPlanningDraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft: TeachingPlanningDraft
    topic: str | None = None
    waiting_for_scope: bool = True


def build_teaching_planning_tools(context: AgentExecutionContext) -> list[AgentTool]:
    def select_scope(raw: BaseModel) -> ExecutedTool:
        values = SelectTeachingScopeInput.model_validate(raw)
        candidates = context.inspection_state.get("selectable_teaching_scopes") or []
        candidate_ids = {
            item.get("node_id")
            for item in candidates
            if item.get("node_id")
        }
        if not candidate_ids:
            return ExecutedTool(
                payload={
                    "ok": False,
                    "code": "curriculum_retrieval_required",
                    "retry_instruction": "请先重新检索教材候选，再从返回结果中选择。",
                },
                status="completed",
                result_fields={},
            )
        try:
            validated = validate_teaching_scope_decision(
                context.session,
                decision=values,
                candidate_node_ids=candidate_ids,
            )
        except TeachingScopeValidationError as exc:
            return ExecutedTool(
                payload={
                    "ok": False,
                    "code": exc.code,
                    "retry_instruction": "请仅根据上一条检索结果重新选择合适的教材候选；不要猜测额外标识。",
                },
                status="completed",
                result_fields={},
            )

        context.inspection_state["teaching_scope_decision"] = validated.model_dump(mode="json")
        context.inspection_state["validated_scope_names"] = validated.validated_scope_names
        if context.state_store is not None and context.conversation_id:
            memory = context.state_store.get_memory(context.conversation_id)
            current = (
                memory.active_task
                if memory.active_task.get("type") == "teaching_planning"
                else {}
            )
            memory.active_task = {
                **current,
                "type": "teaching_planning",
                "status": "scope_selected",
                "waiting_for_scope": False,
                "scope_decision": validated.model_dump(mode="json"),
            }
            context.state_store.set_memory(context.conversation_id, memory)
        return ExecutedTool(
            payload={
                "ok": True,
                "scope_selected": True,
                **validated.model_dump(mode="json"),
                "next_step": "Use validated_scope_names with inspect_curriculum and inspect_question_bank before create_teaching_design.",
            },
            status="completed",
            result_fields={},
        )

    def prepare(raw: BaseModel) -> ExecutedTool:
        values = PrepareTeachingPlanningDraftInput.model_validate(raw)
        if context.state_store is None or not context.conversation_id:
            return ExecutedTool(
                payload={"ok": False, "code": "teaching_planning_requires_conversation"},
                status="failed",
                result_fields={"blocking_errors": ["teaching_planning_requires_conversation"]},
            )
        memory = context.state_store.get_memory(context.conversation_id)
        memory.active_task = {
            "type": "teaching_planning",
            "status": "awaiting_scope" if values.waiting_for_scope else "drafted",
            "topic": values.topic,
            "waiting_for_scope": values.waiting_for_scope,
            "draft": values.draft.model_dump(mode="json"),
        }
        context.state_store.set_memory(context.conversation_id, memory)
        return ExecutedTool(
            payload={"ok": True, "teaching_planning_draft": values.draft.model_dump(mode="json"), "waiting_for_scope": values.waiting_for_scope},
            status="completed",
            result_fields={"teaching_planning_draft": values.draft},
        )

    return [
        AgentTool(
            "select_teaching_scope",
            "Select teaching scope only from selectable_scopes in the immediately preceding retrieve_curriculum_candidates result. Submit selected_node_ids only. Python derives type, validates active textbook ownership and hierarchy, and returns validated_scope_names. reasoning is optional and explanatory only.",
            SelectTeachingScopeInput,
            select_scope,
        ),
        AgentTool(
            "prepare_teaching_planning_draft",
            "Create a structured TeachingPlanning draft for a teaching-goal request without creating TeachingDesign, Paper, or generation constraints. Use this before a curriculum scope is known.",
            PrepareTeachingPlanningDraftInput,
            prepare,
        ),
    ]
