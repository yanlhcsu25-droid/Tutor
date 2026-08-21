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
from calculus_agent.papers.addressing import QuestionAddress
from calculus_agent.application.teaching_design_generation import (
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
from calculus_agent.agent.context_metrics import measure_context
from calculus_agent.agent.langfuse_tracing import (
    llm_generation_span,
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
    requires_teaching_design_artifact,
    tool_surface_for,
)
from calculus_agent.agent.trace_log import AgentTraceRecorder, redact_trace_value
from calculus_agent.agent.tool_registry import AgentExecutionContext, build_agent_tools
from calculus_agent.agent.paper_change_service import PaperChangePreview
from calculus_agent.agent.paper_tool_registry import PAPER_TOOL_NAMES
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

logger = logging.getLogger(__name__)


QUESTION_OPERATION_SKILL = "paper_question_operations"
TEACHING_DESIGN_SKILL = "teaching_design"


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
    "add_question_score_required",
    "add_question_score_ambiguous",
    "paper_observation_required",
    "no_current_paper",
    "no_pending_generation",
    "no_pending_action",
    "no_pending_adjustment",
    "curriculum_scope_unresolved",
    "question_bank_scope_unresolved",
})

PENDING_PRESERVATION_ERRORS: frozenset[str] = frozenset({
    "pending_replacement_exists",
    "pending_generation_exists",
    "pending_adjustment_exists",
    "legacy_pending_replacement_exists",
})


_PENDING_CONFIRMATION_WRITING_TOOLS: frozenset[str] = frozenset({
    "prepare_generation_plan",
    "preview_paper_changes",
    "create_teaching_design",
    "revise_teaching_design",
})


def _successful_pending_confirmation_tool_observed(
    trace_calls: list[dict[str, Any]],
) -> bool:
    # True only when this turn successfully wrote pending domain state.
    for call in trace_calls:
        if call.get("tool_name") not in _PENDING_CONFIRMATION_WRITING_TOOLS:
            continue
        result = call.get("result")
        if isinstance(result, dict) and result.get("ok") is True:
            return True
    return False


def _recoverable_post_tool_narration_failure(
    *,
    current_stage: str,
    trace_calls: list[dict[str, Any]],
    pending_action_in_store: bool,
) -> bool:
    # A narration failure must not roll back an already-saved pending action.
    return (
        current_stage in {"llm_call", "response_parse"}
        and pending_action_in_store
        and _successful_pending_confirmation_tool_observed(trace_calls)
    )


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


class _PaperGroundingDecision(BaseModel):
    """Validated decision about whether the final answer needs Paper state."""

    model_config = ConfigDict(extra="forbid")

    paper_observation_required: bool
    requested_positions: list[int] = Field(default_factory=list)
    answer: str = ""


_SYSTEM_PROMPT = """你是 Teacher Agent。

你的职责是理解教师自然语言，并通过系统提供的 Tool 完成真实业务操作。数据库 / Tool Observation 是业务事实来源；Conversation History 和 Working Memory 只用于语言理解、指代和任务连续性。

不要绕过 Tool 修改数据库，不要编造 Paper、Question、KnowledgeNode、Version ID、分值、难度、知识点或执行结果。Tool 失败不能描述成成功；Preview 不能描述成已经应用。

## 新建试卷

新建试卷使用 `prepare_generation_plan`。它只准备和校验组卷方案，不创建试卷。教师明确确认当前 pending generation 后，才调用 `confirm_generation`。

明确的新建试卷请求属于 direct generation，不要绕去 TeachingDesign。先按需真实调查 curriculum / question bank（`inspect_curriculum`、`inspect_question_bank`），然后必须在同一轮继续调用 `prepare_generation_plan` 生成可编辑蓝图卡片；不得只返回自然语言建议方案就结束。蓝图卡片本身就是预览：教师可在卡片内修改、用自然语言补充，或点击确认生成；只有“确认生成”调用 `confirm_generation`。`prepare_generation_plan` 成功后，聊天正文必须先给教师一个简短的“组卷设计意图”，然后由 GenerationPlanPreview 卡片展示可编辑蓝图。“组卷设计意图”只解释：本套试卷主要针对什么知识或能力、为什么把这些内容作为重点、本次测评希望验证学生哪方面的掌握情况；如果教师提供了学生薄弱点，还要说明当前方案如何针对该薄弱点。设计意图必须基于教师本轮原始要求和真实 Tool Observation，不得编造题库供给、知识点归属或已经执行的组卷结果。聊天正文不要重复卡片中的具体题型数量、每题分值、总分等结构化明细。设计意图只是给教师看的语义解释，不是 Generation Constraint 的 source of truth。

已有 pending generation 时，`prepare_generation_plan` 是 PATCH-only：只提交教师本轮真正改变的字段。题型数量或分值变化使用 `question_type_patches`；禁止重发完整 `question_type_requirements`。Python 会从 pending source of truth 合并未修改字段并确定性重平衡。

教师明确表示“算了、不出了、不要这个方案”时调用 `discard_pending_plan`。它只清除未提交计划，不修改已有试卷。普通修改 pending generation 不需要先 discard。

## 错题反馈与巩固卷

当教师是在反馈当前已生成试卷中的错题，并希望据此继续强化、再出一套、出巩固卷、针对错题练习时，使用 `prepare_reinforcement_plan`。题目引用必须遵循当前 Paper addressing 规则：“选择题第2题”使用 section_type + section_order；只有明确说“全卷第N题”才使用 position；无法唯一定位时必须澄清。

不要根据题目文本自行判断知识点、章节或 reinforcement weight。这些必须来自 `prepare_reinforcement_plan` 的 Tool Observation。`prepare_reinforcement_plan` 只准备新的 GenerationPlanPreview，不创建 Paper。教师明确确认后继续使用 `confirm_generation`。不要为错题反馈新增第二套 confirmation lifecycle，也不要把它解释成高层 TeachingDesign。

Tool 成功后，根据 reinforcement_context 简短说明：哪些知识点成为下一套卷子的强化重点，以及这些重点来自哪些错题 evidence。不要把错题描述成已经证明学生“不会”某知识点，也不要重复蓝图卡中的题型数量、分值、总分。

## 当前试卷

读取当前试卷事实使用 `read_paper`；整卷确定性分析使用 `analyze_paper`。

教师可见题号默认是题型内编号，例如“填空题第2题”，应使用 `QuestionAddress(section_type, section_order)`。不得把题型内编号误当成全卷 position。只有教师明确说“全卷第N题”时才使用 legacy positions 读取；如果随后要修改该题，先通过 `read_paper` 得到其题型内地址，再创建修改计划。

已有试卷的所有写操作统一使用 `preview_paper_changes`。它可以在一个 request 中表达多个 operation：
- `replace_question`
- `remove_question`
- `add_questions`
- `change_question_score`
- `change_question_type_distribution`

`preview_paper_changes` 只生成一个整体修改计划，不修改 Paper。教师明确确认这个 pending paper-change plan 后，才调用 `confirm_paper_changes`。禁止在同一轮刚生成 preview 后替教师自动确认。

如果教师是在修改已有 pending paper-change plan，继续调用 `preview_paper_changes`，由 Python 从同一 base version 合并 / 重编译；不要自行在聊天里维护 merge 结果。

如果教师明确确认旧 pending，同时又提出新的额外修改，例如“刚才删除就按这个，再把第一题换掉”，应先 `confirm_paper_changes` 应用已确认方案，再基于新版本调用 `preview_paper_changes` 生成新的待确认方案。不要把尚未确认的新要求偷偷并入已经被教师确认的旧方案。

教师明确放弃任何未提交的 generation / paper-change 方案时调用 `discard_pending_plan`。该 Tool 不回滚 Paper，不恢复版本，只清 Pending。

删除题目且教师没有要求保持总分时，不要填写 `target_total_score`；删除分值自然从总分中消失。只有教师明确说“总分保持100分”或指定新总分时才传 `target_total_score`，由 Python 负责确定性重平衡。

`preserve_knowledge_points` 是显式 opt-in 硬约束。只有教师明确要求“知识点不变 / 保持考点 / 保留原知识点”等时，`replace_question` operation 才允许设置 true。教师只说“换一道、超纲、简单一点”不代表保持原知识点。

新增题目只表达题型、数量和教师明确指定的分值；不得由 LLM 指定 Question ID。候选选择、去重、scope、难度与分值校验全部交给 Python。

版本链统一使用 `operate_paper_version(action=undo|redo|restore)`；restore 必须提供 target_version。

## TeachingDesign 边界

普通“怎么教、重点是什么”等教学方法讨论可以直接自然语言回答；明确的新建试卷请求继续走 `prepare_generation_plan`。对于被当前任务模式识别为 `TEACHING_PLANNING`、且教师已明确给出教材章节范围的教学设计创建请求，直接调用 `create_teaching_design`，不要先调用 retrieve_curriculum_candidates、select_teaching_scope、inspect_curriculum 或 inspect_question_bank；系统会在该 Tool 内按固定顺序完成范围解析和环境调查。创建后必须等待教师确认；不得在同一轮自动确认或生成试卷。
当前会话已经存在未完成 TeachingDesign（draft / awaiting_confirmation）时，允许继续读取、修改或确认该既有设计，直到其生命周期结束。

## 执行原则

一个教师请求可以连续调用多个 Tool。Pending 是未完成业务状态，不是限制 Agent 能力的权限开关。只要当前请求需要，可以在同一轮读取、确认旧计划、再为新要求创建 preview。

你没有后台异步任务能力。不得回复“正在处理、稍后完成”后停止；必须在当前 Agent Loop 中推进到完成、明确阻塞、需要教师补充，或需要教师确认。"""


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
            for item in value:
                if item not in existing:
                    existing.append(item)
        elif value is not None:
            target[key] = value


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
                "需要 Paper 事实时只返回 JSON。当前试卷按题型分别编号。"
                "题型和题号可以按自然语序出现，不要求固定格式；"
                "例如“第三题这道填空题”仍可唯一表示填空题第3题。"
                "只要当前原始消息在语义上能够唯一确定题型内题号，"
                "就返回 paper_observation_required=true 且 requested_positions=[]；"
                "执行层若有高置信地址 hint 会使用它，否则可先读取当前 Paper 再继续语义判断。"
                "只有教师明确说“全卷第N题”时才把 N 放入 requested_positions。"
                "只有确实只有“第N题”且原始消息与可靠上下文都无法确定题型时才追问题型；"
                "不要因为固定正则未命中就判定有歧义。"
                "整卷问题返回 requested_positions:[]。"
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
    requested_addresses: list[QuestionAddress] | None = None,
    retry: bool = False,
) -> list[dict[str, str]]:
    if requested_addresses:
        payload = [
            address.model_dump(mode="json")
            for address in requested_addresses
        ]
        scope_instruction = (
            f"教师明确指定题型内地址 {payload}；必须调用 "
            f"read_paper(addresses={payload})，不得转换成全卷 position。"
        )
    elif requested_positions:
        scope_instruction = (
            f"教师明确指定全卷内部题号 {requested_positions}；必须调用 "
            f"read_paper(positions={requested_positions})。"
        )
    else:
        scope_instruction = (
            "教师询问整卷情况；调用 read_paper 时省略 addresses 和 positions。"
        )
    return [
        {
            "role": "system",
            "content": (
                "教师当前请求已经通过事实核验，确认依赖当前 Paper。"
                "必须立即调用 read_paper 获取当前版本事实。"
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


_QUESTION_NUMBER_VALUE = r"\d+|[一二三四五六七八九十]+"
_SECTION_QUESTION_PATTERN = re.compile(
    rf"(?P<section>选择题|多选题|填空题|计算题|证明题)\s*"
    rf"第\s*(?P<number>{_QUESTION_NUMBER_VALUE})\s*题"
)
_REVERSED_SECTION_QUESTION_PATTERN = re.compile(
    rf"第\s*(?P<number>{_QUESTION_NUMBER_VALUE})\s*题"
    rf"[^。！？,，；;]{{0,12}}?"
    rf"(?P<section>选择题|多选题|填空题|计算题|证明题)"
)
_GLOBAL_QUESTION_PATTERN = re.compile(
    rf"(?:全卷|全卷的)\s*第\s*(?P<number>{_QUESTION_NUMBER_VALUE})\s*题"
)
_CHINESE_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

_KNOWLEDGE_CONSTRAINT_TARGET = r"(?:知识点|考点)"
_KNOWLEDGE_PRESERVE_ACTION = (
    r"(?:保持|保留|不变|别变|不要变|"
    r"不动|别动|不要动|"
    r"不改|别改|不要改|"
    r"不调整|别调整|不要调整)"
)
_KNOWLEDGE_CONSTRAINT_GAP = r"[^。！？,，；;]{0,6}"

_PRESERVE_KNOWLEDGE_POINTS_PATTERN = re.compile(
    rf"{_KNOWLEDGE_CONSTRAINT_TARGET}"
    rf"{_KNOWLEDGE_CONSTRAINT_GAP}"
    rf"{_KNOWLEDGE_PRESERVE_ACTION}"
    rf"|{_KNOWLEDGE_PRESERVE_ACTION}"
    rf"{_KNOWLEDGE_CONSTRAINT_GAP}"
    rf"(?:原)?{_KNOWLEDGE_CONSTRAINT_TARGET}"
)


_TOTAL_SCORE_PATTERNS = (
    # "总分100" / "满分 100 分" / "总分保持90分"
    re.compile(
        r"(?:总分|满分|卷面(?:总)?分)"
        r"[^。！？,，；;]{0,8}?"
        r"\d{1,3}\s*分?"
    ),
    # "100分的试卷" / "100分制考试"
    re.compile(
        r"\d{1,3}\s*分(?:制|的)?\s*(?:试卷|卷子|测试|考试)"
    ),
    # Follow-up shorthand such as "90分就可以" / "90即可".
    re.compile(
        r"^\s*\d{1,3}\s*分?\s*(?:就可以|即可|可以|就行|行)?[。！？]?\s*$"
    ),
)


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


def _explicit_question_addresses(message: str) -> list[QuestionAddress]:
    """Extract only high-confidence section-local address hints.

    This parser is a positive fast path, not a semantic gate. If it cannot
    understand a teacher's wording, the original message still goes to the
    LLM + active Skill for semantic resolution.
    """
    addresses: list[QuestionAddress] = []
    seen: set[tuple[str, int]] = set()

    for pattern in (
        _SECTION_QUESTION_PATTERN,
        _REVERSED_SECTION_QUESTION_PATTERN,
    ):
        for match in pattern.finditer(message):
            section_order = _question_position(match.group("number"))
            if not section_order:
                continue

            address = QuestionAddress(
                section_type=match.group("section"),
                section_order=section_order,
            )
            key = (address.section_type, address.section_order)

            if key not in seen:
                addresses.append(address)
                seen.add(key)

    return addresses


def _explicit_question_positions(message: str) -> list[int]:
    """Extract only explicit legacy references such as 全卷第5题."""
    positions: list[int] = []

    for match in _GLOBAL_QUESTION_PATTERN.finditer(message):
        position = _question_position(match.group("number"))
        if position and position not in positions:
            positions.append(position)

    return positions


def _explicit_preserve_knowledge_points_requested(message: str) -> bool:
    """Return True only for an explicit teacher request to preserve KP/考点."""
    return _PRESERVE_KNOWLEDGE_POINTS_PATTERN.search(message) is not None



def _explicit_total_score_requested(message: str) -> bool:
    """Return True only when the teacher explicitly states a paper total score.

    This is provenance validation for a hard business constraint. A model-added
    default (for example total_score=100) must not silently become
    teacher_explicit.
    """
    return any(pattern.search(message) is not None for pattern in _TOTAL_SCORE_PATTERNS)


_EXPLICIT_QUESTION_COUNT_PATTERNS = (
    re.compile(r"(?:共|总共|一共|合计|总计)\s*\d{1,3}\s*(?:道|题)"),
    re.compile(r"(?:题量|总题数|题目数量)\s*[:：]?\s*\d{1,3}"),
    re.compile(
        r"^\s*\d{1,3}\s*(?:道|题)\s*"
        r"(?:就可以|即可|可以|就行|行)?[。！？]?\s*$"
    ),
)

_EXPLICIT_QUESTION_TYPE_COUNT_PATTERNS = (
    # "计算题5道" / "计算题 5" — Arabic digits may omit the unit.
    re.compile(
        r"(?:选择题|填空题|计算题|证明题)"
        r"[^。！？,，；;]{0,8}?"
        r"\d{1,3}\s*(?:道|题)?"
    ),
    # "计算题五道" / "计算题十题" — Chinese numerals MUST carry a unit.
    # This avoids false positives such as "计算题多一点".
    re.compile(
        r"(?:选择题|填空题|计算题|证明题)"
        r"[^。！？,，；;]{0,8}?"
        r"[一二三四五六七八九十两]+\s*(?:道|题)"
    ),
    # "5道计算题" / "五道计算题"
    re.compile(
        r"(?:\d{1,3}|[一二三四五六七八九十两]+)"
        r"\s*(?:道|题)\s*"
        r"(?:选择题|填空题|计算题|证明题)"
    ),
)


def _explicit_question_count_requested(message: str) -> bool:
    """High-confidence evidence that the teacher stated the whole-paper count."""
    return any(
        pattern.search(message) is not None
        for pattern in _EXPLICIT_QUESTION_COUNT_PATTERNS
    )


def _explicit_question_type_structure_requested(message: str) -> bool:
    """High-confidence evidence that the teacher stated per-type counts.

    The model may reason about a sensible structure, but exact counts are
    executable business constraints. If the teacher did not state them, the
    deterministic paper-type template must remain authoritative.
    """
    return any(
        pattern.search(message) is not None
        for pattern in _EXPLICIT_QUESTION_TYPE_COUNT_PATTERNS
    )


def _apply_question_reference_hints(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    addresses: list[QuestionAddress],
    positions: list[int],
) -> dict[str, Any]:
    """Fill only missing references from deterministic positive hints.

    Hints never decide semantic intent. For paper changes, only a single
    unresolved target may be filled from a single high-confidence address.
    """
    updated = dict(arguments)

    if tool_name == "read_paper":
        if not updated.get("addresses") and not updated.get("positions"):
            if addresses:
                updated["addresses"] = [
                    address.model_dump(mode="json")
                    for address in addresses
                ]
            elif positions:
                updated["positions"] = list(positions)
        return updated

    if tool_name != "preview_paper_changes" or len(addresses) != 1:
        return updated

    raw_operations = updated.get("operations")
    if not isinstance(raw_operations, list):
        return updated

    operations = [
        dict(operation) if isinstance(operation, dict) else operation
        for operation in raw_operations
    ]
    target_types = {
        "replace_question",
        "remove_question",
        "change_question_score",
    }
    unresolved = [
        index
        for index, operation in enumerate(operations)
        if isinstance(operation, dict)
        and operation.get("type") in target_types
        and operation.get("target") is None
    ]
    if len(unresolved) != 1:
        return updated

    index = unresolved[0]
    operations[index]["target"] = addresses[0].model_dump(mode="json")
    updated["operations"] = operations
    return updated


def _apply_explicit_opt_in_guards(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    message: str,
) -> dict[str, Any]:
    """Enforce model arguments that require explicit teacher provenance."""
    updated = dict(arguments)

    if tool_name == "prepare_generation_plan":
        # Hard/defaultable generation fields need teacher provenance.
        # Presence in an LLM Tool Call only proves that the model supplied the
        # field; it does NOT prove that the teacher requested it.
        if (
            "total_score" in updated
            and not _explicit_total_score_requested(message)
        ):
            updated.pop("total_score", None)

        if (
            "question_count" in updated
            and not _explicit_question_count_requested(message)
        ):
            updated.pop("question_count", None)

        if not _explicit_question_type_structure_requested(message):
            # Exact per-type counts/scores are executable structure. When the
            # teacher did not state a structure, discard model-invented values
            # and let the deterministic paper-type template compile defaults.
            updated.pop("question_type_requirements", None)
            updated.pop("question_type_patches", None)

    if (
        tool_name != "preview_paper_changes"
        or _explicit_preserve_knowledge_points_requested(message)
    ):
        return updated

    raw_operations = updated.get("operations")
    if not isinstance(raw_operations, list):
        return updated

    changed = False
    operations: list[Any] = []
    for raw in raw_operations:
        if not isinstance(raw, dict):
            operations.append(raw)
            continue
        operation = dict(raw)
        if (
            operation.get("type") == "replace_question"
            and operation.get("preserve_knowledge_points") is True
        ):
            operation["preserve_knowledge_points"] = False
            changed = True
        operations.append(operation)

    if changed:
        updated["operations"] = operations
    return updated


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
) -> TeacherAgentResult:
    """Run LLM → tool observation → LLM until a final natural-language answer."""
    message = user_message.strip() if isinstance(user_message, str) else ""
    teaching_design_artifact_requested = requires_teaching_design_artifact(message)

    # Restore workspace paper context before creating trace and agent context.
    # paper_id/version_id passed by API are optional hints.
    # Conversation workspace is the source of truth when they are missing.
    if conversation_id and (paper_id is None or version_id is None):
        from calculus_agent.agent.state import WorkspaceService

        workspace = WorkspaceService(session).get(conversation_id)

        if workspace is not None:
            paper_id = paper_id or workspace.current_paper_id
            version_id = version_id or workspace.current_version_id

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
    ).create()
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
            )
            run_manager.update_span(
                agent_span,
                status="error" if error is not None else "success",
                output={"status": result.status, "message": result.message},
            )
            result.run_id = run_id
            return result

        explicit_question_addresses = _explicit_question_addresses(message)
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
            owner_key=owner_key,
            run_id=run_id,
            user_message=message,
        )
        tools = build_agent_tools(context)
        toolkit = Toolkit(tools.values())
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

        if not has_current_paper:
            definition_names = [
                "prepare_generation_plan",
                *environment_definition_names,
                *design_definition_names,
            ]
            if task_decision.route.task_type == TaskType.TEACHING_PLANNING:
                definition_names.append("create_teaching_design")
            if pending_generation:
                definition_names.extend([
                    "confirm_generation",
                    "discard_pending_plan",
                ])
            if pending:
                definition_names.append("discard_pending_plan")
        elif pending or pending_generation or pending_adjustment:
            # Pending describes unfinished business; it must not hide the
            # Paper Agent's other domain capabilities.
            definition_names = list(PAPER_TOOL_NAMES)
        else:
            definition_names = list(tools)

        # A fresh TeachingDesign entry is available only to the dedicated
        # TEACHING_PLANNING route. History/activation remain legacy-only.
        teaching_design_tool_names_all = {
            "read_active_teaching_design",
            "create_teaching_design",
            "revise_teaching_design",
            "confirm_teaching_design",
            "discard_teaching_design",
            "search_teaching_design_history",
            "activate_teaching_design",
        }
        definition_names = [
            name
            for name in definition_names
            if (
                name not in teaching_design_tool_names_all
                or (
                    legacy_teaching_design_active
                    and name in design_definition_names
                )
                or (
                    task_decision.route.task_type == TaskType.TEACHING_PLANNING
                    and name == "create_teaching_design"
                )
            )
        ]

        definition_names = list(dict.fromkeys(
            name for name in definition_names if name in tools
        ))

        if pending_teaching_design_intent is not None:
            intent_tools = {
                "confirm": {"confirm_teaching_design"},
                "revise": {"revise_teaching_design"},
                "query": {"read_active_teaching_design"},
                "cancel": {"discard_teaching_design"},
                "ambiguous": set(),
            }[pending_teaching_design_intent.action]
            definition_names = [
                name for name in definition_names if name in intent_tools
            ]

        # Apply the new task surface only when no existing lifecycle owns the
        # turn. Pending/Paper/legacy TeachingDesign state remains authoritative
        # and keeps its established Tool surface unchanged.
        has_strong_business_state = bool(
            pending_generation
            or pending_adjustment
            or pending
            or has_current_paper
            or legacy_teaching_design_active
        )
        if (
            not has_strong_business_state
            and task_decision.route.task_type == TaskType.TEACHING_PLANNING
            and (
                teaching_design_artifact_requested
                or ("设计" in message and "复习方案" in message)
            )
            and has_explicit_curriculum_scope(message)
        ):
            # The unchanged create schema performs semantic requirement
            # extraction; its fixed business sequence runs in the workflow.
            context.use_teaching_design_workflow = True
            definition_names = ["create_teaching_design"]
        elif not has_strong_business_state and task_decision.route.task_type in {
            TaskType.TEACHING_PLANNING,
            TaskType.INFORMATION_REQUEST,
        }:
            allowed = set(tool_surface_for(task_decision.route.task_type).allowed_tools)
            definition_names = [
                name for name in definition_names if name in allowed
            ]
            # A teaching topic is not automatically a textbook scope. Without
            # an explicit chapter/section-like scope, let the model analyze the
            # learning problem and ask for scope only when material generation
            # actually requires curriculum grounding.
            if (
                task_decision.route.task_type == TaskType.TEACHING_PLANNING
                and not has_explicit_curriculum_scope(message)
            ):
                definition_names = [
                    "retrieve_curriculum_candidates",
                    "select_teaching_scope",
                    *([] if teaching_design_artifact_requested else [
                        "prepare_teaching_planning_draft",
                    ]),
                ]

        definitions = toolkit.schemas(
            names=definition_names,
            transform=lambda tool: _tool_definition_for_context(
                tool,
                pending_generation=bool(pending_generation),
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
            task_decision.route.task_type == TaskType.TEACHING_PLANNING
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

        messages, serialized_context, initial_context_metrics = context_builder.build(
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
        paper_version_at_turn_start = context.version_id or context.paper_id
        paper_grounding_rechecked = False
        paper_grounding_format_retried = False
        paper_read_required = False
        paper_read_call_retried = False
        paper_observation_version_id: str | None = None
        malformed_response_retried = False
        generation_patch_retried = False
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
            for _round in range(max_tool_rounds + 1):
                current_stage = "llm_call"
                active_skills = []
                if teaching_design_skill_active:
                    active_skills.append(TEACHING_DESIGN_SKILL)
                if question_operation_skill_active:
                    active_skills.append(QUESTION_OPERATION_SKILL)
                context_metrics = measure_context(
                    messages=messages,
                    tool_definitions=definitions,
                    serialized_context=serialized_context,
                )
                model_span = run_manager.add_span(
                    "model_call", "llm_completion",
                    parent_span_id=agent_span.span_id if agent_span is not None else None,
                    input={
                        "n_messages": len(messages),
                        "n_definitions": len(definitions),
                        "active_skills": active_skills,
                        "context_metrics": context_metrics.as_dict(),
                        "tool_round": _round,
                    },
                )
                llm_started_at = datetime.now(UTC)
                with llm_generation_span(backend, messages, definitions) as _lf_llm:
                    try:
                        if (
                            _round == 0
                            and pending_teaching_design_intent is not None
                            and pending_teaching_design_intent.action == "cancel"
                        ):
                            response_message = {
                                "tool_calls": [{
                                    "id": f"cancel_{uuid4().hex}",
                                    "type": "function",
                                    "function": {
                                        "name": "discard_teaching_design",
                                        "arguments": "{}",
                                    },
                                }],
                            }
                        else:
                            response_message = ToolLoop.run(
                                backend, messages, definitions
                            )
                        llm_ended_at = datetime.now(UTC)
                        run_manager.update_span(
                            model_span,
                            status="success",
                            output={
                                "tool_calls": len(response_message.get("tool_calls") or []),
                                "tool_names": [
                                    (call.get("function") or {}).get("name")
                                    for call in (response_message.get("tool_calls") or [])
                                ],
                                "tool_round": _round,
                                "context_metrics": context_metrics.as_dict(),
                                "llm_latency_ms": int(
                                    (llm_ended_at - llm_started_at).total_seconds() * 1000
                                ),
                            },
                            ended_at=llm_ended_at,
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

                    paper_change_lifecycle_observed = any(
                        call["tool_name"] in {
                            "preview_paper_changes",
                            "confirm_paper_changes",
                            "discard_pending_plan",
                            "operate_paper_version",
                        }
                        for call in trace_calls
                    )
                    if (
                        pending_adjustment
                        and trace_calls
                        and not paper_change_lifecycle_observed
                        and not pending_paper_change_rechecked
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
                            names=[
                                "read_paper",
                                "analyze_paper",
                                "preview_paper_changes",
                                "confirm_paper_changes",
                                "discard_pending_plan",
                            ]
                        )
                        pending_paper_change_rechecked = True
                        continue

                    if pending_state_at_turn_start and not trace_calls and not pending_state_rechecked:
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
                    if pending_state_at_turn_start and not trace_calls and pending_state_rechecked:
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
                    if (
                        not has_current_paper
                        and not pending
                        and not pending_adjustment
                        and not pending_generation
                        and not post_inspection_intent_rechecked
                        and any(
                            call["tool_name"] in environment_definition_names
                            for call in trace_calls
                        )
                        and not any(
                            call["tool_name"] == "prepare_generation_plan"
                            for call in trace_calls
                        )
                        and not any(
                            call["tool_name"] in design_definition_names
                            for call in trace_calls
                        )
                        and not any(
                            call["tool_name"] in teaching_design_tool_names_all
                            for call in trace_calls
                        )
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
                        post_inspection_names = [
                            "prepare_generation_plan",
                            *environment_definition_names,
                            *design_definition_names,
                        ]
                        if task_decision.route.task_type == TaskType.TEACHING_PLANNING:
                            post_inspection_names.append("create_teaching_design")
                        if task_decision.route.task_type in {
                            TaskType.TEACHING_PLANNING,
                            TaskType.INFORMATION_REQUEST,
                        }:
                            allowed = set(
                                tool_surface_for(
                                    task_decision.route.task_type
                                ).allowed_tools
                            )
                            post_inspection_names = [
                                name
                                for name in post_inspection_names
                                if name in allowed
                            ]
                        definitions = toolkit.schemas(
                            names=post_inspection_names,
                            transform=lambda tool: _tool_definition_for_context(
                                tool,
                                pending_generation=False,
                            ),
                        )
                        post_inspection_intent_rechecked = True
                        continue

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
                                        []
                                        if explicit_question_addresses
                                        else (
                                            decision.requested_positions
                                            or explicit_question_positions
                                        )
                                    ),
                                    requested_addresses=explicit_question_addresses,
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
                                definitions = toolkit.schemas(names=["read_paper"])
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
                                requested_addresses=explicit_question_addresses,
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
                            definitions = toolkit.schemas(names=["read_paper"])
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
                    if (
                        teaching_design_artifact_requested
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
                    name, arguments = ToolLoop.parse_call(call)
                    arguments = _apply_question_reference_hints(
                        tool_name=name,
                        arguments=arguments,
                        addresses=explicit_question_addresses,
                        positions=explicit_question_positions,
                    )
                    arguments = _apply_explicit_opt_in_guards(
                        tool_name=name,
                        arguments=arguments,
                        message=message,
                    )
                    blocked_confirmation = bool(
                        pending_teaching_design_intent is not None
                        and pending_teaching_design_intent.action == "revise"
                        and name == "confirm_teaching_design"
                    )
                    tool = None if blocked_confirmation else tools.get(name)
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
                    tool_execution_status: str | None = None
                    if blocked_confirmation:
                        execution_payload = {
                            "ok": False,
                            "code": "pending_design_revision_requires_revise",
                            "message": "本轮包含新的教学要求，不能确认当前教学设计；请先修改。",
                        }
                        tool_execution_status = "needs_clarification"
                        turn_status = "needs_clarification"
                        result_values["clarification_questions"].append(
                            "本轮包含新的教学要求，请先修改教学设计。"
                        )
                        result_values["blocking_errors"].append(
                            "pending_design_revision_requires_revise"
                        )
                    elif tool is None:
                        execution_payload = {
                            "ok": False,
                            "code": "unknown_tool",
                            "message": f"不存在工具：{name}",
                        }
                        turn_status = "failed"
                        result_values["blocking_errors"].append("unknown_tool")
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
                                execution = ToolLoop.execute(
                                    toolkit, name, arguments
                                )
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
                        _merge_result_fields(result_values, execution.result_fields)
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
                            output=redact_trace_value(execution_payload),
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
                                and not generation_patch_retried
                                and "generation_partial_patch_required"
                                in (execution_payload.get("blocking_errors") or [])
                            ):
                                generation_patch_retry_needed = True
                        if (
                            name == "select_teaching_scope"
                            and execution_payload.get("ok")
                        ):
                            refreshed_names = [
                                tool_name
                                for tool_name in tool_surface_for(
                                    TaskType.TEACHING_PLANNING
                                ).allowed_tools
                                if tool_name in tools
                            ]
                            if teaching_design_artifact_requested:
                                refreshed_names = [
                                    name for name in refreshed_names
                                    if name != "prepare_teaching_planning_draft"
                                ]
                            definitions = toolkit.schemas(names=refreshed_names)
                        if name == "read_paper":
                            paper_observation_version_id = observed_version_id
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
                            definitions = []
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
                    trace_entry = {
                        "tool_call_id": call_id,
                        "tool_name": name,
                        "arguments": arguments,
                        "result": execution_payload,
                    }
                    if name == "read_paper":
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
                    ToolLoop.append_observation(
                        messages,
                        call_id=call_id,
                        name=name,
                        payload=execution_payload,
                    )
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
                            final_text = "已形成教学规划草稿，请继续补充教材章节范围。"
                        else:
                            final_text = "已形成教学规划草稿，并已保留当前确认的教材范围。"

                    if (
                        tool_execution_status == "needs_clarification"
                        and not generation_patch_retry_needed
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
                            names=["prepare_generation_plan"],
                            transform=lambda tool: _tool_definition_for_context(
                                tool,
                                pending_generation=True,
                            ),
                        )
                        generation_patch_retried = True
                    if terminal_tool_boundary_reached or repeated_validation_boundary_reached:
                        # Stop processing any additional model-provided calls in
                        # this response as well as stopping the next LLM round.
                        break
                if (
                    clarification_boundary_reached
                    or terminal_tool_boundary_reached
                    or repeated_validation_boundary_reached
                ):
                    break
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
            active_design_after_turn = active_teaching_design_snapshot(
                session,
                owner_key=owner_key,
                conversation_id=conversation_id,
            )
            pending_action_in_store = bool(
                store.get(conversation_id)
                or (store.get_adjustment(conversation_id) if hasattr(store, "get_adjustment") else None)
                or (store.get_generation(conversation_id) if hasattr(store, "get_generation") else None)
                or (
                    active_design_after_turn
                    and active_design_after_turn.get("status")
                    == "awaiting_confirmation"
                )
            )
        if turn_error is not None:
            if _recoverable_post_tool_narration_failure(
                current_stage=current_stage,
                trace_calls=trace_calls,
                pending_action_in_store=pending_action_in_store,
            ):
                # The business Tool already committed a valid pending action.
                # A later LLM call is presentation/narration only; its failure
                # must not turn the committed business outcome into "failed".
                turn_status = "waiting_confirmation"
                error_code = turn_error.get("error_code")
                if error_code:
                    result_values["blocking_errors"] = [
                        item
                        for item in result_values["blocking_errors"]
                        if item != error_code
                    ]
                if "post_tool_narration_failed" not in result_values["warnings"]:
                    result_values["warnings"].append(
                        "post_tool_narration_failed"
                    )
                final_text = (
                    "方案已成功生成并保存，当前等待确认。"
                    "本轮说明文字生成超时，但不影响已保存的方案；"
                    "你可以查看方案后确认，或继续修改。"
                )
        elif any(code in PENDING_PRESERVATION_ERRORS for code in blocking_errors):
            turn_status = "waiting_confirmation"
        elif any(code in CLARIFICATION_BLOCKING_ERRORS for code in blocking_errors):
            turn_status = "needs_clarification"
        elif pending_action_in_store:
            turn_status = "waiting_confirmation"
        elif pending_query_possible and turn_status == "waiting_confirmation":
            turn_status = "completed"

        # A successful generation preview already caused one post-Tool LLM
        # response. Preserve that model-authored response so it can explain the
        # generation design intent. GenerationPlanPreview remains the structured,
        # executable source of truth shown by the editable blueprint card.
        #
        # Do not deterministically overwrite final_text here: doing so would erase
        # the semantic rationale while adding no business-state safety.

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
