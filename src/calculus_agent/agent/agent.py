"""Autonomous tool-calling Teacher Agent with deterministic business tools."""

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from calculus_agent.config import Settings
from calculus_agent.models import TeacherAgentRunTrace
from calculus_agent.orchestration.backend import BailianChatBackend

from .conversation_state import (
    DatabaseConversationHistoryStore,
    DatabasePendingReplacementStore,
    PendingReplacement,
    PendingReplacementStore,
)
from .langfuse_tracing import (
    llm_generation_span,
    safe_update as _langfuse_update,
    teacher_turn_span,
    tool_observation_span,
)
from .run_tracing import TeacherAgentRunManager
from .trace_log import AgentTraceRecorder, redact_trace_value
from .tool_registry import AgentExecutionContext, build_agent_tools, execute_tool
from .schemas import GenerationPlanPreview
from .tools.analysis_tools import (
    ConfirmAdjustmentResult,
    PaperAdjustmentPreview,
    PaperAnalysisResult,
)
from .tools.paper_tools import GeneratePaperToolResult
from .tools.read_tools import ReadCurrentPaperResult
from .tools.replacement_tools import ApplyReplacementResult, ReplacementDryRunResult
from .tools.version_tools import VersionOperationResult

logger = logging.getLogger(__name__)


# Blocking-error categories used by the final state machine.
# A code belongs to AT MOST ONE category; the categories drive the priority:
#   execution_error  >  pending_preservation  >  clarification  >  pending  >  completed
# Where execution_error is signalled by turn_error being set (caught exception),
# not by a string match.
CLARIFICATION_BLOCKING_ERRORS: frozenset[str] = frozenset({
    "knowledge_scope_conflict",
    "knowledge_unknown",
    "knowledge_ambiguous",
    "knowledge_scope_uncertain",
    "missing_scope",
    "missing_exam_scope",
    "scope_not_found",
    "scope_ambiguous",
    "missing_total_score",
    "missing_difficulty_ratio",
    "question_count_mismatch",
    "score_total_mismatch",
    "question_type_invalid",
    "candidate_insufficient",
    "insufficient_candidates",
    "generation_partial_patch_required",
    "score_rebalance_ambiguous",
    "pending_adjustment_not_updated",
    "paper_observation_required",
    "no_current_paper",
    "no_pending_generation",
    "no_pending_action",
    "no_pending_adjustment",
})

PENDING_PRESERVATION_ERRORS: frozenset[str] = frozenset({
    "pending_replacement_exists",
    "pending_generation_exists",
    "pending_adjustment_exists",
})


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
    adjustment_preview: PaperAdjustmentPreview | None = None
    adjustment: ConfirmAdjustmentResult | None = None
    paper_read: ReadCurrentPaperResult | None = None
    generation_preview: GenerationPlanPreview | None = None


class _PaperGroundingDecision(BaseModel):
    """Validated decision about whether the final answer needs Paper state."""

    model_config = ConfigDict(extra="forbid")

    paper_observation_required: bool
    requested_positions: list[int] = Field(default_factory=list)
    answer: str = ""


_SYSTEM_PROMPT = """你是 Teacher Agent。

你的任务是通过自然语言帮助教师完成组卷、查看试卷、调整题目、解释试卷以及管理试卷版本。
你拥有一组工具，可以读取或修改系统中的真实数据。

你必须自主判断当前问题是否需要工具、调用哪个工具、是否需要连续调用多个工具，以及获得工具结果后下一步做什么。不要要求用户使用固定指令格式。

普通聊天、能力介绍和不依赖系统事实的解释，直接用自然语言回答，不调用工具。回答依赖当前试卷、题目、题库或版本的真实状态时，不要猜测，主动调用对应读取工具。教师明确询问一个或多个题号时，read_current_paper 必须传 positions 且只读取这些题号；只有整卷概览问题才省略 positions。你可以连续调用多个工具完成一个用户目标。

工具负责确定性业务执行。你不得绕过工具直接修改数据库，不得编造 Paper、Question、KnowledgeNode 或版本 ID，不得把工具失败说成成功。Tool 返回约束失败时，应根据 Observation 向教师解释；必要时可以调用其他相关工具，但不能擅自放宽教师约束。

所有新组卷请求必须先调用 preview_generation_plan 展示方案。教师没有明确说明的题型、题量和分值必须省略，由 Python 使用现有默认模板；禁止自行编造。预览后必须等待教师明确确认，下一轮才能调用 confirm_generation_plan。confirm 返回的 ok=true 只表示草稿已成功创建；必须继续检查 validation_report：passed=false 时明确说明审核未通过及具体违规，不得宣称试卷已校验通过或可直接下载使用。

Working Memory 只用于理解当前任务、上一轮追问和上一份试卷引用。已有 pending generation 时，preview_generation_plan 的参数是本轮 patch，Python 会与完整 pending request 合并；不要重复猜测或删掉教师此前已确认字段。“不要与上一套重复”当前只会被记录为 unsupported preference，必须明确说明当前 Tool 尚不能保证排重，绝不能声称已经避免重复。

教师只修改某个题型的数量或分值时，使用 question_type_patches 只表达变更字段。pending 中的目标总分会保留，Python 会按0.5分粒度尝试确定性平衡；不得自行把100分改成85分。Tool 返回 score_rebalance_ambiguous 时，按其 clarification 追问，不得自行凑分。

单题换题和整卷调整都先生成 preview，必须等教师明确确认后才能调用对应 confirm 工具。不要在同一轮预览后替教师自行确认。

当前工作区上下文中的 pending 是业务状态事实，优先于聊天中的口头描述。教师接受或拒绝 pending 单题换题时，必须分别调用 confirm_replace_question 或 cancel_replace_question；教师接受 pending 整卷调整时必须调用 confirm_adjust_paper。没有对应 Tool Observation 时，绝不能声称已经确认、取消或应用。

教师表示不接受、不要或放弃当前 pending 方案时，含义是取消该方案，不是自动再生成一个候选。只有教师明确要求“再找一个、换个候选”时，才可以先取消当前 pending，再调用 preview_replace_question 生成新方案；不得用新 preview 静默覆盖旧 pending。

教师要求换题时“知识点不变、别动知识点、保持考点”属于硬约束，调用 preview_replace_question 必须显式传 preserve_knowledge_points=true。不得把仅部分重合的知识点描述成完全保持。

如果用户要求先读取再换题、撤销后再处理、或读取后解释，可以按需要连续调用多个工具。读取 Tool 只是获取事实；如果教师同一请求还要求调整、替换或分析，读取后必须在本轮继续调用完成该目标所需的 Tool。

你没有后台异步任务能力。禁止回复“正在寻找、请稍等、稍后完成”后停止。必须在当前 Agent Loop 中继续执行，直到得到 preview、明确的业务阻塞、需要教师确认/补充，或目标已经完成，再给出清晰的中文回复。"""


def build_teacher_agent_backend(settings: Settings) -> ChatBackend | None:
    if not settings.siliconflow_api_key:
        return None
    return BailianChatBackend(
        api_key=settings.siliconflow_api_key,
        base_url=settings.siliconflow_base_url,
        model=settings.siliconflow_agent_model,
        timeout=settings.siliconflow_timeout_seconds,
    )


def _assistant_message(raw: dict) -> dict:
    message = raw.get("message", raw)
    if not isinstance(message, dict):
        raise ValueError("agent_invalid_model_response")
    return message


def _tool_arguments(call: dict) -> tuple[str, dict]:
    function = call.get("function") or {}
    name = function.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("agent_invalid_tool_call")
    raw = function.get("arguments", {})
    arguments = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(arguments, dict):
        raise ValueError("agent_invalid_tool_arguments")
    return name, arguments


def _merge_result_fields(target: dict[str, Any], values: dict[str, Any]) -> None:
    for key, value in values.items():
        if key in {"warnings", "blocking_errors", "clarification_questions"}:
            existing = target.setdefault(key, [])
            existing.extend(item for item in value if item not in existing)
        elif value is not None:
            target[key] = value


def _contains_leaked_tool_protocol(content: str) -> bool:
    return any(marker in content for marker in (
        "</think>", "<tool_call", "</tool_call>", "<arg_key>", "<arg_value>",
    ))


def _paper_grounding_messages(
    *,
    message: str,
    serialized_context: str,
    format_retry: bool = False,
) -> list[dict[str, str]]:
    """Build a history-free gate that cannot reuse stale Paper claims."""
    return [
        {
            "role": "system",
            "content": (
                "你是 Teacher Agent 的 Paper 事实核验步骤。只处理教师当前这条原始消息，"
                "忽略此前聊天中的 Paper 内容和上一条未验证回答。"
                "判断回答是否涉及当前试卷、当前题目、分值、难度、知识点或当前版本的事实。"
                "版本刚变化时，旧版本读取结果一律无效。"
                "本步骤没有工具，不要尝试回答 Paper 事实。"
                "需要 Paper 事实时只返回 JSON；若教师明确提到题号，将题号放入 requested_positions，"
                "例如“第五题是什么”返回："
                '{"paper_observation_required":true,"requested_positions":[5],"answer":""}；'
                "整卷问题则返回 requested_positions:[]。"
                "如果请求完全不依赖当前 Paper（例如问候、能力介绍、通用知识解释），"
                "只返回一个 JSON 对象，格式必须严格为："
                '{"paper_observation_required":false,"answer":"给教师的自然语言回答"}。'
                "不得把任何当前 Paper 事实放进该 JSON 回答。"
                + (
                    "上一条无工具响应未通过结构校验；这次禁止 Markdown 代码块、前后说明或纯文本，"
                    "只能返回上述两种 JSON 之一。"
                    if format_retry else ""
                )
            ),
        },
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


def _paper_read_messages(
    *,
    message: str,
    serialized_context: str,
    requested_positions: list[int] | None = None,
    retry: bool = False,
) -> list[dict[str, str]]:
    scope_instruction = (
        f"教师明确询问题号 {requested_positions}；必须调用 "
        f"read_current_paper(positions={requested_positions})，不得扩大为整卷读取。"
        if requested_positions else
        "教师询问整卷情况；调用 read_current_paper 时可以省略 positions。"
    )
    return [
        {
            "role": "system",
            "content": (
                "教师当前请求已经通过事实核验，确认依赖当前 Paper。"
                "必须立即调用 read_current_paper 获取当前版本事实。"
                + scope_instruction
                + "不得凭聊天历史回答，不得在没有 Tool Observation 时输出事实。"
                + ("上一条没有调用读取工具，这次必须调用。" if retry else "")
            ),
        },
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


_QUESTION_POSITION_PATTERN = re.compile(r"第\s*(\d+|[一二三四五六七八九十]+)\s*题")
_CHINESE_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _question_position(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if "十" in value:
        tens, ones = value.split("十", 1)
        tens_value = _CHINESE_DIGITS.get(tens, 1) if tens else 1
        ones_value = _CHINESE_DIGITS.get(ones, 0) if ones else 0
        return tens_value * 10 + ones_value
    return _CHINESE_DIGITS.get(value)


def _explicit_question_positions(message: str) -> list[int]:
    """Extract only explicit 第N题 references; this narrows a read, never routes an action."""
    positions: list[int] = []
    for match in _QUESTION_POSITION_PATTERN.finditer(message):
        position = _question_position(match.group(1))
        if position and position not in positions:
            positions.append(position)
    return positions


def _parse_paper_grounding_decision(content: str) -> _PaperGroundingDecision | None:
    normalized = content.strip()
    object_start = normalized.find("{")
    object_end = normalized.rfind("}")
    if object_start >= 0 and object_end > object_start:
        normalized = normalized[object_start:object_end + 1]
    try:
        return _PaperGroundingDecision.model_validate_json(normalized)
    except (ValueError, TypeError):
        return None


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
    """Return a closure that writes ``turn_span.update(output=...)`` safely.

    Langfuse may be unavailable (``turn_span is None``) or its SDK may throw
    on update; both must be silent. Errors themselves are surfaced through
    ``error`` argument so the agent span is marked ``level=ERROR`` when the
    turn was a genuine execution failure.
    """
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


def run_teacher_agent(
    session: Session,
    user_message: str,
    *,
    conversation_id: str | None = None,
    paper_id: str | None = None,
    version_id: str | None = None,
    state_store: PendingReplacementStore | None = None,
    backend: ChatBackend | None = None,
    max_tool_rounds: int = 8,
    trace_recorder: AgentTraceRecorder | None = None,
) -> TeacherAgentResult:
    """Run LLM → tool observation → LLM until a final natural-language answer."""
    message = user_message.strip() if isinstance(user_message, str) else ""
    trace_recorder = trace_recorder or AgentTraceRecorder()
    trace_recorder.start(
        conversation_id=conversation_id,
        paper_id=paper_id,
        user_input=message,
    )
    # ── Run-Level Tracing (source of truth for this turn) ──
    # Create exactly one run_id BEFORE any business logic or early return so a
    # request that fails fast (e.g. model unavailable) still produces a queryable
    # trace. All writes are best-effort and degrade to a no-op if the DB/manager
    # fails, so business behaviour is never affected.
    run_manager = TeacherAgentRunManager(
        session, conversation_id, paper_id, message
    ).create()
    run_id = run_manager.run_id
    agent_span = (
        run_manager.add_span("agent", "teacher_agent_run") if run_manager.row is not None else None
    )
    if agent_span is not None:
        run_manager.mark_running()
    store: PendingReplacementStore | None = None
    context: AgentExecutionContext | None = None
    turn_error: dict[str, Any] | None = None  # captured for langfuse agent span

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
            # Finalize the local run-level trace (best-effort; never breaks business).
            run_manager.finalize(
                status=result.status,
                final_response=result.message,
                state_after=_working_memory_snapshot(store, conversation_id),
                paper_id=context.paper_id if context is not None else paper_id,
                error=error,
            )
            run_manager.update_span(
                agent_span,
                # A genuine exception is a technical error (span=error); a normal
                # business return -- even status="failed" (e.g. insufficient
                # candidates / model unavailable) -- is NOT a span error.
                status="error" if error is not None else "success",
                output={"status": result.status, "message": result.message},
            )
            result.run_id = run_id
            return result

    explicit_question_positions = _explicit_question_positions(message)
    history_store = DatabaseConversationHistoryStore(session) if conversation_id else None
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

    store = state_store or (DatabasePendingReplacementStore(session) if conversation_id else None)
    context = AgentExecutionContext(
        session=session,
        conversation_id=conversation_id,
        paper_id=paper_id,
        version_id=version_id,
        state_store=store if isinstance(store, DatabasePendingReplacementStore) else store,
    )
    tools = build_agent_tools(context)
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
    if pending:
        definition_names = [
            "confirm_replace_question",
            "cancel_replace_question",
            "read_current_paper",
        ]
    else:
        definition_names = list(tools)
    definitions = [tools[name].definition() for name in definition_names]
    working_memory = (
        store.get_memory(conversation_id)
        if store and conversation_id and hasattr(store, "get_memory") else None
    )
    trace_recorder.set_memory_before(
        working_memory.model_dump(mode="json") if working_memory else None
    )
    run_manager.set_state_before(
        working_memory.model_dump(mode="json") if working_memory else None
    )
    dynamic_context = {
        "current_paper": {
            "exists": bool(version_id or paper_id),
            "paper_id": paper_id,
            "version_id": version_id,
        },
        "pending": (
            {"type": "single_question_replacement", "position": pending.target_position}
            if pending else {"type": "generation_plan"}
            if pending_generation else {"type": "whole_paper_adjustment", "plan_id": pending_adjustment}
            if pending_adjustment else None
        ),
        "working_memory": working_memory.model_dump(mode="json") if working_memory else None,
    }
    serialized_context = json.dumps(dynamic_context, ensure_ascii=False)
    system_content = (
        _SYSTEM_PROMPT
        + "\n\n当前工作区上下文："
        + serialized_context
    )
    current_user_content = (
        message
        + "\n\n<current_workspace_state>"
        + serialized_context
        + "</current_workspace_state>"
    )
    messages: list[dict] = [
        {"role": "system", "content": system_content},
        *recent_messages,
        {"role": "user", "content": current_user_content},
    ]
    result_values: dict[str, Any] = {
        "warnings": [],
        "blocking_errors": [],
        "clarification_questions": [],
    }
    turn_status: Literal["completed", "needs_clarification", "waiting_confirmation", "failed"] = "completed"
    current_stage = "init"
    trace_calls: list[dict[str, Any]] = []
    pending_state_at_turn_start = bool(pending or pending_adjustment or pending_generation)
    pending_state_rechecked = False
    pending_adjustment_rechecked = False
    paper_state_at_turn_start = bool(version_id or paper_id)
    paper_version_at_turn_start = context.version_id or context.paper_id
    paper_grounding_rechecked = False
    paper_grounding_format_retried = False
    paper_read_required = False
    paper_read_call_retried = False
    paper_observation_version_id: str | None = None
    malformed_response_retried = False
    # Reuse the run-level trace row created at the very top so there is exactly
    # ONE row per turn (carrying run_id + the legacy tool_calls_json fields used
    # by existing tests). If run creation failed, fall back to a standalone
    # legacy row (no run_id) so the historical write path is preserved.
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
        for _round in range(max_tool_rounds + 1):
            current_stage = "llm_call"
            model_span = run_manager.add_span(
                "model_call", "llm_completion",
                parent_span_id=agent_span.span_id if agent_span is not None else None,
                input={"n_messages": len(messages), "n_definitions": len(definitions)},
            )
            with llm_generation_span(backend, messages, definitions) as _lf_llm:
                try:
                    response_message = _assistant_message(backend.complete(messages, definitions))
                    run_manager.update_span(
                        model_span,
                        status="success",
                        output={"tool_calls": len(response_message.get("tool_calls") or [])},
                        ended_at=datetime.now(UTC),
                    )
                except Exception as exc:
                    _langfuse_update(_lf_llm, level="ERROR", status_message=str(exc))
                    run_manager.update_span(
                        model_span, status="error", output={"error": str(exc)},
                        ended_at=datetime.now(UTC),
                    )
                    raise
                _langfuse_update(
                    _lf_llm,
                    output={"response": redact_trace_value(response_message)},
                )
            current_stage = "response_parse"
            tool_calls = response_message.get("tool_calls") or []
            if not tool_calls:
                content = response_message.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("agent_missing_final_response")
                if _contains_leaked_tool_protocol(content):
                    if malformed_response_retried:
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
                adjustment_observed = any(
                    call["tool_name"] in {"preview_adjust_paper", "confirm_adjust_paper"}
                    for call in trace_calls
                )
                if pending_adjustment and not adjustment_observed and not pending_adjustment_rechecked:
                    messages.extend([
                        {"role": "assistant", "content": content.strip()},
                        {
                            "role": "user",
                            "content": (
                                "<pending_adjustment_guard>当前存在待确认的整卷 AdjustmentPlan。"
                                "如果教师正在修改该方案，必须调用 preview_adjust_paper 生成并保存新版 preview；"
                                "target_total_score 会与旧方案中的删除位置确定性合并。"
                                "如果教师明确接受当前方案，必须调用 confirm_adjust_paper。"
                                "没有对应 Tool Observation 时，不得声称方案已更新或已应用。"
                                "请重新处理教师本轮原始请求。</pending_adjustment_guard>"
                            ),
                        },
                    ])
                    definitions = [
                        tools[name].definition()
                        for name in ("read_current_paper", "preview_adjust_paper", "confirm_adjust_paper")
                    ]
                    pending_adjustment_rechecked = True
                    continue
                if pending_adjustment and not adjustment_observed and pending_adjustment_rechecked:
                    final_text = (
                        "当前待确认的整卷调整方案没有被更新，本轮不会确认旧方案。"
                        "请重试你的修改要求，我会先生成新的调整预览。"
                    )
                    turn_status = "needs_clarification"
                    result_values["blocking_errors"].append("pending_adjustment_not_updated")
                    break
                if pending_state_at_turn_start and not trace_calls and not pending_state_rechecked:
                    pending_guard = (
                        "你正在处理一个已经存在的组卷方案。教师明确接受时必须调用 "
                        "confirm_generation_plan；教师提出修改时必须调用 preview_generation_plan "
                        "生成新的方案。未经 Tool Observation 不得声称已经组卷。"
                        if pending_generation else
                        "你正在处理一个已经存在的单题换题 pending 事务。忽略更早对话中的建议，"
                        "只根据教师当前这条原始消息和最新 pending 状态行动。"
                        "教师接受方案时调用 confirm_replace_question；拒绝、不要或放弃方案时调用 "
                        "cancel_replace_question；需要查看原卷时调用 read_current_paper。"
                        "如果只是询问方案，可以直接回答。不得生成新 preview，也不得在没有 Tool "
                        "Observation 时声称状态已改变。"
                    )
                    messages = [
                        {
                            "role": "system",
                            "content": pending_guard,
                        },
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
                if pending_state_at_turn_start and not trace_calls and pending_state_rechecked:
                    final_text = (
                        "当前待确认方案仍未改变，本轮没有可靠执行确认或取消操作。"
                        "你可以让我重新展示方案，或再次明确确认/取消。"
                    )
                    break
                current_paper_version_id = context.version_id or context.paper_id
                paper_version_changed = (
                    paper_version_at_turn_start is not None
                    and current_paper_version_id != paper_version_at_turn_start
                )
                stale_paper_observation = (
                    paper_version_changed
                    and paper_observation_version_id is not None
                    and paper_observation_version_id != current_paper_version_id
                )
                needs_grounding_gate = (
                    paper_state_at_turn_start
                    and (not trace_calls or stale_paper_observation)
                )
                if needs_grounding_gate and not paper_grounding_rechecked:
                    messages = _paper_grounding_messages(
                        message=message,
                        serialized_context=json.dumps(
                            {
                                **dynamic_context,
                                "current_paper": {
                                    "exists": bool(current_paper_version_id),
                                    "paper_id": context.paper_id,
                                    "version_id": context.version_id,
                                },
                            },
                            ensure_ascii=False,
                        ),
                    )
                    definitions = []
                    paper_grounding_rechecked = True
                    continue
                if needs_grounding_gate and paper_grounding_rechecked:
                    decision = _parse_paper_grounding_decision(content)
                    if not paper_read_required and decision is not None:
                        if decision.paper_observation_required:
                            messages = _paper_read_messages(
                                message=message,
                                requested_positions=(
                                    decision.requested_positions or explicit_question_positions
                                ),
                                serialized_context=json.dumps(
                                    {
                                        **dynamic_context,
                                        "current_paper": {
                                            "exists": bool(current_paper_version_id),
                                            "paper_id": context.paper_id,
                                            "version_id": context.version_id,
                                        },
                                    },
                                    ensure_ascii=False,
                                ),
                            )
                            definitions = [tools["read_current_paper"].definition()]
                            paper_read_required = True
                            continue
                        independent_answer = decision.answer.strip()
                        if independent_answer:
                            final_text = independent_answer
                            break
                    if not paper_read_required and not paper_grounding_format_retried:
                        messages = _paper_grounding_messages(
                            message=message,
                            serialized_context=json.dumps(
                                {
                                    **dynamic_context,
                                    "current_paper": {
                                        "exists": bool(current_paper_version_id),
                                        "paper_id": context.paper_id,
                                        "version_id": context.version_id,
                                    },
                                },
                                ensure_ascii=False,
                            ),
                            format_retry=True,
                        )
                        definitions = []
                        paper_grounding_format_retried = True
                        continue
                    if paper_read_required and not paper_read_call_retried:
                        messages = _paper_read_messages(
                            message=message,
                            requested_positions=explicit_question_positions,
                            serialized_context=json.dumps(
                                {
                                    **dynamic_context,
                                    "current_paper": {
                                        "exists": bool(current_paper_version_id),
                                        "paper_id": context.paper_id,
                                        "version_id": context.version_id,
                                    },
                                },
                                ensure_ascii=False,
                            ),
                            retry=True,
                        )
                        definitions = [tools["read_current_paper"].definition()]
                        paper_read_call_retried = True
                        continue
                    final_text = (
                        "本轮没有取得当前试卷的有效读取结果，因此不能可靠回答这项 Paper 事实。"
                        "请重试，我会先读取当前版本。"
                    )
                    turn_status = "failed"
                    result_values["blocking_errors"].append("paper_observation_required")
                    break
                if paper_grounding_rechecked:
                    grounded_decision = _parse_paper_grounding_decision(content)
                    if grounded_decision is not None and grounded_decision.answer.strip():
                        final_text = grounded_decision.answer.strip()
                        break
                final_text = content.strip()
                break

            if _round >= max_tool_rounds:
                raise RuntimeError("agent_tool_round_limit")
            normalized_calls = []
            for call in tool_calls:
                normalized = dict(call)
                normalized["id"] = call.get("id") or f"call_{uuid4().hex}"
                normalized_calls.append(normalized)
            messages.append({
                "role": "assistant",
                "content": response_message.get("content"),
                "tool_calls": normalized_calls,
            })
            for call in normalized_calls:
                call_id = call["id"]
                current_stage = "tool_arguments_parse"
                name, arguments = _tool_arguments(call)
                if (
                    name == "read_current_paper"
                    and explicit_question_positions
                    and not arguments.get("positions")
                ):
                    # A read-only least-data guard: an explicit 第N题 request must not
                    # silently expand into loading the whole paper if the model omits it.
                    arguments = {**arguments, "positions": explicit_question_positions}
                tool = tools.get(name)
                tool_span = run_manager.add_span(
                    "tool_call", name,
                    parent_span_id=agent_span.span_id if agent_span is not None else None,
                    input={"arguments": redact_trace_value(arguments)},
                )
                memory_before_tool = _working_memory_snapshot(store, conversation_id)
                if tool is None:
                    execution_payload = {
                        "ok": False,
                        "code": "unknown_tool",
                        "message": f"不存在工具：{name}",
                    }
                    turn_status = "failed"
                    result_values["blocking_errors"].append("unknown_tool")
                    # A missing tool is a business failure of the call, not a crash:
                    # the span succeeds and the failure lives in its output.
                    run_manager.update_span(
                        tool_span, status="success",
                        output=redact_trace_value(execution_payload),
                        ended_at=datetime.now(UTC),
                    )
                else:
                    observed_version_id = context.version_id or context.paper_id
                    current_stage = "tool_execution"
                    with tool_observation_span(name, arguments) as _lf_tool:
                        try:
                            execution = execute_tool(tool, arguments)
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
                    turn_status = execution.status
                    _merge_result_fields(result_values, execution.result_fields)
                    run_manager.update_span(
                        tool_span, status="success",
                        output=redact_trace_value(execution_payload),
                        ended_at=datetime.now(UTC),
                    )
                    if name == "preview_generation_plan" and execution_payload.get("ok"):
                        # Earlier invalid patch attempts remain visible in trace, but a
                        # later validated preview resolves their transient user-facing state.
                        result_values["blocking_errors"] = []
                        result_values["clarification_questions"] = []
                    if name == "read_current_paper":
                        paper_observation_version_id = observed_version_id
                memory_after_tool = _working_memory_snapshot(store, conversation_id)
                # A state_transition span records the working-memory / pending-state
                # delta this tool call produced, hanging off the tool_call span
                # (mirrors the spec's span tree). A read-only tool simply shows
                # equal before/after snapshots.
                run_manager.add_span(
                    "state_transition", f"{name}_state_change",
                    parent_span_id=tool_span.span_id if tool_span is not None else None,
                    status="success",
                    input={"before": redact_trace_value(memory_before_tool)},
                    output={"after": redact_trace_value(memory_after_tool)},
                    ended_at=datetime.now(UTC),
                )
                trace_entry = {
                    "tool_call_id": call_id,
                    "tool_name": name,
                    "arguments": arguments,
                    "result": execution_payload,
                }
                if name == "read_current_paper":
                    trace_entry["paper_observation"] = {
                        "version_id": paper_observation_version_id,
                        "positions": arguments.get("positions"),
                        "ok": bool(execution_payload.get("ok")),
                        "code": execution_payload.get("code"),
                    }
                trace_calls.append(trace_entry)
                trace_recorder.record_tool_call(
                    tool_name=name,
                    arguments=arguments,
                    memory_before=memory_before_tool,
                    result=execution_payload,
                    memory_after=memory_after_tool,
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": json.dumps(execution_payload, ensure_ascii=False),
                })
        else:
            raise RuntimeError("agent_tool_round_limit")
    except Exception as exc:
        turn_status = "failed"
        code = str(exc) if str(exc).startswith("agent_") else "agent_execution_failed"
        if code not in result_values["blocking_errors"]:
            result_values["blocking_errors"].append(code)
        turn_error = {
            "error_code": code,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "error_stage": current_stage,
        }
        logger.exception("Teacher Agent turn failed at stage=%s code=%s", current_stage, code)
        final_text = "Teacher Agent 暂时无法完成这次请求，请稍后重试。"

    blocking_errors = result_values["blocking_errors"]
    pending_query_possible = bool(store and conversation_id)
    pending_action_in_store = False
    if pending_query_possible:
        pending_action_in_store = bool(
            store.get(conversation_id)
            or (store.get_adjustment(conversation_id) if hasattr(store, "get_adjustment") else None)
            or (store.get_generation(conversation_id) if hasattr(store, "get_generation") else None)
        )
    if turn_error is not None:
        # Genuine execution exception caught by the broad except block.
        # Never mask with a softer business status.
        pass
    elif any(code in PENDING_PRESERVATION_ERRORS for code in blocking_errors):
        # The existing pending action was kept; the tool refused this turn but
        # the teacher still has something waiting to confirm or cancel.
        turn_status = "waiting_confirmation"
    elif any(code in CLARIFICATION_BLOCKING_ERRORS for code in blocking_errors):
        # Business-level signal: the teacher can adjust parameters and re-run.
        # Must not be masked by a stale pending from earlier turns.
        turn_status = "needs_clarification"
    elif pending_action_in_store:
        # Successful turn that left a pending action awaiting confirmation.
        turn_status = "waiting_confirmation"
    elif pending_query_possible and turn_status == "waiting_confirmation":
        # The pending was cleared between turns; report the turn as completed.
        turn_status = "completed"

    if "avoid_previous_paper_questions_unsupported" in result_values["warnings"]:
        final_text = (
            "我已记住你希望新试卷不要与上一套重复，并保留了其他组卷条件。"
            "但当前 generate Tool 尚未支持跨试卷排重，因此本方案不能保证题目不重复；"
            "在排重能力接入前，我不会把这项偏好描述为已经执行。你仍可以检查并确认其他方案参数。"
        )

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
