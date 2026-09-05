"""Autonomous tool-calling Teacher Agent with deterministic business tools."""

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.config import Settings
from calculus_agent.models import TeacherAgentRunTrace
from calculus_agent.runtime.backend import BailianChatBackend
from calculus_agent.application.teaching_design_execution import (
    TeachingDesignPaperGenerationResult,
)
from calculus_agent.teaching_design.schemas import TeachingDesignRead

from calculus_agent.agent.pending_teaching_design_intent import (
    PendingTeachingDesignIntent,
    resolve_pending_teaching_design_intent,
)
from calculus_agent.agent.conversation_state import (
    DatabaseConversationHistoryStore,
    DatabasePendingReplacementStore,
    PendingReplacement,
    PendingReplacementStore,
)
from calculus_agent.agent.context_builder import AgentContextBuilder
from calculus_agent.agent.langfuse_tracing import (
    safe_update as _langfuse_update,
    teacher_turn_span,
    tool_observation_span,
)
from calculus_agent.agent.identity import DEFAULT_TEACHER_OWNER_KEY
from calculus_agent.agent.run_tracing import TeacherAgentRunManager
from calculus_agent.agent.skills import load_skill_bundle
from calculus_agent.agent.state_snapshot import (
    active_teaching_design_snapshot,
    build_runtime_state_snapshot,
)
from calculus_agent.agent.tool_adapters.teaching_design import teaching_design_tool_names
from calculus_agent.agent.tool_adapters.teaching_environment import (
    environment_inspection_tool_names,
)
from calculus_agent.agent.task_router import (
    RoutingState,
    TaskType,
    decide_task,
    has_explicit_curriculum_scope,
)
from calculus_agent.agent.trace_log import AgentTraceRecorder, redact_trace_value
from calculus_agent.agent.tool_registry import AgentExecutionContext, build_agent_tools
from calculus_agent.agent.paper_change_service import PaperChangePreview
from calculus_agent.agent.toolkit import Toolkit
from calculus_agent.agent.schemas import GenerationPlanPreview, TeachingPlanningDraft
from calculus_agent.agent.tools.add_tools import AddQuestionPreview
from calculus_agent.agent.tools.analysis_tools import (
    ConfirmAdjustmentResult,
    PaperAdjustmentPreview,
    PaperAnalysisResult,
)
from calculus_agent.agent.tools.paper_tools import GeneratePaperToolResult
from calculus_agent.agent.tools.read_tools import ReadCurrentPaperResult
from calculus_agent.agent.tools.replacement_tools import ApplyReplacementResult, ReplacementDryRunResult
from calculus_agent.agent.tools.version_tools import VersionOperationResult
from calculus_agent.runtime.tool_loop import ToolLoop
from calculus_agent.runtime.contracts import RuntimeErrorInfo, ToolResult
from calculus_agent.runtime.observation_projection import observation_size_metrics
from calculus_agent.runtime.policies import AgentRuntimePolicy
from calculus_agent.runtime.grounding_policy import GroundingPolicy
from calculus_agent.runtime.tool_exposure_policy import ToolExposureContext, ToolExposurePolicy
from calculus_agent.runtime.model_turn import prepare_model_turn
from calculus_agent.runtime.model_turn_executor import execute_model_turn
from calculus_agent.runtime.finalization_policy import (
    FinalizationInput, FinalizationPolicy,
)
from calculus_agent.runtime.runtime import AgentRuntime, UserTurn
from calculus_agent.runtime.tool_execution import (
    ToolExecutor, exposed_tool_names, merge_result_fields, normalize_tool_calls,
    trace_entry,
)
from calculus_agent.runtime.response_policy import ResponsePolicy
from calculus_agent.runtime.request_guards import (
    _apply_explicit_opt_in_guards,
    explicit_generation_constraint_mismatches,
)
from calculus_agent.runtime.variants import AgentVariant, STATE_POLICY
from calculus_agent.runtime.paper_request import (
    _apply_question_reference_hints,
    _explicit_question_addresses,
    _explicit_question_positions,
    _paper_read_messages,
)

# Historical private import retained by the facade.
_merge_result_fields = merge_result_fields

logger = logging.getLogger(__name__)


QUESTION_OPERATION_SKILL = "paper_question_operations"
TEACHING_DESIGN_SKILL = "teaching_design"


class ChatBackend(Protocol):
    def complete(self, messages: list[dict], tools: list[dict]) -> dict: ...


class TeacherAgentResult(BaseModel):
    status: Literal["completed", "needs_clarification", "waiting_confirmation", "failed"]
    message: str
    run_id: str | None = None
    clarification_questions: list[str] = Field(default_factory=list)
    paper: GeneratePaperToolResult | None = None
    warnings: list[str] = Field(default_factory=list)
    blocking_errors: list[str] = Field(default_factory=list)
    pending_action: PendingReplacement | None = None
    version_operation: VersionOperationResult | None = None
    replacement: ApplyReplacementResult | None = None
    replacement_preview: ReplacementDryRunResult | None = None
    analysis: PaperAnalysisResult | None = None
    add_preview: AddQuestionPreview | None = None
    adjustment_preview: PaperAdjustmentPreview | PaperChangePreview | None = None
    adjustment: ConfirmAdjustmentResult | None = None
    paper_read: ReadCurrentPaperResult | None = None
    generation_preview: GenerationPlanPreview | None = None
    teaching_design: TeachingDesignRead | None = None
    teaching_design_candidates: list[TeachingDesignRead] = Field(
        default_factory=list
    )
    teaching_design_generation: TeachingDesignPaperGenerationResult | None = None
    teaching_planning_draft: TeachingPlanningDraft | None = None



_SYSTEM_PROMPT = """你是 Teacher Agent，负责理解教师自然语言并通过系统提供的 Tool 完成真实业务操作。

## 事实与安全
数据库和 Tool Observation 是业务事实来源；Conversation History 与 Working Memory 只用于语言理解、指代和任务连续性。不得绕过 Tool 修改数据库，不得编造 Paper、Question、KnowledgeNode、Version ID、分值、难度、知识点、候选、证据或执行结果。Tool 失败不能说成成功，Preview 不能说成已应用。

## 业务流程
新建试卷调用 `prepare_generation_plan` 生成待确认方案，教师明确确认后才调用 `confirm_generation`；已有 pending generation 时只提交本轮变化，题型变化使用 `question_type_patches`，不要重发完整结构；明确放弃时调用 `discard_pending_plan`。教师用自然语言描述重点知识时，先通过 `inspect_curriculum`（必要时再用题库 `chapter_detail`）读取当前章节的标准知识点，理解语义后将教师表述映射为 Observation 中最贴近的标准名称，再传给 `prepare_generation_plan`；不要把教师原话当作数据库精确名称，也不要在存在合理语义对应项时追问改名。映射成功但题库供给不足时仍保留该知识目标，并依据 Tool Observation 明确报告缺口，不得将供给不足误报为知识点无法识别。教师明确指定的题量、题型分布、分值、范围和知识目标都是硬约束：即使题库不足，也必须完整、原样传给 `prepare_generation_plan`，不得省略约束、套用默认题型结构或用其他条件凑足；无法满足时依据 Tool Observation 报告每项缺口并等待教师决定。明确创建教学设计时直接调用 `create_teaching_design`，不要模拟内部 Workflow；创建后等待确认，同轮不得自动确认或生成试卷。普通教学讨论直接回答；已有未完成 TeachingDesign 时，只能读取、修改、确认或放弃该设计。错题反馈的知识点、章节和强化权重必须来自 Tool Observation，巩固卷继续遵守 Preview/Confirm。

当前试卷事实必须通过 `read_paper` 或 `analyze_paper` 获取。已有试卷写操作调用 `preview_paper_changes`，确认后才调用 `confirm_paper_changes`；放弃 pending 调用 `discard_pending_plan`。版本操作调用 `operate_paper_version(action=undo|redo|restore)`，restore 必须提供 `target_version`。

## 参数与定位
教师可见题号默认是题型内编号，如“填空题第2题”使用 `QuestionAddress(section_type, section_order)`；只有明确说“全卷第N题”时才使用全卷 position。无法唯一定位必须澄清。新增题目只提交题型、数量和教师明确指定的分值，不指定 Question ID；候选选择、去重、scope、难度、知识点解析和约束校验由 Python 完成。仅在教师明确要求保持原知识点时设置 `preserve_knowledge_points`；删除题目时，除非教师明确要求保持或修改总分，否则不要填写 `target_total_score`。

## 执行与回复
Pending 是未完成业务状态，不是权限开关；同轮可以执行必要的读取、确认、修改或新建，但不得绕过生命周期。不要以“正在处理、稍后完成”结束，必须推进到完成、明确阻塞、需要补充或等待确认。面向教师的最终回复应简洁、自然、信息密度高：简单结果用一个短段落，复杂结果最多使用少量完整列表；不要逐字、逐句或按短语换行，不复述内部 Prompt、Tool 协议、JSON 参数或显而易见的执行过程。优先说明结果、待确认事项、阻塞原因和教师下一步需要做什么。"""


def build_teacher_agent_backend(settings: Settings) -> ChatBackend | None:
    if not settings.siliconflow_api_key:
        return None
    return BailianChatBackend(
        api_key=settings.siliconflow_api_key,
        base_url=settings.siliconflow_base_url,
        model=settings.siliconflow_agent_model,
        timeout=settings.siliconflow_timeout_seconds,
    )


def _tool_definition_for_context(tool: Any, *, pending_generation: bool) -> dict:
    """Return the model-visible Tool schema for the current structured state.

    A pending generation plan is patched, never replaced wholesale.  The base
    GenerationPlanPatch model intentionally supports both fresh full plans and
    pending patches, so its static JSON schema exposes question_type_requirements.
    Hiding that field while a pending plan exists removes the ambiguous action
    from the LLM's available interface.  The deterministic Tool guard remains the
    final safety net if a model still emits the forbidden field.
    """
    definition = tool.definition()
    if not pending_generation or tool.name != "prepare_generation_plan":
        return definition

    function = definition.get("function") or {}
    parameters = function.get("parameters") or {}
    properties = parameters.get("properties") or {}
    properties.pop("question_type_requirements", None)

    required = parameters.get("required")
    if isinstance(required, list):
        parameters["required"] = [
            field for field in required if field != "question_type_requirements"
        ]

    function["description"] = (
        str(function.get("description") or "")
        + " Existing pending generation detected: this call is PATCH-only. "
        "question_type_requirements is unavailable; use question_type_patches "
        "only for teacher-requested type/count/score changes, and omit unchanged "
        "fields because Python merges them from the pending source of truth."
    )
    return definition




def _persist_final_message(
    history_store: DatabaseConversationHistoryStore | None,
    conversation_id: str | None,
    message: str,
) -> None:
    if history_store is None or not conversation_id:
        return
    try:
        history_store.append(conversation_id, role="assistant", content=message)
    except Exception:
        pass


def _working_memory_snapshot(
    store: PendingReplacementStore | None,
    conversation_id: str | None,
) -> dict[str, Any] | None:
    if store is None or not conversation_id or not hasattr(store, "get_memory"):
        return None
    try:
        return store.get_memory(conversation_id).model_dump(mode="json")
    except Exception:
        return None


def _build_turn_output_emitter(turn_span: Any) -> Any:
    """Return a closure that writes ``turn_span.update(output=...)`` safely."""
    def emit(result: TeacherAgentResult, error: dict[str, Any] | None) -> None:
        if turn_span is None:
            return
        payload: dict[str, Any] = {
            "status": result.status,
            "message": result.message,
            "final_response": result.message,
        }
        if error:
            payload["error_code"] = error.get("error_code")
            payload["error_type"] = error.get("error_type")
            payload["error_message"] = error.get("error_message")
            payload["error_stage"] = error.get("error_stage")
        try:
            turn_span.update(
                output=payload,
                level="ERROR" if (error is not None or result.status == "failed") else "DEFAULT",
            )
        except Exception:
            pass
    return emit


def _replayed_operation_result(
    row: TeacherAgentRunTrace,
    request_fingerprint: str,
) -> TeacherAgentResult:
    if row.request_fingerprint != request_fingerprint:
        return TeacherAgentResult(
            status="failed",
            message="operation_id 已用于其他请求。",
            run_id=row.run_id,
            blocking_errors=["operation_id_conflict"],
        )
    if row.result_json is None:
        return TeacherAgentResult(
            status="needs_clarification",
            message="该请求正在处理中，请稍后使用相同 operation_id 查询。",
            run_id=row.run_id,
            blocking_errors=["operation_in_progress"],
        )
    try:
        result = TeacherAgentResult.model_validate(row.result_json)
    except Exception:
        return TeacherAgentResult(
            status="failed",
            message="已保存的操作结果无法读取。",
            run_id=row.run_id,
            blocking_errors=["operation_result_invalid"],
        )
    result.run_id = row.run_id
    return result


def _run_teacher_agent_turn(
    session: Session,
    user_message: str,
    *,
    conversation_id: str | None = None,
    owner_key: str = DEFAULT_TEACHER_OWNER_KEY,
    paper_id: str | None = None,
    version_id: str | None = None,
    state_store: PendingReplacementStore | None = None,
    backend: ChatBackend | None = None,
    max_tool_rounds: int = 8,
    trace_recorder: AgentTraceRecorder | None = None,
    variant: AgentVariant = STATE_POLICY,
    tool_fault_injector: Any = None,
    operation_id: str | None = None,
) -> TeacherAgentResult:
    """Run LLM → tool observation → LLM until a final natural-language answer."""
    message = user_message.strip() if isinstance(user_message, str) else ""
    policy = AgentRuntimePolicy(max_tool_rounds=max_tool_rounds)
    teaching_design_artifact_requested = False
    paper_operation_without_target = False
    if operation_id is not None and not (1 <= len(operation_id) <= 36):
        return TeacherAgentResult(
            status="failed",
            message="operation_id 长度必须为 1 到 36 个字符。",
            blocking_errors=["invalid_operation_id"],
        )
    request_fingerprint = hashlib.sha256(json.dumps(
        {
            "conversation_id": conversation_id,
            "owner_key": owner_key,
            "paper_id": paper_id,
            "version_id": version_id,
            "message": message,
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode()).hexdigest()
    if operation_id is not None:
        existing_run = session.scalar(select(TeacherAgentRunTrace).where(
            TeacherAgentRunTrace.run_id == operation_id
        ))
        if existing_run is not None:
            return _replayed_operation_result(existing_run, request_fingerprint)

    # Paper operations require an explicit target from the current request/UI.
    # Persisted workspace state is never promoted into an executable Paper fact.
    trace_recorder = trace_recorder or AgentTraceRecorder()

    trace_recorder.start(
        conversation_id=conversation_id,
        paper_id=paper_id,
        user_input=message,
    )

    run_manager = TeacherAgentRunManager(
        session,
        conversation_id,
        paper_id,
        message,
        run_id=operation_id,
        request_fingerprint=request_fingerprint if operation_id else None,
    ).create()
    if run_manager.conflict:
        existing_run = session.scalar(select(TeacherAgentRunTrace).where(
            TeacherAgentRunTrace.run_id == operation_id
        ))
        if existing_run is not None:
            return _replayed_operation_result(existing_run, request_fingerprint)
        return TeacherAgentResult(
            status="needs_clarification",
            message="该请求正在处理中，请稍后重试。",
            run_id=operation_id,
            blocking_errors=["operation_in_progress"],
        )
    run_id = run_manager.run_id
    agent_span = (
        run_manager.add_span("agent", "teacher_agent_run") if run_manager.row is not None else None
    )
    if agent_span is not None:
        run_manager.mark_running()
    store: PendingReplacementStore | None = None
    context: AgentExecutionContext | None = None
    turn_error: dict[str, Any] | None = None

    with teacher_turn_span(conversation_id, message) as turn_span:
        _emit_turn_output = _build_turn_output_emitter(turn_span)

        def finish(result: TeacherAgentResult, error: dict[str, Any] | None = None) -> TeacherAgentResult:
            trace_recorder.finish(
                agent_status=result.status,
                final_response=result.message,
                memory_after=_working_memory_snapshot(store, conversation_id),
                paper_id=context.paper_id if context is not None else paper_id,
                error=error,
            )
            _emit_turn_output(result, error)
            result.run_id = run_id
            run_manager.finalize(
                status=result.status,
                final_response=result.message,
                state_after=build_runtime_state_snapshot(
                    session,
                    store=store,
                    owner_key=owner_key,
                    conversation_id=conversation_id,
                ),
                paper_id=context.paper_id if context is not None else paper_id,
                error=error,
                result_json=result.model_dump(mode="json"),
            )
            run_manager.update_span(
                agent_span,
                status="error" if error is not None else "success",
                output={"status": result.status, "message": result.message},
            )
            return result

        explicit_question_addresses = _explicit_question_addresses(message)
        explicit_question_positions = _explicit_question_positions(message)
        history_store = (
            DatabaseConversationHistoryStore(session)
            if variant.persistent_state and conversation_id else None
        )
        recent_messages: list[dict[str, str]] = []
        if history_store and conversation_id:
            try:
                recent_messages = history_store.recent_messages(conversation_id)
                history_store.append(conversation_id, role="user", content=message)
            except Exception:
                return finish(TeacherAgentResult(
                    status="failed",
                    message="无法读取会话上下文。",
                    blocking_errors=["conversation_context_error"],
                ))
        if backend is None:
            result = TeacherAgentResult(
                status="failed",
                message="Teacher Agent 模型当前不可用，请检查模型配置后重试。",
                blocking_errors=["agent_model_unavailable"],
            )
            _persist_final_message(history_store, conversation_id, result.message)
            return finish(result)

        store = (
            state_store or (DatabasePendingReplacementStore(session) if conversation_id else None)
        ) if variant.persistent_state else None
        context = AgentExecutionContext(
            session=session,
            conversation_id=conversation_id,
            paper_id=paper_id,
            version_id=version_id,
            state_store=store if isinstance(store, DatabasePendingReplacementStore) else store,
            owner_key=owner_key,
            run_id=run_id,
            user_message=message,
            workflow_trace=trace_recorder.set_task_workflow,
        )
        tools = build_agent_tools(context) if variant.tools_enabled else {}
        toolkit = Toolkit(tools.values())
        tool_executor = ToolExecutor(
            toolkit, session=session, fault_injector=tool_fault_injector,
        )
        working_memory = (
            store.get_memory(conversation_id)
            if store and conversation_id and hasattr(store, "get_memory") else None
        )
        pending = store.get(conversation_id) if store and conversation_id else None
        pending_adjustment = (
            store.get_adjustment(conversation_id)
            if store and conversation_id and hasattr(store, "get_adjustment")
            else None
        )
        pending_generation = (
            store.get_generation(conversation_id)
            if store and conversation_id and hasattr(store, "get_generation")
            else None
        )
        has_current_paper = bool(version_id or paper_id)
        active_teaching_design = active_teaching_design_snapshot(
            session,
            owner_key=owner_key,
            conversation_id=conversation_id,
        )
        legacy_teaching_design_active = bool(
            active_teaching_design
            and active_teaching_design.get("status")
            in {"draft", "awaiting_confirmation"}
        )
        pending_teaching_design_intent: PendingTeachingDesignIntent | None = None
        if (
            active_teaching_design
            and active_teaching_design.get("status") == "awaiting_confirmation"
        ):
            pending_teaching_design_intent = resolve_pending_teaching_design_intent(
                message,
                backend=backend,
            )

        design_definition_names = (
            [
                name
                for name in teaching_design_tool_names(active_teaching_design)
                if name in {
                    "read_active_teaching_design",
                    "revise_teaching_design",
                    "confirm_teaching_design",
                    "discard_teaching_design",
                }
            ]
            if legacy_teaching_design_active
            else []
        )
        environment_definition_names = (
            environment_inspection_tool_names()
        )
        task_decision = decide_task(
            message,
            state=RoutingState(
                pending_generation=bool(pending_generation),
                pending_paper_change=bool(pending_adjustment),
                pending_replacement=bool(pending),
                current_paper=has_current_paper,
                active_teaching_design=legacy_teaching_design_active,
            ),
        )
        teaching_design_artifact_requested = task_decision.route.artifact_required
        paper_operation_without_target = bool(
            not has_current_paper
            and task_decision.route.reason == "explicit paper operation wording"
        )
        continuing_generation = bool(
            working_memory
            and working_memory.active_task.get("type") == "generation"
            and working_memory.active_task.get("status") in {
                "drafting", "awaiting_scope", "awaiting_confirmation",
            }
        )
        if continuing_generation and not (
            pending_adjustment or pending or has_current_paper
        ):
            task_decision.route.task_type = TaskType.DIRECT_ACTION
            task_decision.route.artifact_required = False
            task_decision.route.reason = "continuing conversation generation draft"

        continuing_teaching_planning = bool(
            working_memory
            and working_memory.active_task.get("type") == "teaching_planning"
            and working_memory.active_task.get("status") in {"awaiting_scope", "drafted"}
        )
        if continuing_teaching_planning and not (
            pending_generation or pending_adjustment or pending or has_current_paper
        ):
            task_decision.route.task_type = TaskType.TEACHING_PLANNING
            task_decision.route.reason = "continuing conversation teaching-planning draft"

        has_strong_business_state = bool(
            pending_generation or pending_adjustment or pending or has_current_paper
            or legacy_teaching_design_active
        )
        if (
            not has_strong_business_state
            and task_decision.route.task_type == TaskType.TEACHING_DESIGN
            and teaching_design_artifact_requested
            and has_explicit_curriculum_scope(message)
        ):
            # The Tool owns the fixed evidence-before-create workflow.
            context.use_teaching_design_workflow = True
        exposure_policy = ToolExposurePolicy()
        exposure_context = ToolExposureContext(
            tool_names=frozenset(tools),
            task_type=task_decision.route.task_type,
            message=message,
            has_current_paper=has_current_paper,
            pending_generation=bool(pending_generation),
            pending_paper_change=bool(pending_adjustment),
            pending_replacement=bool(pending),
            legacy_teaching_design_active=legacy_teaching_design_active,
            design_tool_names=tuple(design_definition_names),
            environment_tool_names=tuple(environment_definition_names),
            pending_teaching_design_action=(
                pending_teaching_design_intent.action
                if pending_teaching_design_intent is not None else None
            ),
            teaching_design_artifact_requested=teaching_design_artifact_requested,
            has_explicit_curriculum_scope=has_explicit_curriculum_scope(message),
        )
        definition_names = exposure_policy.initial_tools(exposure_context)
        definitions = toolkit.schemas(
            names=definition_names,
            transform=lambda tool: _tool_definition_for_context(
                tool, pending_generation=bool(pending_generation),
            ),
        )
        trace_recorder.set_memory_before(
            working_memory.model_dump(mode="json") if working_memory else None
        )
        run_manager.set_state_before(
            build_runtime_state_snapshot(
                session,
                store=store,
                owner_key=owner_key,
                conversation_id=conversation_id,
            )
        )
        dynamic_context = {
            "current_paper": {
                "exists": bool(version_id or paper_id),
                "paper_id": paper_id,
                "version_id": version_id,
            },
            "pending": (
                {
                    "type": "legacy_single_question_replacement",
                    "position": pending.target_position,
                }
                if pending
                else {"type": "generation_plan"}
                if pending_generation
                else {"type": "paper_change", "plan_id": pending_adjustment}
                if pending_adjustment
                else None
            ),
            "working_memory": working_memory.model_dump(mode="json") if working_memory else None,
            "active_teaching_design": active_teaching_design,
            "deterministic_hints": {
                "question_addresses": [
                    address.model_dump(mode="json")
                    for address in explicit_question_addresses
                ],
                "global_positions": explicit_question_positions,
            },
            "task_route": task_decision.model_dump(mode="json"),
        }
        context_builder = AgentContextBuilder()

        question_operation_skill_active = bool(
            has_current_paper
            or pending
            or pending_adjustment
        )
        teaching_design_skill_active = legacy_teaching_design_active

        teaching_topic_only = (
            task_decision.route.task_type in {
                TaskType.TEACHING_DESIGN, TaskType.TEACHING_PLANNING,
            }
            and not has_explicit_curriculum_scope(message)
            and not has_strong_business_state
        )
        system_parts = [
            _SYSTEM_PROMPT.strip(),
            (
                "当前任务模式："
                + task_decision.route.task_type.value
                + "。该模式仅用于本轮工作流和 Tool surface 路由，"
                "不是业务事实，不得自行扩展为章节、题量、分值、难度或生成约束。"
            ),
        ]
        if teaching_topic_only:
            system_parts.append(
                "当前请求包含教学主题但未给出明确教材章节范围。必须先调用 "
                "retrieve_curriculum_candidates，再根据 Observation 调用 select_teaching_scope；"
                "选择只能来自 Tool 返回的 selectable_scopes，reasoning 仅作解释。Python 验证成功后，必须使用返回的 "
                "validated_scope_names 调用 inspect_curriculum 和 inspect_question_bank，最后才可创建 TeachingDesign。"
                "不得把候选直接当作 scope，也不得自行生成 curriculum id。若教师只要求初步分析而不要求形成"
                "可确认教学设计，可以改用 prepare_teaching_planning_draft。"
            )
        if task_decision.route.clarification_needed:
            system_parts.append(
                "当前任务分类置信度不足。请先向教师提出以下澄清问题，"
                "不要调用会改变业务状态的 Tool："
                + (task_decision.route.clarification_question or "")
            )

        if pending_teaching_design_intent is not None:
            intent_messages = {
                "confirm": "本轮只允许确认当前教学设计。",
                "revise": "本轮包含新的教学要求，只允许调用 revise_teaching_design，禁止确认当前设计。",
                "query": "本轮是对当前教学设计的询问，只允许读取后回答。",
                "cancel": "教师希望放弃当前教学设计，只允许调用 discard_teaching_design，不要确认或修改它。",
                "ambiguous": "无法确定教师是确认、修改、询问还是放弃当前教学设计，请先用自然语言澄清，不要调用确认或修改 Tool。",
            }
            system_parts.append(intent_messages[pending_teaching_design_intent.action])

        if teaching_design_skill_active:
            system_parts.extend([
                (
                    "当前会话存在历史未完成 TeachingDesign。"
                    "以下 active_skill 仅用于继续读取、修改或确认这个既有设计，"
                    "直到其生命周期结束；不得为新的教学讨论或新建试卷请求创建新的 TeachingDesign。"
                    "不得把聊天记录当作设计业务事实。"
                ),
                load_skill_bundle(
                    TEACHING_DESIGN_SKILL
                ),
            ])

        if question_operation_skill_active:
            system_parts.extend([
                (
                    "以下 active_skill 是当前 Teacher Agent 的题目操作业务契约。"
                    "涉及当前试卷中具体题目的查看、新增、删除、替换、调整、"
                    "确认或取消时，必须遵守该 Skill。"
                ),
                load_skill_bundle(
                    QUESTION_OPERATION_SKILL
                ),
            ])

        messages, serialized_context, _ = context_builder.build(
            message=message,
            recent_messages=recent_messages,
            dynamic_context=dynamic_context,
            system_parts=system_parts,
            tool_definitions=definitions,
        )
        result_values: dict[str, Any] = {
            "warnings": [],
            "blocking_errors": [],
            "clarification_questions": [],
        }
        turn_status: Literal["completed", "needs_clarification", "waiting_confirmation", "failed"] = "completed"
        current_stage = "init"
        trace_calls: list[dict[str, Any]] = []
        teaching_design_pending_at_turn_start = bool(
            active_teaching_design
            and active_teaching_design.get("status") == "awaiting_confirmation"
        )
        pending_state_at_turn_start = bool(
            pending
            or pending_adjustment
            or pending_generation
            or teaching_design_pending_at_turn_start
        )
        pending_state_rechecked = False
        pending_paper_change_rechecked = False
        paper_state_at_turn_start = bool(version_id or paper_id)
        response_policy = ResponsePolicy()
        paper_change_requested = (
            not pending_adjustment and not pending
            and response_policy.paper_change_intent(
                message, paper_state_at_turn_start=paper_state_at_turn_start,
            )
        )
        paper_version_at_turn_start = context.version_id or context.paper_id
        observed_paper_read_versions: set[str] = set()
        paper_change_reprompted = False
        malformed_response_retried = False
        generation_patch_retried = False
        explicit_constraint_retried = False
        post_inspection_intent_rechecked = False
        clarification_boundary_reached = False
        terminal_tool_boundary_reached = False
        repeated_validation_boundary_reached = False
        last_tool_validation_failure: tuple[str, str] | None = None
        trace = run_manager.row
        if trace is None:
            trace = TeacherAgentRunTrace(
                conversation_id=conversation_id,
                paper_id=paper_id,
                user_message=message,
                tool_calls_json=[],
                result_status="running",
            )
            session.add(trace)
            session.flush()

        final_text = ""
        try:
            for _round in range(policy.max_tool_rounds + 1):
                current_stage = "llm_call"
                model_turn = prepare_model_turn(
                    messages=messages,
                    definitions=definitions,
                    serialized_context=serialized_context,
                    recent_messages=recent_messages,
                    context_builder=context_builder,
                    dynamic_context=dynamic_context,
                    teaching_design_skill_active=teaching_design_skill_active,
                    question_operation_skill_active=question_operation_skill_active,
                    teaching_design_skill_name=TEACHING_DESIGN_SKILL,
                    question_operation_skill_name=QUESTION_OPERATION_SKILL,
                    tool_round=_round,
                )
                forced_response = None
                if (
                    _round == 0
                    and variant.confirmation_guard
                    and pending_teaching_design_intent is not None
                    and pending_teaching_design_intent.action == "cancel"
                ):
                    forced_response = {
                        "tool_calls": [{
                            "id": f"cancel_{uuid4().hex}",
                            "type": "function",
                            "function": {
                                "name": "discard_teaching_design",
                                "arguments": "{}",
                            },
                        }],
                    }
                response_message = execute_model_turn(
                    backend=backend,
                    messages=messages,
                    definitions=definitions,
                    preparation=model_turn,
                    run_manager=run_manager,
                    parent_span_id=(
                        agent_span.span_id if agent_span is not None else None
                    ),
                    forced_response=forced_response,
                )
                current_stage = "response_parse"
                tool_calls = response_message.get("tool_calls") or []
                if not tool_calls:
                    content = response_message.get("content")
                    if not isinstance(content, str) or not content.strip():
                        raise ValueError("agent_missing_final_response")
                    if response_policy.contains_leaked_tool_protocol(content):
                        if malformed_response_retried or not variant.recovery_policy:
                            final_text = "模型未能返回有效的工具调用格式，本轮没有执行任何操作。"
                            turn_status = "failed"
                            result_values["blocking_errors"].append("agent_invalid_tool_protocol")
                            break
                        messages.extend([
                            {"role": "assistant", "content": content.strip()},
                            {
                                "role": "user",
                                "content": (
                                    "<protocol_error>上一条响应把内部思考或工具标记写进了正文，"
                                    "它不是有效 Tool Call，也不能作为事实回答。请重新处理教师原始请求："
                                    "需要真实状态时使用原生 tool_calls 字段；否则只返回干净的自然语言。"
                                    "</protocol_error>"
                                ),
                            },
                        ])
                        malformed_response_retried = True
                        continue

                    if variant.confirmation_guard and response_policy.requires_pending_paper_change_recheck(
                        pending_adjustment=bool(pending_adjustment),
                        trace_calls=trace_calls,
                        already_rechecked=pending_paper_change_rechecked,
                    ):
                        messages.extend([
                            {"role": "assistant", "content": content.strip()},
                            {
                                "role": "user",
                                "content": (
                                    "<pending_paper_change_guard>"
                                    "当前存在待确认的 paper-change plan。"
                                    "重新判断教师本轮原始请求："
                                    "如果教师在修改当前方案，必须调用 preview_paper_changes；"
                                    "如果明确确认，调用 confirm_paper_changes；"
                                    "如果明确放弃，调用 discard_pending_plan；"
                                    "如果只是读取或询问事实，可以直接回答。"
                                    "read_paper / analyze_paper Observation 只能证明读取成功，"
                                    "不能证明 pending 已修改、确认或取消。"
                                    "没有对应生命周期 Tool Observation 时，不得声称业务状态已经改变。"
                                    "</pending_paper_change_guard>"
                                ),
                            },
                        ])
                        definitions = toolkit.schemas(
                            names=exposure_policy.boundary_tools(
                                "pending_paper_change", context=exposure_context
                            )
                        )
                        pending_paper_change_rechecked = True
                        continue

                    if (
                        variant.confirmation_guard
                        and pending_state_at_turn_start
                        and not trace_calls
                        and not pending_state_rechecked
                    ):
                        if pending_generation:
                            pending_guard = (
                                "当前存在待确认组卷方案。教师明确接受时调用 confirm_generation；"
                                "教师修改方案时调用 prepare_generation_plan，并且只提交本轮 patch；"
                                "教师明确放弃时调用 discard_pending_plan。"
                                "未经 Tool Observation 不得声称方案已修改、放弃或已经组卷。"
                            )
                        elif pending_adjustment:
                            pending_guard = (
                                "当前存在待确认 paper-change plan。教师明确接受时调用 "
                                "confirm_paper_changes；教师继续修改时调用 preview_paper_changes；"
                                "教师明确放弃时调用 discard_pending_plan；需要当前试卷事实时调用 read_paper。"
                                "Pending 是未完成业务状态，不得只靠自然语言声称已经修改或应用。"
                            )
                        elif pending:
                            pending_guard = (
                                "当前存在部署前遗留的单题换题 pending。模型侧不要再使用旧 Tool 名。"
                                "教师接受时调用统一的 confirm_paper_changes；教师放弃时调用 "
                                "discard_pending_plan。若教师要求换一个新候选，应先 discard_pending_plan，"
                                "再调用 preview_paper_changes 创建新的修改计划。"
                            )
                        else:
                            pending_guard = (
                                "当前存在 awaiting_confirmation 的 TeachingDesign。"
                                "教师明确接受当前设计时必须调用 confirm_teaching_design；"
                                "教师提出教学目标、重点、顺序、讲义或测评策略修改时必须调用 "
                                "revise_teaching_design 创建新版本。"
                                "不得只回复自然语言就声称设计已确认或已修改。"
                            )
                        messages = [
                            {"role": "system", "content": pending_guard},
                            {
                                "role": "user",
                                "content": (
                                    message
                                    + "\n\n<current_workspace_state>"
                                    + serialized_context
                                    + "</current_workspace_state>"
                                ),
                            },
                        ]
                        pending_state_rechecked = True
                        continue
                    if (
                        variant.confirmation_guard
                        and pending_state_at_turn_start
                        and not trace_calls
                        and pending_state_rechecked
                    ):
                        final_text = (
                            "当前待确认方案仍未改变，本轮没有可靠执行确认或取消操作。"
                            "你可以让我重新展示方案，或再次明确确认/取消。"
                        )
                        break

                    # After environment inspection, re-check the semantic intent
                    # before accepting a prose-only final answer. This prevents
                    # direct-generation requests from stopping before the editable
                    # blueprint, while also preventing environment-only questions
                    # from being accidentally turned into generation requests.
                    if variant.recovery_policy and response_policy.requires_post_inspection_recheck(
                        has_current_paper=has_current_paper,
                        pending=bool(pending),
                        pending_adjustment=bool(pending_adjustment),
                        pending_generation=bool(pending_generation),
                        already_rechecked=post_inspection_intent_rechecked,
                        trace_calls=trace_calls,
                        environment_tool_names=environment_definition_names,
                        design_tool_names=design_definition_names,
                        is_teaching_design_tool=exposure_policy.is_teaching_design_tool,
                    ):
                        messages.extend([
                            {"role": "assistant", "content": content.strip()},
                            {
                                "role": "user",
                                "content": (
                                    "<post_inspection_intent_recheck>"
                                    "环境调查已经完成。请重新依据教师本轮原始请求判断下一步，"
                                    "不要因为调用过 inspect_curriculum / inspect_question_bank 就默认教师要组卷。"
                                    "如果教师明确要新建/生成试卷，必须在同一轮调用 prepare_generation_plan，"
                                    "生成可编辑 GenerationPlanPreview；Tool 成功后先用聊天正文给出简短的组卷设计意图，"
                                    "再由蓝图卡片展示可编辑结构，之后只需一次确认生成。"
                                    "如果当前任务模式是 TEACHING_PLANNING，且教师提出明确的教学目标、"
                                    "教学重点、复习安排或测评策略，必须在同一轮调用 create_teaching_design；"
                                    "该 Tool 会校验环境证据，并在创建后等待教师确认，禁止同轮确认或生成试卷。"
                                    "如果教师只是讨论教学方法、讲课重点或复习建议，直接自然语言回答，"
                                    "不要创建 TeachingDesign，也不要创建 PendingGeneration。"
                                    "如果教师只是查询课程或题库事实，直接基于已有 Tool Observation 回答，"
                                    "严禁创建 PendingGeneration。"
                                    "</post_inspection_intent_recheck>"
                                ),
                            },
                        ])
                        definitions = toolkit.schemas(
                            names=exposure_policy.post_inspection_tools(exposure_context),
                            transform=lambda tool: _tool_definition_for_context(
                                tool, pending_generation=False,
                            ),
                        )
                        post_inspection_intent_rechecked = True
                        continue

                    current_paper_version_id = context.version_id or context.paper_id
                    grounding = GroundingPolicy.evaluate(
                        message=message,
                        addresses=explicit_question_addresses,
                        positions=explicit_question_positions,
                        current_version_id=current_paper_version_id,
                        observed_read_versions=observed_paper_read_versions,
                    )
                    if (
                        variant.grounding_guard
                        and paper_state_at_turn_start
                        and grounding.read_required
                        and (
                            not trace_calls
                            or current_paper_version_id != paper_version_at_turn_start
                        )
                    ): 
                        # A model that ignored the deterministic read boundary may
                        # not get a prose-only escape hatch. The boundary lives in
                        # message history (not a retry flag) and is version-scoped,
                        # so a later version can still require a new observation.
                        read_boundary = (
                            f'<paper_read_required version_id="{current_paper_version_id or ""}">'
                        )
                        if any(
                            read_boundary in str(item.get("content") or "")
                            for item in messages
                        ):
                            turn_status = "failed"
                            result_values["blocking_errors"].append(
                                "paper_observation_required"
                            )
                            final_text = "本轮没有取得当前试卷的有效读取结果，因此不能可靠回答这项 Paper 事实。"
                            break
                        # CURRENT_PAPER facts require a successful observation of
                        # the current version. No LLM gate or JSON retry decides it.
                        messages = _paper_read_messages(
                            message=message,
                            requested_positions=explicit_question_positions,
                            requested_addresses=explicit_question_addresses,
                            current_version_id=current_paper_version_id,
                            serialized_context=context_builder.serialize_workspace(
                                {**dynamic_context, "current_paper": {
                                    "exists": bool(current_paper_version_id),
                                    "paper_id": context.paper_id,
                                    "version_id": context.version_id,
                                }}, ensure_ascii=False,
                            ),
                        )
                        definitions = toolkit.schemas(names=exposure_policy.boundary_tools("grounding_read", context=exposure_context))
                        continue
                    paper_change_intent = response_policy.paper_change_intent(
                        message, paper_state_at_turn_start=paper_state_at_turn_start,
                    )
                    has_preview = response_policy.successful_observation(
                        trace_calls, "preview_paper_changes"
                    )
                    has_confirmation = response_policy.successful_observation(
                        trace_calls, "confirm_paper_changes"
                    )
                    if (
                        variant.grounding_guard
                        and paper_change_intent
                        and grounding.requires_current_paper_evidence
                        and not has_preview
                        and not has_confirmation
                    ):
                        if not paper_change_reprompted:
                            paper_change_reprompted = True
                            messages.append({"role": "user", "content": (
                                "<paper_change_boundary>教师请求的是试卷修改。当前试卷已经读取成功，"
                                "但尚未生成修改预览。请立即调用 preview_paper_changes 创建待确认方案；"
                                "禁止直接以 completed 回复。</paper_change_boundary>"
                            )})
                            definitions = toolkit.schemas(names=exposure_policy.boundary_tools("paper_change_preview", context=exposure_context))
                            continue
                        turn_status = "failed"
                        result_values["blocking_errors"].append("paper_change_preview_required")
                        final_text = "当前试卷已读取，但本轮没有生成修改预览，因此不能声明修改已完成。"
                        break
                    if (
                        variant.confirmation_guard
                        and teaching_design_artifact_requested
                        and post_inspection_intent_rechecked
                        and not any(
                            call["tool_name"] == "create_teaching_design"
                            and (call.get("result") or {}).get("ok")
                            for call in trace_calls
                        )
                        and all(
                            any(
                                call["tool_name"] == name
                                and (call.get("result") or {}).get("ok")
                                for call in trace_calls
                            )
                            for name in ("inspect_curriculum", "inspect_question_bank")
                        )
                    ):
                        turn_status = "failed"
                        result_values["blocking_errors"].append(
                            "teaching_design_creation_required"
                        )
                        final_text = (
                            "环境调查已经完成，但本轮没有成功创建可确认的 TeachingDesign。"
                        )
                        break
                    final_text = content.strip()
                    break

                if not policy.can_start_round(_round + 1):
                    raise RuntimeError("agent_tool_round_limit")
                normalized_calls = normalize_tool_calls(tool_calls)
                messages.append({
                    "role": "assistant",
                    "content": response_message.get("content"),
                    "tool_calls": normalized_calls,
                })
                for call in normalized_calls:
                    current_stage = "tool_arguments_parse"
                    name, call_id, arguments = tool_executor.prepare(
                        call,
                        addresses=explicit_question_addresses,
                        positions=explicit_question_positions,
                        message=message,
                        apply_reference_hints=_apply_question_reference_hints,
                        apply_explicit_guards=_apply_explicit_opt_in_guards,
                    )
                    blocked_confirmation = bool(
                        pending_teaching_design_intent is not None
                        and pending_teaching_design_intent.action == "revise"
                        and name == "confirm_teaching_design"
                    )
                    registered_tool = tools.get(name)
                    tool_exposed = name in exposed_tool_names(definitions)
                    tool = (
                        registered_tool
                        if not blocked_confirmation and tool_exposed else None
                    )
                    tool_span = run_manager.add_span(
                        "tool_call", name,
                        parent_span_id=agent_span.span_id if agent_span is not None else None,
                        input={"arguments": redact_trace_value(arguments)},
                    )
                    memory_before_tool = _working_memory_snapshot(store, conversation_id)
                    runtime_state_before_tool = build_runtime_state_snapshot(
                        session,
                        store=store,
                        owner_key=owner_key,
                        conversation_id=conversation_id,
                    )
                    generation_patch_retry_needed = False
                    explicit_constraint_retry_needed = False
                    tool_execution_status: str | None = None
                    observed_version_id = context.version_id or context.paper_id
                    constraint_mismatches = (
                        explicit_generation_constraint_mismatches(arguments, message)
                        if name == "prepare_generation_plan" and not pending_generation
                        else []
                    )
                    if constraint_mismatches:
                        mismatch_result = ToolResult.failure(
                            "explicit_constraint_mismatch",
                            "The tool arguments omitted or changed a teacher-explicit hard constraint.",
                            details={
                                "constraint_mismatches": constraint_mismatches,
                                "retryable": not explicit_constraint_retried,
                            },
                        )
                        execution_payload = mismatch_result.payload
                        tool_execution_status = mismatch_result.status
                        turn_status = mismatch_result.status
                        merge_result_fields(result_values, mismatch_result.result_fields)
                        explicit_constraint_retry_needed = not explicit_constraint_retried
                        run_manager.update_span(
                            tool_span,
                            status="success",
                            output=redact_trace_value(execution_payload),
                            ended_at=datetime.now(UTC),
                        )
                    elif blocked_confirmation:
                        blocked_result = ToolResult.failure(
                            "pending_design_revision_requires_revise",
                            "本轮包含新的教学要求，不能确认当前教学设计；请先修改。",
                            status="needs_clarification",
                        )
                        execution_payload = blocked_result.payload
                        tool_execution_status = blocked_result.status
                        turn_status = "needs_clarification"
                        result_values["clarification_questions"].append(
                            "本轮包含新的教学要求，请先修改教学设计。"
                        )
                        result_values["blocking_errors"].append(
                            "pending_design_revision_requires_revise"
                        )
                    elif tool is None:
                        unavailable_code = None
                        if name == "confirm_generation" and not pending_generation:
                            unavailable_code = "no_pending_generation"
                        elif name == "confirm_paper_changes" and not (
                            pending or pending_adjustment
                        ):
                            unavailable_code = "no_pending_action"
                        elif name == "confirm_teaching_design" and not active_teaching_design:
                            unavailable_code = "no_active_teaching_design"
                        elif name in {
                            "read_paper", "analyze_paper", "preview_paper_changes",
                            "operate_paper_version",
                        } and not (context.paper_id or context.version_id):
                            unavailable_code = "no_current_paper"

                        code = (
                            "unknown_tool" if registered_tool is None
                            else unavailable_code or "tool_not_exposed"
                        )
                        message_text = (
                            f"不存在工具：{name}"
                            if registered_tool is None
                            else f"当前工作流阶段不允许或无法调用工具：{name}"
                        )
                        missing_result = ToolResult.failure(
                            code,
                            message_text,
                            status=(
                                "needs_clarification"
                                if unavailable_code is not None else "failed"
                            ),
                        )
                        if code == "tool_not_exposed":
                            terminal_tool_boundary_reached = True
                            final_text = message_text
                        execution_payload = missing_result.payload
                        tool_execution_status = missing_result.status
                        turn_status = missing_result.status
                        result_values["blocking_errors"].append(code)
                        run_manager.update_span(
                            tool_span, status="success",
                            output=redact_trace_value({
                                **execution_payload,
                                "_observation_metrics": observation_size_metrics(
                                    name, execution_payload
                                ),
                            }),
                            ended_at=datetime.now(UTC),
                        )
                    else:
                        current_stage = "tool_execution"
                        with tool_observation_span(name, arguments) as _lf_tool:
                            try:
                                execution = tool_executor.execute(name, arguments)
                            except Exception as exc:
                                _langfuse_update(_lf_tool, level="ERROR", status_message=str(exc))
                                run_manager.update_span(
                                    tool_span, status="error",
                                    output={"error": str(exc)}, ended_at=datetime.now(UTC),
                                )
                                raise
                            _langfuse_update(
                                _lf_tool,
                                output={
                                    "payload": redact_trace_value(execution.payload),
                                    "status": execution.status,
                                },
                            )
                        execution_payload = execution.payload
                        tool_execution_status = execution.status
                        turn_status = execution.status
                        merge_result_fields(result_values, execution.result_fields)
                        if execution_payload.get("code") == "invalid_tool_arguments":
                            signature = (name, "invalid_tool_arguments")
                            if signature == last_tool_validation_failure:
                                repeated_validation_boundary_reached = True
                                final_text = (
                                    "模型连续两次返回无法校验的工具参数，本轮没有执行该操作。"
                                    "请重新发起请求。"
                                )
                            else:
                                last_tool_validation_failure = signature
                        else:
                            if last_tool_validation_failure is not None:
                                result_values["blocking_errors"] = [
                                    code
                                    for code in result_values["blocking_errors"]
                                    if code != "invalid_tool_arguments"
                                ]
                            last_tool_validation_failure = None
                        run_manager.update_span(
                            tool_span, status="success",
                            output=redact_trace_value({
                                **execution_payload,
                                "_observation_metrics": observation_size_metrics(
                                    name, execution_payload
                                ),
                            }),
                            ended_at=datetime.now(UTC),
                        )
                        if name == "prepare_generation_plan":
                            if execution_payload.get("ok"):
                                # A successful corrected preview resolves any transient
                                # generation_partial_patch_required from an earlier attempt.
                                result_values["blocking_errors"] = []
                                result_values["clarification_questions"] = []
                            elif (
                                pending_generation
                                and variant.recovery_policy
                                and not generation_patch_retried
                                and "generation_partial_patch_required"
                                in (execution_payload.get("blocking_errors") or [])
                            ):
                                generation_patch_retry_needed = True
                        if (
                            name == "select_teaching_scope"
                            and execution_payload.get("ok")
                        ):
                            definitions = toolkit.schemas(
                                names=exposure_policy.teaching_scope_tools(
                                    exposure_context
                                )
                            )
                        if name == "read_paper" and execution_payload.get("ok"):
                            observed_paper_read_versions.add(observed_version_id)
                        if (
                            name in {
                                "create_teaching_design",
                                "revise_teaching_design",
                            }
                            and execution_payload.get("ok")
                        ):
                            # Hard runtime confirmation boundary: after proposing
                            # a new design version, the same teacher turn cannot
                            # auto-confirm it even if the model tries to continue.
                            if variant.confirmation_guard:
                                definitions = []
                                terminal_tool_boundary_reached = True
                                turn_status = "waiting_confirmation"
                                final_text = (
                                    "教学设计已创建并保存，当前等待教师确认。"
                                )
                    memory_after_tool = _working_memory_snapshot(store, conversation_id)
                    runtime_state_after_tool = build_runtime_state_snapshot(
                        session,
                        store=store,
                        owner_key=owner_key,
                        conversation_id=conversation_id,
                    )
                    run_manager.add_span(
                        "state_transition", f"{name}_state_change",
                        parent_span_id=tool_span.span_id if tool_span is not None else None,
                        status="success",
                        input={"before": redact_trace_value(runtime_state_before_tool)},
                        output={"after": redact_trace_value(runtime_state_after_tool)},
                        ended_at=datetime.now(UTC),
                    )
                    trace_call = trace_entry(
                        call_id=call_id,
                        name=name,
                        arguments=arguments,
                        payload=execution_payload,
                        observed_version_id=observed_version_id,
                    )
                    trace_calls.append(trace_call)
                    trace_recorder.record_tool_call(
                        tool_name=name,
                        arguments=arguments,
                        memory_before=memory_before_tool,
                        result=execution_payload,
                        memory_after=memory_after_tool,
                    )
                    ToolLoop.append_observation(
                        messages,
                        call_id=call_id,
                        name=name,
                        payload=execution_payload,
                    )
                    if (
                        name == "read_paper"
                        and execution_payload.get("ok")
                        and variant.grounding_guard
                        and paper_change_requested
                        and not paper_change_reprompted
                    ):
                        # A read only establishes Paper facts. For a requested
                        # mutation, force the next model decision onto the
                        # preview boundary rather than letting a multi-call
                        # response keep reading/analyzing and end as completed.
                        paper_change_reprompted = True
                        messages.append({
                            "role": "user",
                            "content": (
                                "<paper_change_boundary>当前试卷已经读取成功。"
                                "教师请求修改试卷，下一步必须调用 "
                                "preview_paper_changes 创建待确认方案；"
                                "不要再次读取/分析试卷，也不要直接回复完成。</paper_change_boundary>"
                            ),
                        })
                        definitions = toolkit.schemas(names=exposure_policy.boundary_tools("paper_change_preview", context=exposure_context))
                        break
                    if (
                        name == "prepare_teaching_planning_draft"
                        and tool_execution_status == "completed"
                        and execution_payload.get("ok")
                    ):
                        # This tool is the terminal boundary of the planning-only
                        # workflow. Its structured result is already authoritative;
                        # asking the model for another turn can only reproduce the
                        # same tool call and exhaust the round limit.
                        terminal_tool_boundary_reached = True
                        if execution_payload.get("waiting_for_scope"):
                            turn_status = "needs_clarification"
                            final_text = "已形成教学规划草稿，请继续补充教材章节范围。"
                        else:
                            final_text = "已形成教学规划草稿，并已保留当前确认的教材范围。"

                    if explicit_constraint_retry_needed:
                        messages.append({
                            "role": "user",
                            "content": (
                                "<explicit_constraint_guard>上一条 prepare_generation_plan "
                                "遗漏或修改了教师明确给出的硬约束。请读取 Tool Observation 中的 "
                                "constraint_mismatches，保持其他已理解参数不变，并立即重新调用 "
                                "prepare_generation_plan。不得用默认模板或其他题型替代。"
                                "</explicit_constraint_guard>"
                            ),
                        })
                        definitions = toolkit.schemas(
                            names=exposure_policy.boundary_tools(
                                "generation_patch",
                                context=exposure_context,
                            ),
                        )
                        explicit_constraint_retried = True
                        break
                    if (
                        tool_execution_status == "needs_clarification"
                        and not generation_patch_retry_needed
                        and not explicit_constraint_retry_needed
                    ):
                        clarification_boundary_reached = True
                        turn_status = "needs_clarification"
                        final_text = str(
                            execution_payload.get("message")
                            or (execution_payload.get("clarification_questions") or [""])[0]
                            or "请补充必要信息后继续。"
                        )
                        break
                    if generation_patch_retry_needed:
                        # Recover once inside the same Agent turn instead of asking the
                        # teacher to repeat a request that Python can already represent.
                        messages.append({
                            "role": "user",
                            "content": (
                                "<generation_patch_guard>当前已有 pending generation。"
                                "上一条 prepare_generation_plan 错把完整 "
                                "question_type_requirements 当成 patch，因此被 Python 拒绝。"
                                "请立即重新调用 prepare_generation_plan：只提交教师本轮相对当前 "
                                "pending 真正改变的字段；禁止 question_type_requirements；"
                                "题型数量或分值变化使用 question_type_patches。"
                                "如果本轮只修改章节/知识点，就只传 scope_names / "
                                "knowledge_preferences 等对应字段。不要要求教师取消旧方案，也不要 "
                                "重复追问已经明确的信息。</generation_patch_guard>"
                            ),
                        })
                        definitions = toolkit.schemas(
                            names=exposure_policy.boundary_tools("generation_patch", context=exposure_context),
                            transform=lambda tool: _tool_definition_for_context(
                                tool,
                                pending_generation=True,
                            ),
                        )
                        generation_patch_retried = True
                    if policy.should_stop_after_tool(
                        clarification_boundary=clarification_boundary_reached,
                        terminal_boundary=terminal_tool_boundary_reached,
                        repeated_validation_boundary=repeated_validation_boundary_reached,
                    ):
                        # Stop processing any additional model-provided calls in
                        # this response as well as stopping the next LLM round.
                        break
                if policy.should_stop_after_tool(
                    clarification_boundary=clarification_boundary_reached,
                    terminal_boundary=terminal_tool_boundary_reached,
                    repeated_validation_boundary=repeated_validation_boundary_reached,
                ): 
                    break
            else:
                raise RuntimeError("agent_tool_round_limit")
        except Exception as exc:
            turn_status = "failed"
            error_info = RuntimeErrorInfo.from_exception(exc, stage=current_stage)
            if error_info.error_code not in result_values["blocking_errors"]:
                result_values["blocking_errors"].append(error_info.error_code)
            turn_error = error_info.as_dict()
            logger.exception(
                "Teacher Agent turn failed at stage=%s code=%s",
                current_stage,
                error_info.error_code,
            )
            final_text = "Teacher Agent 暂时无法完成这次请求，请稍后重试。"

        pending_query_possible = bool(store and conversation_id)
        pending_action_in_store = False
        active_design_after_turn = None
        active_task_status_after_turn = None
        if pending_query_possible:
            active_design_after_turn = active_teaching_design_snapshot(
                session, owner_key=owner_key, conversation_id=conversation_id,
            )
            active_task_status_after_turn = (
                store.get_memory(conversation_id).active_task.get("status")
                if hasattr(store, "get_memory") else None
            )
            pending_action_in_store = bool(
                store.get(conversation_id)
                or (store.get_adjustment(conversation_id) if hasattr(store, "get_adjustment") else None)
                or (store.get_generation(conversation_id) if hasattr(store, "get_generation") else None)
                or (
                    active_design_after_turn
                    and active_design_after_turn.get("status")
                    in {"draft", "awaiting_confirmation"}
                )
            )
        finalization_input = FinalizationInput(
            status=turn_status,
            final_text=final_text,
            result_values=result_values,
            turn_error=turn_error,
            current_stage=current_stage,
            trace_calls=trace_calls,
            pending_query_possible=pending_query_possible,
            pending_action_in_store=pending_action_in_store,
            teaching_design_artifact_requested=teaching_design_artifact_requested,
            active_design=active_design_after_turn,
            active_task_status=active_task_status_after_turn,
            paper_operation_without_target=paper_operation_without_target,
        )
        if variant.confirmation_guard:
            finalization = FinalizationPolicy(policy).finalize(finalization_input)
            turn_status = finalization.status
            final_text = finalization.final_text

        trace.tool_calls_json = trace_calls
        trace.final_response = final_text
        trace.paper_id = context.paper_id
        trace.result_status = turn_status
        if turn_error:
            trace.error_code = turn_error.get("error_code")
            trace.error_type = turn_error.get("error_type")
            trace.error_message = turn_error.get("error_message")
            trace.error_stage = turn_error.get("error_stage")
        session.flush()
        _persist_final_message(history_store, conversation_id, final_text)
        return finish(TeacherAgentResult(
            status=turn_status,
            message=final_text,
            **result_values,
        ), error=turn_error)


def run_teacher_agent(
    session: Session,
    user_message: str,
    *,
    conversation_id: str | None = None,
    owner_key: str = DEFAULT_TEACHER_OWNER_KEY,
    paper_id: str | None = None,
    version_id: str | None = None,
    state_store: PendingReplacementStore | None = None,
    backend: ChatBackend | None = None,
    max_tool_rounds: int = 8,
    trace_recorder: AgentTraceRecorder | None = None,
    variant: AgentVariant = STATE_POLICY,
    tool_fault_injector: Any = None,
    operation_id: str | None = None,
) -> TeacherAgentResult:
    """Compatibility function backed by the explicit ``AgentRuntime`` API."""
    runtime = AgentRuntime(
        session,
        coordinator=_run_teacher_agent_turn,
        backend=backend,
        state_store=state_store,
        max_tool_rounds=max_tool_rounds,
        trace_recorder=trace_recorder,
        default_owner_key=DEFAULT_TEACHER_OWNER_KEY,
        variant=variant,
        tool_fault_injector=tool_fault_injector,
    )
    return runtime.run(UserTurn(
        message=user_message,
        conversation_id=conversation_id,
        owner_key=owner_key,
        paper_id=paper_id,
        version_id=version_id,
        operation_id=operation_id,
    ))
