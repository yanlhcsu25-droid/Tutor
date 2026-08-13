"""Tool registry and deterministic executors for the autonomous Teacher Agent."""

from dataclasses import dataclass, field
from math import isclose
from typing import Any, Callable, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.models import AdjustmentPlanRecord, PaperItem, QuestionKnowledgeLink
from calculus_agent.question_types import canonical_question_type

from .conversation_state import DatabasePendingReplacementStore, PendingGeneration, PendingReplacement
from .schemas import AgentWorkingMemory, GenerationPlanPatch, GenerationPlanPreview, GeneratePaperInput, QuestionTypeRequirement, ReplacementIntent
from .tools.analysis_tools import (
    KnowledgePreference,
    analyze_paper,
    confirm_adjust_paper,
    preview_adjust_paper,
)
from .tools.paper_tools import build_structured_generation_request, generate_paper_from_input
from .tools.read_tools import ReadCurrentPaperInput, read_current_paper
from .tools.replacement_tools import apply_question_replacement, dry_run_replace_question
from .tools.version_tools import run_version_operation
from .version_parser import VersionOperationIntent


class EmptyInput(BaseModel):
    pass


class PreviewReplacementInput(BaseModel):
    position: int = Field(ge=1, le=100)
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


class RestoreVersionInput(BaseModel):
    target_version: int = Field(ge=1)


class PreviewAdjustmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_preferences: list[KnowledgePreference] = Field(default_factory=list)
    question_type_changes: dict[str, int] = Field(default_factory=dict)
    remove_positions: list[int] = Field(default_factory=list, description="Exact 1-based positions to remove from the current paper.")
    target_total_score: float | None = Field(default=None, gt=0, validation_alias=AliasChoices("target_total_score", "total_score"), description="Desired final paper score after the adjustment. Omit to preserve the current total score. The compatibility alias total_score is also accepted.")


@dataclass
class AgentExecutionContext:
    session: Session
    conversation_id: str | None
    paper_id: str | None
    version_id: str | None
    state_store: DatabasePendingReplacementStore | None


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


def _normalized_generation_request(request: GeneratePaperInput, generation_request) -> GeneratePaperInput:
    blueprint = generation_request.blueprint
    return request.model_copy(update={
        "question_count": blueprint.total_questions,
        "total_score": int(blueprint.total_score),
        "question_type_requirements": [
            QuestionTypeRequirement(
                question_type=section.question_type,
                count=section.count,
                score_each=section.score_per_question,
                total_score=section.total_score,
            )
            for section in blueprint.sections
        ] if blueprint.sections else [
            QuestionTypeRequirement(question_type=name, count=count)
            for name, count in blueprint.question_type_counts.items()
        ],
    })


def _merge_question_type_patch(base: GeneratePaperInput, patch_values: dict) -> tuple[GeneratePaperInput, set[str], set[str]]:
    """Return merged request, count-changed types, and explicitly score-changed types."""
    current = {item.question_type: item.model_copy() for item in (base.question_type_requirements or [])}
    incoming = patch_values.pop("question_type_patches", None)
    explicit_partial_patch = incoming is not None
    if incoming is None and "question_type_requirements" in patch_values:
        incoming = patch_values.pop("question_type_requirements") or []
    changed_counts: set[str] = set()
    changed_scores: set[str] = set()
    for raw in incoming or []:
        values = raw.model_dump(exclude_unset=True) if isinstance(raw, BaseModel) else dict(raw)
        name = canonical_question_type(values["question_type"])
        values["question_type"] = name
        previous = current.get(name)
        if previous is None:
            current[name] = QuestionTypeRequirement.model_validate(values)
            changed_counts.add(name)
            if values.get("score_each") is not None:
                changed_scores.add(name)
            continue
        updates = {}
        if "count" in values and values["count"] != previous.count:
            updates["count"] = values["count"]
            changed_counts.add(name)
        if "score_each" in values and values["score_each"] is not None:
            if explicit_partial_patch or not isclose(values["score_each"], previous.score_each or 0):
                changed_scores.add(name)
            if not isclose(values["score_each"], previous.score_each or 0):
                updates["score_each"] = values["score_each"]
        if updates:
            updates["total_score"] = updates.get("count", previous.count) * updates.get("score_each", previous.score_each)
            current[name] = previous.model_copy(update=updates)
    merged = base.model_copy(update={**patch_values, "question_type_requirements": list(current.values())})
    return merged, changed_counts, changed_scores


def _rebalance_scores(request: GeneratePaperInput, *, locked_types: set[str], changed_count_types: set[str]) -> tuple[GeneratePaperInput | None, str | None]:
    requirements = [item.model_copy() for item in (request.question_type_requirements or [])]
    if not requirements or request.total_score is None or any(item.score_each is None for item in requirements):
        return request, None
    current_total = sum(item.count * item.score_each for item in requirements)
    difference = request.total_score - current_total
    if isclose(difference, 0):
        normalized = [item.model_copy(update={"total_score": item.count * item.score_each}) for item in requirements]
        return request.model_copy(update={
            "question_type_requirements": normalized,
            "question_count": sum(item.count for item in normalized),
        }), None
    preferred = ["解答题", "计算题", "证明题", "填空题", "选择题"]
    candidates = sorted(
        (item for item in requirements if item.question_type not in locked_types and item.question_type not in changed_count_types),
        key=lambda item: preferred.index(item.question_type) if item.question_type in preferred else len(preferred),
    )
    for item in candidates:
        new_score = item.score_each + difference / item.count
        if new_score > 0 and isclose(new_score * 2, round(new_score * 2)):
            balanced = [
                entry.model_copy(update={
                    "score_each": new_score,
                    "total_score": entry.count * new_score,
                }) if entry.question_type == item.question_type else entry.model_copy(update={"total_score": entry.count * entry.score_each})
                for entry in requirements
            ]
            return request.model_copy(update={"question_type_requirements": balanced, "question_count": sum(entry.count for entry in balanced)}), None
    return None, "当前题型数量无法按0.5分粒度自动平衡到目标总分，请明确希望调整哪一类题目的每题分值。"


def build_agent_tools(context: AgentExecutionContext) -> dict[str, AgentTool]:
    session = context.session
    store = context.state_store

    def preview_generation(raw: BaseModel) -> ExecutedTool:
        patch = GenerationPlanPatch.model_validate(raw)
        patch_values = patch.model_dump(exclude_unset=True)
        unsupported = patch_values.pop("avoid_previous_paper_questions", None)
        pending = store.get_generation(context.conversation_id) if store and context.conversation_id else None
        memory = store.get_memory(context.conversation_id) if store and context.conversation_id and hasattr(store, "get_memory") else AgentWorkingMemory()
        base = pending.request.model_dump(mode="json") if pending else (
            memory.generation_summary if memory.generation_summary else
            {key: value for key, value in (memory.last_completed_paper or {}).items() if key in GeneratePaperInput.model_fields}
            if patch_values.get("paper_type") is None and memory.last_completed_paper else {}
        )
        if pending:
            if "question_type_requirements" in patch.model_fields_set and "question_type_patches" not in patch.model_fields_set:
                preview = GenerationPlanPreview(
                    ok=False,
                    request=pending.request,
                    total_questions=pending.request.question_count,
                    total_score=pending.request.total_score,
                    sections=pending.request.question_type_requirements or [],
                    blocking_errors=["generation_partial_patch_required"],
                    clarification_questions=[
                        "当前已有待确认方案。请只提交教师本轮明确修改的题型字段，并使用 question_type_patches；未提到的题型必须保持不变。"
                    ],
                )
                return ExecutedTool(
                    payload=preview.model_dump(mode="json"),
                    status="needs_clarification",
                    result_fields={
                        "generation_preview": preview,
                        "blocking_errors": preview.blocking_errors,
                        "clarification_questions": preview.clarification_questions,
                    },
                )
            request, changed_count_types, changed_score_types = _merge_question_type_patch(
                pending.request, patch_values
            )
            locked_types = set(pending.locked_score_question_types) | changed_score_types
            balanced, balance_question = _rebalance_scores(
                request,
                locked_types=locked_types,
                changed_count_types=changed_count_types,
            )
            if balanced is None:
                preview = GenerationPlanPreview(
                    ok=False,
                    request=request,
                    total_score=request.total_score,
                    sections=request.question_type_requirements or [],
                    blocking_errors=["score_rebalance_ambiguous"],
                    clarification_questions=[balance_question],
                )
                if store and context.conversation_id and hasattr(store, "get_memory"):
                    memory.last_clarification = {
                        "missing_fields": ["score_rebalance_ambiguous"],
                        "questions": [balance_question],
                    }
                    store.set_memory(context.conversation_id, memory)
                return ExecutedTool(
                    payload=preview.model_dump(mode="json"),
                    status="needs_clarification",
                    result_fields={
                        "generation_preview": preview,
                        "blocking_errors": preview.blocking_errors,
                        "clarification_questions": preview.clarification_questions,
                    },
                )
            request = balanced
        else:
            patch_values.pop("question_type_patches", None)
            request = GeneratePaperInput.model_validate({**base, **patch_values})
            changed_score_types = {
                item.question_type for item in (patch.question_type_requirements or [])
                if item.score_each is not None
            }
        generation_request, warnings, errors, questions = build_structured_generation_request(
            session, request
        )
        if generation_request is not None:
            request = _normalized_generation_request(request, generation_request)
        preview = GenerationPlanPreview(
            ok=generation_request is not None,
            request=request,
            title=generation_request.blueprint.title if generation_request else None,
            total_questions=generation_request.blueprint.total_questions if generation_request else None,
            total_score=generation_request.blueprint.total_score if generation_request else None,
            sections=[
                QuestionTypeRequirement(
                    question_type=section.question_type,
                    count=section.count,
                    score_each=section.score_per_question,
                    total_score=section.total_score,
                )
                for section in (generation_request.blueprint.sections if generation_request else [])
            ] if generation_request and generation_request.blueprint.sections else [
                QuestionTypeRequirement(question_type=question_type, count=count)
                for question_type, count in (
                    generation_request.blueprint.question_type_counts.items()
                    if generation_request else []
                )
            ],
            warnings=warnings,
            blocking_errors=errors,
            clarification_questions=questions,
        )
        if preview.ok and store and context.conversation_id:
            store.set_generation(context.conversation_id, PendingGeneration(
                request=request,
                total_score_source=(
                    "teacher_explicit" if "total_score" in patch.model_fields_set
                    else pending.total_score_source if pending else "default_template"
                ),
                locked_score_question_types=sorted(
                    (set(pending.locked_score_question_types) if pending else set()) | changed_score_types
                ),
            ))
        if store and context.conversation_id and hasattr(store, "get_memory"):
            memory = store.get_memory(context.conversation_id)
            memory.active_task = {"type": "generation", "status": "awaiting_confirmation" if preview.ok else "drafting"}
            memory.generation_summary = request.model_dump(mode="json")
            memory.last_clarification = ({"missing_fields": errors, "questions": questions} if questions else None)
            if unsupported:
                memory.unsupported_preferences = [{
                    "type": "avoid_previous_paper_questions", "source": "teacher_stated",
                    "status": "unsupported",
                    "reference_paper_id": (memory.last_completed_paper or {}).get("paper_id"),
                }]
                warnings = [*warnings, "avoid_previous_paper_questions_unsupported"]
                preview.warnings = warnings
            store.set_memory(context.conversation_id, memory)
        return ExecutedTool(
            payload=preview.model_dump(mode="json"),
            status="waiting_confirmation" if preview.ok else "needs_clarification",
            result_fields={
                "generation_preview": preview,
                "warnings": warnings,
                "blocking_errors": errors,
                "clarification_questions": questions,
            },
        )

    def confirm_generation(_raw: BaseModel) -> ExecutedTool:
        pending = store.get_generation(context.conversation_id) if store and context.conversation_id else None
        if pending is None:
            return _failed("no_pending_generation", "当前没有等待确认的组卷方案。")
        result = generate_paper_from_input(session, pending.request)
        status = "completed" if result.ok else "needs_clarification" if result.needs_clarification else "failed"
        if result.ok:
            context.paper_id = str(result.paper_id)
            context.version_id = str(result.version_id)
            store.clear_generation(context.conversation_id)
            if hasattr(store, "get_memory"):
                memory = store.get_memory(context.conversation_id)
                memory.active_task = {"type": "generation", "status": "completed"}
                memory.last_completed_paper = {
                    "paper_id": str(result.paper_id), "version_id": str(result.version_id),
                    **pending.request.model_dump(mode="json"),
                }
                memory.generation_summary = {}
                memory.last_clarification = None
                store.set_memory(context.conversation_id, memory)
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

    def preview_replacement(raw: BaseModel) -> ExecutedTool:
        values = PreviewReplacementInput.model_validate(raw)
        if not context.paper_id or not context.version_id:
            return _failed("no_current_paper", "当前还没有可修改的试卷。")
        if store and context.conversation_id and store.get(context.conversation_id) is not None:
            return _failed(
                "pending_replacement_exists",
                "当前已有待确认换题方案；必须先确认或取消，不能用新预览静默覆盖。",
            )
        target_knowledge: list[str] = []
        if values.preserve_knowledge_points:
            item = session.scalar(select(PaperItem).where(
                PaperItem.paper_id == context.version_id,
                PaperItem.position == values.position,
            ))
            if item is not None:
                target_knowledge = list(session.scalars(
                    select(QuestionKnowledgeLink.knowledge_node_id).where(
                        QuestionKnowledgeLink.question_id == item.question_id
                    )
                ))
        intent = ReplacementIntent(
            target_position=values.position,
            difficulty_direction=values.difficulty_direction or "same",
            target_difficulty=values.target_difficulty,
            target_knowledge_node_ids=target_knowledge,
            avoid_similarity_with_question_numbers=values.avoid_similarity_with_question_numbers,
        )
        result = dry_run_replace_question(
            session,
            paper_id=context.paper_id,
            version_id=context.version_id,
            intent=intent,
        )
        fields = {
            "replacement_preview": result,
            "warnings": result.warnings,
            "blocking_errors": result.blocking_errors,
        }
        if not result.ok:
            return ExecutedTool(result.model_dump(mode="json"), "failed", fields)
        if store is None or not context.conversation_id:
            return _failed("missing_conversation_context", "无法保存待确认的换题方案。")
        pending = PendingReplacement(
            paper_id=context.paper_id,
            source_version_id=context.version_id,
            target_position=values.position,
            old_question_id=result.current_question.question_id,
            replacement_question_id=result.recommended_question.question_id,
            difficulty_direction=intent.difficulty_direction,
            target_difficulty=intent.target_difficulty,
            warnings=result.warnings,
        )
        store.set(context.conversation_id, pending)
        fields["pending_action"] = pending
        return ExecutedTool(
            {**result.model_dump(mode="json"), "confirmation_required": True},
            "waiting_confirmation",
            fields,
        )

    def confirm_replacement(_raw: BaseModel) -> ExecutedTool:
        pending = store.get(context.conversation_id) if store and context.conversation_id else None
        if pending is None:
            return _failed("no_pending_action", "当前没有等待确认的单题替换方案。")
        result = apply_question_replacement(
            session,
            paper_id=pending.paper_id,
            source_version_id=pending.source_version_id,
            target_position=pending.target_position,
            replacement_question_id=pending.replacement_question_id,
            difficulty_direction=pending.difficulty_direction,
            target_difficulty=pending.target_difficulty,
        )
        if result.ok:
            store.clear(context.conversation_id)
            context.paper_id = result.new_version_id
            context.version_id = result.new_version_id
        return ExecutedTool(
            result.model_dump(mode="json"),
            "completed" if result.ok else "failed",
            {"replacement": result, "warnings": result.warnings, "blocking_errors": result.blocking_errors},
        )

    def cancel_replacement(_raw: BaseModel) -> ExecutedTool:
        pending = store.get(context.conversation_id) if store and context.conversation_id else None
        if pending is None:
            return _failed("no_pending_action", "当前没有等待取消的单题替换方案。")
        store.clear(context.conversation_id)
        return ExecutedTool({"ok": True, "cancelled": True}, "completed")

    def preview_adjustment(raw: BaseModel) -> ExecutedTool:
        values = PreviewAdjustmentInput.model_validate(raw)
        if not (context.version_id or context.paper_id):
            return _failed("no_current_paper", "当前还没有可调整的试卷。")
        remove_positions = values.remove_positions
        pending_plan_id = (
            store.get_adjustment(context.conversation_id)
            if store and context.conversation_id else None
        )
        if pending_plan_id and not remove_positions and values.target_total_score is not None:
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
        AgentTool("read_current_paper", "Read the concrete current paper or selected positions. For a request about named question numbers, positions is required and must contain exactly those numbers (for example, 第五题是什么 means positions=[5]); omit positions only for a whole-paper overview. Use before answering factual questions about existing questions. Read-only. If the teacher also requested an operation such as replacement, continue to the appropriate operation tool after reading; do not promise background work.", ReadCurrentPaperInput, read),
        AgentTool("preview_generation_plan", "Create or patch a validated generation plan without selecting questions. When pending exists, use question_type_patches for only the teacher's changed type/count/score fields; Python preserves the authoritative target total and deterministically rebalances unlocked scores in 0.5-point increments. Never change total_score merely to match stale per-type scores. Empty lists explicitly clear a preference. avoid_previous_paper_questions records an unsupported preference only and must be disclosed as unsupported.", GenerationPlanPatch, preview_generation),
        AgentTool("confirm_generation_plan", "Create the paper from the currently pending validated generation plan. Call only after the teacher explicitly accepts the displayed plan. Never call in the same turn that creates or revises a preview.", EmptyInput, confirm_generation),
        AgentTool("preview_replace_question", "Find a deterministic single-question replacement preview. This never modifies the paper and always requires later confirmation. Do not call it to handle rejection of an existing pending proposal: cancel that proposal first, and only create another preview when the teacher explicitly asks for another candidate. IMPORTANT: when the teacher wants knowledge points unchanged, preserve_knowledge_points must be true; omitting it permits only the paper's normal scope constraints.", PreviewReplacementInput, preview_replacement),
        AgentTool("confirm_replace_question", "Required state-changing tool when the teacher accepts or confirms the currently pending single-question replacement. Never claim it was applied without calling this tool.", EmptyInput, confirm_replacement),
        AgentTool("cancel_replace_question", "Required state-changing tool when the teacher rejects, does not want, abandons, or cancels the currently pending single-question replacement. Rejection alone means cancel, not automatically finding another candidate. Never claim it was cancelled without calling this tool.", EmptyInput, cancel_replacement),
        AgentTool("analyze_current_paper", "Analyze difficulty, question-type, and knowledge distributions of the current paper. Read-only.", EmptyInput, analyze),
        AgentTool("preview_adjust_paper", "Create a whole-paper AdjustmentPlan preview. It does not modify the paper and requires confirmation. To delete concrete questions, first read the paper if needed, then pass exact remove_positions. target_total_score is the desired final total; when omitted, Python preserves the current total. After removals, Python deterministically rebalances one remaining question type at 0.5-point granularity or returns clarification. Do not encode deletion only as a negative question_type_changes delta.", PreviewAdjustmentInput, preview_adjustment),
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
