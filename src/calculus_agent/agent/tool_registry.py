"""Tool registry and deterministic executors for the autonomous Teacher Agent."""

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.models import AdjustmentPlanRecord
from calculus_agent.papers.addressing import QuestionAddress, resolve_section_item
from .conversation_state import DatabasePendingReplacementStore
from .schemas import GenerationPlanPatch
from .tools.add_tools import AddQuestionPreview, preview_add_question
from .tools.analysis_tools import (
    KnowledgePreference,
    analyze_paper,
    confirm_adjust_paper,
    preview_adjust_paper,
)
from .tools.read_tools import ReadCurrentPaperInput, read_current_paper
from .services.generation import GenerationService, NoPendingGenerationError
from .services.replacement import (
    ReplacementService,
    ReplacementServiceError,
)
from .tools.version_tools import run_version_operation
from .version_parser import VersionOperationIntent


class EmptyInput(BaseModel):
    pass


class PreviewReplacementInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: QuestionAddress | None = Field(
        default=None,
        description=(
            "Teacher-facing section-local question address. "
            "Example: 填空题第2题 -> "
            '{"section_type":"填空题","section_order":2}.'
        ),
    )
    position: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description=(
            "Legacy internal global PaperItem.position. "
            "Do not derive this from normal teacher-facing numbering."
        ),
    )
    difficulty_direction: Literal["easier", "harder", "same"] | None = None
    target_difficulty: int | None = Field(default=None, ge=1, le=5)
    preserve_knowledge_points: bool = Field(
        default=False,
        description=(
            "Hard constraint. Set true whenever the teacher asks to keep, preserve, "
            "or not change the current question's knowledge points."
        ),
    )
    avoid_similarity_with_question_numbers: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def one_target_address_mode(self):
        if bool(self.address) == bool(self.position):
            raise ValueError("address 和 legacy position 必须且只能提供一个")
        return self


class PreviewAddQuestionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_type: Literal[
        "选择题", "多选题", "填空题", "计算题", "证明题"
    ]
    score: float | None = Field(default=None, gt=0, le=300)


class RestoreVersionInput(BaseModel):
    target_version: int = Field(ge=1)


class PreviewAdjustmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_preferences: list[KnowledgePreference] = Field(default_factory=list)
    question_type_changes: dict[str, int] = Field(default_factory=dict)
    remove_addresses: list[QuestionAddress] = Field(
        default_factory=list,
        description=(
            "Teacher-facing section-local addresses to remove. "
            "Example: 填空题第2题 -> "
            '[{"section_type":"填空题","section_order":2}].'
        ),
    )
    remove_positions: list[int] = Field(
        default_factory=list,
        description=(
            "Legacy internal global PaperItem.position values. "
            "Do not infer these from normal teacher-facing numbering."
        ),
    )
    target_total_score: float | None = Field(
        default=None,
        gt=0,
        validation_alias=AliasChoices("target_total_score", "total_score"),
        description=(
            "Explicit desired final paper score. For deletion, omit this field "
            "to let the removed questions' scores disappear naturally. "
            "Set it only when the teacher explicitly asks to keep or change the total."
        ),
    )

    @model_validator(mode="after")
    def removal_addressing_is_unambiguous(self):
        if self.remove_addresses and self.remove_positions:
            raise ValueError(
                "remove_addresses 和 legacy remove_positions 不能同时使用"
            )
        return self


@dataclass
class AgentExecutionContext:
    session: Session
    conversation_id: str | None
    paper_id: str | None
    version_id: str | None
    state_store: DatabasePendingReplacementStore | None
    expected_pending_generation_version: int | None = None


@dataclass
class ExecutedTool:
    payload: dict[str, Any]
    status: Literal["completed", "needs_clarification", "waiting_confirmation", "failed"]
    result_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    input_model: type[BaseModel]
    execute: Callable[[BaseModel], ExecutedTool]

    def definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }


def _failed(code: str, message: str) -> ExecutedTool:
    return ExecutedTool(
        payload={"ok": False, "code": code, "message": message},
        status="failed",
        result_fields={"blocking_errors": [code]},
    )


def build_agent_tools(context: AgentExecutionContext) -> dict[str, AgentTool]:
    session = context.session
    store = context.state_store

    generation_service = GenerationService(
        session=session,
        store=store,
        conversation_id=context.conversation_id,
        expected_pending_generation_version=(
            context.expected_pending_generation_version
        ),
    )

    def preview_generation(raw: BaseModel) -> ExecutedTool:
        preview = generation_service.preview(
            GenerationPlanPatch.model_validate(raw)
        )
        return ExecutedTool(
            payload=preview.model_dump(mode="json"),
            status=(
                "waiting_confirmation"
                if preview.ok
                else "needs_clarification"
            ),
            result_fields={
                "generation_preview": preview,
                "warnings": preview.warnings,
                "blocking_errors": preview.blocking_errors,
                "clarification_questions": preview.clarification_questions,
            },
        )

    def confirm_generation(_raw: BaseModel) -> ExecutedTool:
        try:
            result = generation_service.confirm()
        except NoPendingGenerationError:
            return _failed(
                "no_pending_generation",
                "当前没有等待确认的组卷方案。",
            )

        status = (
            "completed"
            if result.ok
            else "needs_clarification"
            if result.needs_clarification
            else "failed"
        )
        if result.ok:
            context.paper_id = str(result.paper_id)
            context.version_id = str(result.version_id)

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
        if not (context.version_id or context.paper_id):
            return _failed("no_current_paper", "当前还没有可分析的试卷。")
        result = analyze_paper(session, paper_id=context.version_id or context.paper_id)
        return ExecutedTool(
            payload=result.model_dump(mode="json"),
            status="completed" if result.ok else "failed",
            result_fields={"analysis": result, "blocking_errors": result.blocking_errors},
        )

    replacement_service = ReplacementService(
        session=session,
        store=store,
        conversation_id=context.conversation_id,
    )

    def preview_replacement(raw: BaseModel) -> ExecutedTool:
        values = PreviewReplacementInput.model_validate(raw)
        try:
            outcome = replacement_service.preview(
                paper_id=context.paper_id,
                version_id=context.version_id,
                address=values.address,
                position=values.position,
                difficulty_direction=values.difficulty_direction,
                target_difficulty=values.target_difficulty,
                preserve_knowledge_points=values.preserve_knowledge_points,
                avoid_similarity_with_question_numbers=(
                    values.avoid_similarity_with_question_numbers
                ),
            )
        except ReplacementServiceError as exc:
            return _failed(exc.code, exc.message)

        result = outcome.result
        fields = {
            "replacement_preview": result,
            "warnings": result.warnings,
            "blocking_errors": result.blocking_errors,
        }
        if not result.ok:
            return ExecutedTool(
                result.model_dump(mode="json"),
                "failed",
                fields,
            )

        fields["pending_action"] = outcome.pending_action
        return ExecutedTool(
            {
                **result.model_dump(mode="json"),
                "confirmation_required": True,
            },
            "waiting_confirmation",
            fields,
        )

    def confirm_replacement(_raw: BaseModel) -> ExecutedTool:
        try:
            result = replacement_service.confirm()
        except ReplacementServiceError as exc:
            return _failed(exc.code, exc.message)

        if result.ok:
            context.paper_id = result.new_version_id
            context.version_id = result.new_version_id
        return ExecutedTool(
            result.model_dump(mode="json"),
            "completed" if result.ok else "failed",
            {
                "replacement": result,
                "warnings": result.warnings,
                "blocking_errors": result.blocking_errors,
            },
        )

    def cancel_replacement(_raw: BaseModel) -> ExecutedTool:
        try:
            replacement_service.cancel()
        except ReplacementServiceError as exc:
            return _failed(exc.code, exc.message)
        return ExecutedTool(
            {"ok": True, "cancelled": True},
            "completed",
        )

    def preview_add(raw: BaseModel) -> ExecutedTool:
        values = PreviewAddQuestionInput.model_validate(raw)
        if not (context.version_id or context.paper_id):
            return _failed(
                "no_current_paper",
                "当前还没有可新增题目的试卷。",
            )

        if store and context.conversation_id:
            if store.get_adjustment(context.conversation_id):
                return _failed(
                    "pending_adjustment_exists",
                    "当前已有待确认的试卷调整方案；必须先确认或处理该方案，不能静默覆盖。",
                )
            if store.get(context.conversation_id) is not None:
                return _failed(
                    "pending_replacement_exists",
                    "当前已有待确认换题方案；必须先确认或取消，不能静默覆盖。",
                )

        result = preview_add_question(
            session,
            paper_id=context.version_id or context.paper_id,
            question_type=values.question_type,
            score=values.score,
        )

        if (
            result.ok
            and result.plan
            and store
            and context.conversation_id
        ):
            store.set_adjustment(
                context.conversation_id,
                result.plan.plan_id,
            )

        status = (
            "waiting_confirmation"
            if result.ok
            else "needs_clarification"
            if result.clarification_questions
            else "failed"
        )
        return ExecutedTool(
            payload=result.model_dump(mode="json"),
            status=status,
            result_fields={
                "add_preview": result,
                "warnings": result.warnings,
                "blocking_errors": result.blocking_errors,
                "clarification_questions": result.clarification_questions,
            },
        )

    def preview_adjustment(raw: BaseModel) -> ExecutedTool:
        values = PreviewAdjustmentInput.model_validate(raw)
        if not (context.version_id or context.paper_id):
            return _failed("no_current_paper", "当前还没有可调整的试卷。")
        remove_positions = list(values.remove_positions)

        if values.remove_addresses:
            resolved_positions: list[int] = []
            for address in values.remove_addresses:
                item = resolve_section_item(
                    session,
                    paper_id=context.version_id or context.paper_id,
                    section_type=address.section_type,
                    section_order=address.section_order,
                )
                if item is None:
                    return _failed(
                        "question_address_not_found",
                        (
                            f"当前试卷没有{address.section_type}"
                            f"第{address.section_order}题。"
                        ),
                    )
                resolved_positions.append(item.position)
            remove_positions = resolved_positions

        pending_plan_id = (
            store.get_adjustment(context.conversation_id)
            if store and context.conversation_id else None
        )
        if (
            pending_plan_id
            and not remove_positions
            and not values.remove_addresses
            and values.target_total_score is not None
        ):
            pending_plan = session.get(AdjustmentPlanRecord, pending_plan_id)
            if pending_plan is not None:
                remove_positions = [
                    operation["position"]
                    for operation in pending_plan.operations_json
                    if operation.get("type") == "remove_question"
                ]
        result = preview_adjust_paper(
            session,
            paper_id=context.version_id or context.paper_id,
            knowledge_preferences=values.knowledge_preferences,
            question_type_changes=values.question_type_changes,
            remove_positions=remove_positions,
            target_total_score=values.target_total_score,
        )
        if result.ok and result.plan and store and context.conversation_id:
            store.set_adjustment(context.conversation_id, result.plan.plan_id)
        return ExecutedTool(
            result.model_dump(mode="json"),
            "waiting_confirmation" if result.ok else "needs_clarification" if result.clarification_questions else "failed",
            {"adjustment_preview": result, "warnings": result.warnings, "blocking_errors": result.blocking_errors, "clarification_questions": result.clarification_questions},
        )

    def confirm_adjustment(_raw: BaseModel) -> ExecutedTool:
        plan_id = store.get_adjustment(context.conversation_id) if store and context.conversation_id else None
        if not plan_id:
            return _failed("no_pending_adjustment", "当前没有等待确认的整卷调整方案。")
        if not context.paper_id or not context.version_id:
            return _failed("no_current_paper", "当前还没有可调整的试卷。")
        result = confirm_adjust_paper(
            session,
            plan_id=plan_id,
            paper_id=context.paper_id,
            current_version_id=context.version_id,
        )
        if result.ok:
            store.clear_adjustment(context.conversation_id)
            context.paper_id = result.new_version_id
            context.version_id = result.new_version_id
        return ExecutedTool(
            result.model_dump(mode="json"),
            "completed" if result.ok else "failed",
            {"adjustment": result, "blocking_errors": result.blocking_errors},
        )

    def version_operation(action: Literal["undo", "redo", "restore"], target: int | None = None) -> ExecutedTool:
        if not context.paper_id or not context.version_id:
            return _failed("no_current_paper", "当前还没有可操作的试卷。")
        result = run_version_operation(
            session,
            paper_id=context.paper_id,
            version_id=context.version_id,
            intent=VersionOperationIntent(action=action, target_version=target),
        )
        if result.ok:
            context.paper_id = result.current_version_id
            context.version_id = result.current_version_id
            if store and context.conversation_id:
                store.clear(context.conversation_id)
        return ExecutedTool(
            result.model_dump(mode="json"),
            "completed" if result.ok else "failed",
            {"version_operation": result, "warnings": result.warnings, "blocking_errors": result.blocking_errors},
        )

    tools = [
        AgentTool("read_current_paper", "Read the concrete current paper. For normal teacher-facing numbering, use addresses with section_type + section_order (for example, 填空题第2题). positions is legacy internal global order and must not be inferred from a normal 第N题 reference. Omit both only for a whole-paper overview. Read-only.", ReadCurrentPaperInput, read),
        AgentTool("preview_generation_plan", "Create or patch a validated generation plan without selecting questions. When pending exists, use question_type_patches for only the teacher's changed type/count/score fields; Python preserves the authoritative target total and deterministically rebalances unlocked scores in 0.5-point increments. Never change total_score merely to match stale per-type scores. Empty lists explicitly clear a preference. avoid_previous_paper_questions records an unsupported preference only and must be disclosed as unsupported.", GenerationPlanPatch, preview_generation),
        AgentTool("confirm_generation_plan", "Create the paper from the currently pending validated generation plan. Call only after the teacher explicitly accepts the displayed plan. Never call in the same turn that creates or revises a preview.", EmptyInput, confirm_generation),
        AgentTool("preview_replace_question", "Find a deterministic single-question replacement preview. Use address={section_type, section_order} for teacher-facing numbering such as 填空题第2题; position is legacy internal global order only. This never modifies the paper and always requires later confirmation. Do not call it to handle rejection of an existing pending proposal: cancel that proposal first, and only create another preview when the teacher explicitly asks for another candidate. IMPORTANT: when the teacher wants knowledge points unchanged, preserve_knowledge_points must be true; omitting it permits only the paper's normal scope constraints. When true, the preview-time knowledge IDs are persisted as a hard confirmation contract.", PreviewReplacementInput, preview_replacement),
        AgentTool("confirm_replace_question", "Required state-changing tool when the teacher accepts or confirms the currently pending single-question replacement. Hard constraints captured by the pending preview are revalidated against current database state before mutation. Never claim it was applied without calling this tool.", EmptyInput, confirm_replacement),
        AgentTool("cancel_replace_question", "Required state-changing tool when the teacher rejects, does not want, abandons, or cancels the currently pending single-question replacement. Rejection alone means cancel, not automatically finding another candidate. Never claim it was cancelled without calling this tool.", EmptyInput, cancel_replacement),
        AgentTool(
            "preview_add_question",
            "Preview adding one existing-bank question to the current paper. "
            "Provide canonical question_type and optional explicit score only. "
            "Python selects an approved/active/current, in-scope, non-duplicate "
            "candidate and inserts it at the end of that section. If score is "
            "omitted, Python inherits a uniform score from the existing section; "
            "otherwise it returns clarification. Preview never mutates Paper state "
            "and requires later confirm_adjust_paper.",
            PreviewAddQuestionInput,
            preview_add,
        ),
        AgentTool("analyze_current_paper", "Analyze difficulty, question-type, and knowledge distributions of the current paper. Read-only.", EmptyInput, analyze),
        AgentTool("preview_adjust_paper", "Create a whole-paper AdjustmentPlan preview. It does not modify the paper and requires confirmation. To delete teacher-visible questions, use remove_addresses with section_type + section_order; remove_positions is legacy internal global order only. When deleting and target_total_score is omitted, removed scores disappear naturally and remaining question scores are unchanged. Only when the teacher explicitly requests a final total should target_total_score be set; Python then deterministically rebalances at 0.5-point granularity or returns clarification. Do not encode deletion only as a negative question_type_changes delta.", PreviewAdjustmentInput, preview_adjustment),
        AgentTool("confirm_adjust_paper", "Required state-changing tool to apply the pending whole-paper AdjustmentPlan after explicit teacher confirmation. Never claim it was applied without calling this tool.", EmptyInput, confirm_adjustment),
        AgentTool("undo_paper", "Undo the latest version-chain operation on the current paper.", EmptyInput, lambda raw: version_operation("undo")),
        AgentTool("redo_paper", "Redo the latest undone version-chain operation on the current paper.", EmptyInput, lambda raw: version_operation("redo")),
        AgentTool("restore_paper_version", "Restore the current paper to a specified numbered version.", RestoreVersionInput, lambda raw: version_operation("restore", RestoreVersionInput.model_validate(raw).target_version)),
    ]
    return {tool.name: tool for tool in tools}


def execute_tool(tool: AgentTool, arguments: Any) -> ExecutedTool:
    """Validate every model-provided argument before deterministic execution."""
    try:
        validated = tool.input_model.model_validate(arguments or {})
    except ValidationError as exc:
        return ExecutedTool(
            payload={
                "ok": False,
                "code": "invalid_tool_arguments",
                "message": "工具参数未通过校验。",
                "details": exc.errors(include_url=False),
            },
            status="failed",
            result_fields={"blocking_errors": ["invalid_tool_arguments"]},
        )
    return tool.execute(validated)
