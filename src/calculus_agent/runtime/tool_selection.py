"""Feature-gated, task-aware pruning of model-visible Tool schemas."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
import re
from typing import Any, Iterable

from calculus_agent.agent.task_router import TaskType
from calculus_agent.runtime.response_policy import ResponsePolicy

_FEATURE_FLAG = "AGENT_DYNAMIC_TOOL_SELECTION_ENABLED"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_CONFIRM = re.compile(r"确认|同意|就按|按这个|可以|没问题|开始(?:生成|执行)")
_CANCEL = re.compile(r"取消|放弃|作废|算了|不要(?:了|这个)")
_MODIFY = re.compile(r"改|调整|修改|换|增加|减少|删除|题型|分值|总分|难度|章节|范围|知识点")
_ANALYZE = re.compile(r"分析|分布|覆盖|结构")
_VERSION = re.compile(r"撤销|重做|恢复|版本")


@dataclass(frozen=True)
class ToolSelectionDecision:
    selected_names: tuple[str, ...]
    task_family: str
    reason: str
    fallback: bool
    feature_enabled: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def dynamic_tool_selection_enabled(value: str | bool | None = None) -> bool:
    """Read the flag at decision time so tests and deployments can toggle safely."""
    if isinstance(value, bool):
        return value
    raw = os.getenv(_FEATURE_FLAG, "false") if value is None else value
    return str(raw).strip().lower() in _TRUE_VALUES


def _available(names: Iterable[str], baseline: tuple[str, ...]) -> tuple[str, ...]:
    wanted = set(names)
    return tuple(name for name in baseline if name in wanted)


def _registered(names: Iterable[str], context: Any) -> tuple[str, ...]:
    return tuple(name for name in dict.fromkeys(names) if name in context.tool_names)


def _lifecycle_action(message: str) -> str | None:
    actions = {
        "confirm": bool(_CONFIRM.search(message)),
        "cancel": bool(_CANCEL.search(message)),
        "modify": bool(_MODIFY.search(message)),
    }
    selected = [name for name, matched in actions.items() if matched]
    return selected[0] if len(selected) == 1 else None


def select_tool_names(
    context: Any,
    phase1_names: Iterable[str],
    *,
    enabled: str | bool | None = None,
) -> ToolSelectionDecision:
    """Prune the Phase-1 surface, with a bounded state-safe mixed-task fallback."""
    baseline = tuple(dict.fromkeys(phase1_names))
    if not dynamic_tool_selection_enabled(enabled):
        return ToolSelectionDecision(
            selected_names=baseline,
            task_family="PHASE1",
            reason="feature_flag_disabled",
            fallback=False,
            feature_enabled=False,
        )

    message = str(context.message or "")
    low_confidence = bool(
        getattr(context, "clarification_needed", False)
        or float(getattr(context, "route_confidence", 1.0)) < 0.7
    )
    if low_confidence:
        return ToolSelectionDecision(
            baseline, "MIXED_UNKNOWN", "uncertain_route_safe_phase1_fallback", True, True
        )

    if context.legacy_teaching_design_active:
        return ToolSelectionDecision(
            baseline, "TEACHING_DESIGN", "active_design_lifecycle_owned_by_phase1", False, True
        )

    if context.pending_generation:
        action = _lifecycle_action(message)
        selected = {
            "confirm": ("confirm_generation",),
            "cancel": ("discard_pending_plan",),
            # Preserve the complete Pending Generation lifecycle for patches;
            # existing multi-action turns may still need confirmation or discard.
            "modify": baseline,
        }.get(action, baseline)
        fallback = action is None
        return ToolSelectionDecision(
            _available(selected, baseline),
            "GENERATION",
            f"pending_generation_{action or 'ambiguous'}",
            fallback,
            True,
        )

    if context.pending_paper_change or context.pending_replacement:
        action = _lifecycle_action(message)
        selected = {
            "confirm": ("confirm_paper_changes",),
            "cancel": ("discard_pending_plan",),
            "modify": ("read_paper", "preview_paper_changes", "discard_pending_plan"),
        }.get(action, baseline)
        fallback = action is None
        return ToolSelectionDecision(
            _available(selected, baseline),
            "PAPER_CHANGE",
            f"pending_paper_change_{action or 'ambiguous'}",
            fallback,
            True,
        )

    policy = ResponsePolicy()
    generation = policy.explicitly_requests_generation(message)
    paper_change = bool(
        context.has_current_paper
        and policy.paper_change_intent(message, paper_state_at_turn_start=True)
    )
    if generation and paper_change:
        safe_union = _registered(
            (
                "read_paper", "analyze_paper", "preview_paper_changes",
                "operate_paper_version", "prepare_generation_plan",
                "prepare_reinforcement_plan",
            ),
            context,
        )
        return ToolSelectionDecision(
            safe_union, "MIXED_UNKNOWN", "cross_task_bounded_state_safe_fallback", True, True
        )

    if context.task_type in {TaskType.TEACHING_DESIGN, TaskType.TEACHING_PLANNING}:
        return ToolSelectionDecision(
            baseline, "TEACHING_DESIGN", "phase1_teaching_chain_is_already_staged", False, True
        )

    if paper_change:
        names = ["read_paper", "preview_paper_changes"]
        if _ANALYZE.search(message):
            names.append("analyze_paper")
        if _VERSION.search(message):
            names.append("operate_paper_version")
        return ToolSelectionDecision(
            _available(names, baseline),
            "PAPER_CHANGE",
            "current_paper_preview_chain",
            False,
            True,
        )

    if generation:
        selected = baseline
        reason = "current_paper_generation_chain_preserved"
        if not context.has_current_paper:
            selected = _available(
                ("prepare_generation_plan", *context.environment_tool_names),
                baseline,
            )
            reason = "new_generation_inspect_then_preview_chain"
        return ToolSelectionDecision(selected, "GENERATION", reason, False, True)

    if context.task_type == TaskType.DIRECT_ACTION:
        if (
            not context.has_current_paper
            and getattr(context, "route_reason", "") != "explicit paper operation wording"
        ):
            selected = _available(
                ("prepare_generation_plan", *context.environment_tool_names),
                baseline,
            )
            return ToolSelectionDecision(
                selected,
                "GENERATION",
                "deterministic_direct_generation_chain",
                False,
                True,
            )
        return ToolSelectionDecision(
            baseline, "MIXED_UNKNOWN", "unclassified_direct_action_safe_fallback", True, True
        )

    if context.task_type == TaskType.INFORMATION_REQUEST:
        if context.has_current_paper:
            names = ["read_paper"]
            if _ANALYZE.search(message):
                names.append("analyze_paper")
            return ToolSelectionDecision(
                _available(names, baseline), "READ", "current_paper_read_only", False, True
            )
        return ToolSelectionDecision(
            baseline, "READ", "curriculum_and_question_bank_read_chain", False, True
        )

    return ToolSelectionDecision(
        baseline, "MIXED_UNKNOWN", "unsupported_route_safe_phase1_fallback", True, True
    )


def task_chain_tools(decision: ToolSelectionDecision, available_names: Iterable[str]) -> tuple[str, ...]:
    """Conservative multi-round capability union used by offline shadow audits."""
    available = tuple(sorted(set(available_names)))
    chains = {
        "READ": (
            "read_paper", "analyze_paper", "retrieve_curriculum_candidates",
            "inspect_curriculum", "inspect_question_bank", "read_active_teaching_design",
        ),
        "GENERATION": (
            "retrieve_curriculum_candidates", "inspect_curriculum", "inspect_question_bank",
            "prepare_generation_plan", "prepare_reinforcement_plan", "confirm_generation",
            "discard_pending_plan",
        ),
        "PAPER_CHANGE": (
            "read_paper", "analyze_paper", "preview_paper_changes",
            "confirm_paper_changes", "discard_pending_plan", "operate_paper_version",
        ),
        "TEACHING_DESIGN": (
            "retrieve_curriculum_candidates", "select_teaching_scope", "inspect_curriculum",
            "inspect_question_bank", "prepare_teaching_planning_draft",
            "read_active_teaching_design", "create_teaching_design", "revise_teaching_design",
            "confirm_teaching_design", "discard_teaching_design",
            "search_teaching_design_history", "activate_teaching_design",
        ),
    }
    names = decision.selected_names if decision.fallback else chains.get(decision.task_family, available)
    return tuple(name for name in available if name in set(names))


def compare_tool_exposure(
    toolkit: Any,
    full_names: Iterable[str],
    decision: ToolSelectionDecision,
) -> dict[str, Any]:
    """Compare schemas without calling a model or executing a Tool."""
    selected = decision.selected_names
    # A mixed-task fallback may safely union two Phase-1 surfaces. Treat that
    # bounded union as the comparison surface rather than reporting a negative reduction.
    full = tuple(dict.fromkeys([*full_names, *selected]))

    def chars(names: Iterable[str]) -> int:
        return sum(
            len(json.dumps(toolkit.schema(name), ensure_ascii=False, separators=(",", ":")))
            for name in names
        )

    full_chars = chars(full)
    selected_chars = chars(selected)
    return {
        **decision.as_dict(),
        "full_tool_count": len(full),
        "selected_tool_count": len(selected),
        "full_schema_chars": full_chars,
        "selected_schema_chars": selected_chars,
        "schema_chars_reduction": full_chars - selected_chars,
    }
