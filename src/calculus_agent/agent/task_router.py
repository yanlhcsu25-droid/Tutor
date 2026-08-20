"""Lightweight task routing boundary for Teacher Agent.

This module is intentionally independent from ``agent.py`` runtime wiring.  It
classifies only the top-level workflow direction and applies deterministic state
/ operation overrides before any model-driven routing would be trusted.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskType(str, Enum):
    """First-version Teacher Agent task categories.

    The router must not encode generation constraints or Tool arguments.  Those
    belong to downstream domain workflows.
    """

    DIRECT_ACTION = "DIRECT_ACTION"
    TEACHING_PLANNING = "TEACHING_PLANNING"
    INFORMATION_REQUEST = "INFORMATION_REQUEST"


class TaskRoute(BaseModel):
    """Router output schema.

    Extra fields are forbidden so the router cannot grow into a broad
    requirement parser by accidentally returning chapter, score, difficulty,
    question counts, generation constraints, or Tool names.
    """

    model_config = ConfigDict(extra="forbid")

    task_type: TaskType
    confidence: float = Field(ge=0.0, le=1.0)
    clarification_needed: bool = False
    clarification_question: str | None = Field(default=None, max_length=300)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def clarification_is_consistent(self) -> "TaskRoute":
        if self.clarification_needed and not self.clarification_question:
            raise ValueError("clarification_question is required when clarification_needed")
        if not self.clarification_needed and self.clarification_question:
            raise ValueError("clarification_question must be empty unless clarification is needed")
        return self


class RoutingState(BaseModel):
    """Runtime state projection consumed by deterministic routing policy.

    This is a pure value object; it does not load or persist any database state.
    The Agent Runtime can later map its existing pending/workspace/current-paper
    state into this shape.
    """

    model_config = ConfigDict(extra="forbid")

    pending_generation: bool = False
    pending_paper_change: bool = False
    pending_replacement: bool = False
    current_paper: bool = False
    active_teaching_design: bool = False

    @property
    def has_pending(self) -> bool:
        return bool(
            self.pending_generation
            or self.pending_paper_change
            or self.pending_replacement
            or self.active_teaching_design
        )


RouteSource = Literal[
    "deterministic_state",
    "deterministic_operation",
    "router",
]


class WorkflowDecision(BaseModel):
    """Task routing decision plus provenance.

    ``source`` explains whether the decision came from strong runtime state,
    explicit operation wording, or the lightweight semantic router.
    """

    model_config = ConfigDict(extra="forbid")

    route: TaskRoute
    source: RouteSource


INSPECT_TOOLS: tuple[str, ...] = (
    "retrieve_curriculum_candidates",
    "inspect_curriculum",
    "inspect_question_bank",
)

GENERATION_TOOLS: tuple[str, ...] = (
    "prepare_generation_plan",
    "prepare_reinforcement_plan",
    "confirm_generation",
    "discard_pending_plan",
)

PAPER_OPERATION_TOOLS: tuple[str, ...] = (
    "read_paper",
    "analyze_paper",
    "preview_paper_changes",
    "confirm_paper_changes",
    "operate_paper_version",
    "discard_pending_plan",
)

TEACHING_DESIGN_TOOLS: tuple[str, ...] = (
    "create_teaching_design",
)
TEACHING_PLANNING_DRAFT_TOOLS: tuple[str, ...] = (
    "prepare_teaching_planning_draft",
)
TEACHING_SCOPE_DECISION_TOOLS: tuple[str, ...] = (
    "select_teaching_scope",
)
UNIFIED_SCOPE_TOOLS: tuple[str, ...] = (
    "retrieve_curriculum_candidates",
    "select_teaching_scope",
)


class ToolSurface(BaseModel):
    """Conceptual Tool surface mapping for a task type.

    ``reserved_tools`` documents future workflow entry points without exposing
    them as currently allowed tools.
    """

    model_config = ConfigDict(extra="forbid")

    task_type: TaskType
    allowed_tools: tuple[str, ...]
    reserved_tools: tuple[str, ...] = ()


def tool_surface_for(task_type: TaskType) -> ToolSurface:
    """Return the first-version Tool policy for a task category."""

    if task_type == TaskType.DIRECT_ACTION:
        return ToolSurface(
            task_type=task_type,
            allowed_tools=tuple(dict.fromkeys([
                *GENERATION_TOOLS,
                *PAPER_OPERATION_TOOLS,
                *UNIFIED_SCOPE_TOOLS,
            ])),
        )
    if task_type == TaskType.TEACHING_PLANNING:
        return ToolSurface(
            task_type=task_type,
            allowed_tools=(
                *INSPECT_TOOLS,
                *TEACHING_DESIGN_TOOLS,
                *TEACHING_PLANNING_DRAFT_TOOLS,
                *TEACHING_SCOPE_DECISION_TOOLS,
            ),
        )
    if task_type == TaskType.INFORMATION_REQUEST:
        return ToolSurface(
            task_type=task_type,
            allowed_tools=INSPECT_TOOLS,
        )
    raise ValueError(f"unsupported task type: {task_type}")


_CONFIRM_RE = re.compile(r"确认|可以|就按|按这个|生成吧|开始生成|同意|没问题")
_CANCEL_RE = re.compile(r"取消|放弃|不要(?:这个|了)?|算了|作废")
_PENDING_MODIFY_RE = re.compile(
    r"改|调整|修改|换成|增加|减少|删|删除|题型|分值|总分|难度|章节|范围|知识点"
)

_PAPER_OPERATION_RE = re.compile(
    r"(换|替换|删除|删掉|去掉|新增|增加|加)(?:[^，。；]*)题"
    r"|第\s*(?:\d+|[一二三四五六七八九十]+)\s*题"
    r"|选择题第|填空题第|计算题第|证明题第"
    r"|撤销|重做|恢复到|版本|分析(?:这|当前)?(?:套)?卷|读(?:一下)?(?:这|第)"
)

_DIRECT_ACTION_RE = re.compile(
    r"出(?:一套|个)?|生成|组卷|测试卷|练习卷|巩固卷|作业|期中|期末|测验|考试卷"
)

_TEACHING_PLANNING_RE = re.compile(
    r"安排复习|复习方案|教学设计|教学方案|怎么教|如何教|讲课|备课|教学重点|"
    r"学生[^，。；]*(学不好|总错|不会|薄弱|理解不了|掌握不好)|"
    r"帮我设计[^，。；]*(复习|课|教学)"
)

_INFORMATION_REQUEST_RE = re.compile(
    r"为什么|是什么|解释|讲讲|说明|有多少|多少道|题库|供给|有哪些|查询|查一下|覆盖情况|章节.*内容"
)

_AMBIGUOUS_PREP_RE = re.compile(r"准备(?:一下)?|复习(?:一下)?|看看(?:学生)?")
_EXPLICIT_CURRICULUM_SCOPE_RE = re.compile(
    r"第\s*[一二三四五六七八九十百0-9]+\s*章"
    r"|第\s*[一二三四五六七八九十百0-9]+\s*节"
    r"|上册|下册|全书|整本教材|期中|期末"
)


_TEACHING_DESIGN_ARTIFACT_RE = re.compile(
    r"(?:设计|制定|做|形成|创建)(?:一个|一份|个)?"
    r"(?:新的)?(?:复习方案|教学设计|教学方案)"
    r"|教学设计"
)


def requires_teaching_design_artifact(message: str) -> bool:
    """Whether the teacher explicitly requests a persisted TeachingDesign.

    This intentionally does not parse scope, assessment, or generation fields.
    """
    return bool(_TEACHING_DESIGN_ARTIFACT_RE.search(_normalize(message)))


def has_explicit_curriculum_scope(message: str) -> bool:
    """Return whether the teacher supplied a curriculum-like scope signal."""
    return bool(_EXPLICIT_CURRICULUM_SCOPE_RE.search(_normalize(message)))


class TaskRouter:
    """Small top-level task router with deterministic policy first."""

    ambiguous_question = "您希望我直接生成一套练习卷，还是先设计复习方案？"

    def decide(
        self,
        message: str,
        *,
        state: RoutingState | None = None,
        model_route: TaskRoute | None = None,
    ) -> WorkflowDecision:
        """Return a workflow decision for ``message``.

        Deterministic state / operation policy wins over the semantic router.
        ``model_route`` is an optional structured LLM classification that can be
        supplied by future runtime wiring; when absent, a conservative local
        heuristic is used so the module remains unit-testable in isolation.
        """

        normalized = _normalize(message)
        state = state or RoutingState()

        override = deterministic_route(normalized, state=state)
        if override is not None:
            return override

        if model_route is not None:
            return WorkflowDecision(route=model_route, source="router")

        return WorkflowDecision(route=classify_message(normalized), source="router")


_DEFAULT_ROUTER = TaskRouter()


def decide_task(
    message: str,
    *,
    state: RoutingState | None = None,
    model_route: TaskRoute | None = None,
) -> WorkflowDecision:
    """Convenience wrapper around the default router."""

    return _DEFAULT_ROUTER.decide(message, state=state, model_route=model_route)


def deterministic_route(message: str, *, state: RoutingState) -> WorkflowDecision | None:
    """Apply state and explicit-operation overrides before semantic routing."""

    if state.has_pending and (
        _CONFIRM_RE.search(message)
        or _CANCEL_RE.search(message)
        or _PENDING_MODIFY_RE.search(message)
        or _PAPER_OPERATION_RE.search(message)
    ):
        return WorkflowDecision(
            source="deterministic_state",
            route=TaskRoute(
                task_type=TaskType.DIRECT_ACTION,
                confidence=1.0,
                clarification_needed=False,
                reason="existing pending business state takes priority over task classification",
            ),
        )

    if state.current_paper and _PAPER_OPERATION_RE.search(message):
        return WorkflowDecision(
            source="deterministic_state",
            route=TaskRoute(
                task_type=TaskType.DIRECT_ACTION,
                confidence=1.0,
                clarification_needed=False,
                reason="current paper operation wording under active paper context",
            ),
        )

    if _PAPER_OPERATION_RE.search(message):
        return WorkflowDecision(
            source="deterministic_operation",
            route=TaskRoute(
                task_type=TaskType.DIRECT_ACTION,
                confidence=0.92,
                clarification_needed=False,
                reason="explicit paper operation wording",
            ),
        )

    if _DIRECT_ACTION_RE.search(message):
        return WorkflowDecision(
            source="deterministic_operation",
            route=TaskRoute(
                task_type=TaskType.DIRECT_ACTION,
                confidence=0.95,
                clarification_needed=False,
                reason="explicit generation or paper action wording",
            ),
        )

    return None


def classify_message(message: str) -> TaskRoute:
    """Conservative semantic classifier used when no model route is supplied."""

    if _TEACHING_PLANNING_RE.search(message):
        return TaskRoute(
            task_type=TaskType.TEACHING_PLANNING,
            confidence=0.86,
            clarification_needed=False,
            reason="teaching planning or student-learning goal wording",
        )

    if _INFORMATION_REQUEST_RE.search(message):
        return TaskRoute(
            task_type=TaskType.INFORMATION_REQUEST,
            confidence=0.84,
            clarification_needed=False,
            reason="information request or explanation wording",
        )

    if _AMBIGUOUS_PREP_RE.search(message):
        return TaskRoute(
            task_type=TaskType.TEACHING_PLANNING,
            confidence=0.55,
            clarification_needed=True,
            clarification_question=TaskRouter.ambiguous_question,
            reason="ambiguous preparation request without explicit action",
        )

    return TaskRoute(
        task_type=TaskType.INFORMATION_REQUEST,
        confidence=0.5,
        clarification_needed=True,
        clarification_question="您希望我查询信息、解释知识点，还是执行组卷/改卷操作？",
        reason="no high-confidence task signal found",
    )


def _normalize(message: str) -> str:
    if not isinstance(message, str):
        return ""
    return message.strip()
