"""Deterministic response acceptance and boundary classification."""

import re
from typing import Any


class ResponsePolicy:
    """Classify a model response; callers own retries and message mutation."""

    @staticmethod
    def contains_leaked_tool_protocol(content: str) -> bool:
        return any(marker in content for marker in (
            "</think>", "<tool_call", "</tool_call>", "<arg_key>", "<arg_value>",
        ))

    @staticmethod
    def paper_change_lifecycle_observed(trace_calls: list[dict[str, Any]]) -> bool:
        return any(call["tool_name"] in {
            "preview_paper_changes", "confirm_paper_changes", "discard_pending_plan",
            "operate_paper_version",
        } for call in trace_calls)

    @classmethod
    def requires_pending_paper_change_recheck(
        cls, *, pending_adjustment: bool, trace_calls: list[dict[str, Any]], already_rechecked: bool,
    ) -> bool:
        return pending_adjustment and bool(trace_calls) and not cls.paper_change_lifecycle_observed(trace_calls) and not already_rechecked

    @staticmethod
    def paper_change_intent(message: str, *, paper_state_at_turn_start: bool = True) -> bool:
        return bool(
            paper_state_at_turn_start
            and re.search(r"(?:换|替换|删除|删掉|移除|修改|调整)", message)
            and re.search(r"(?:题|题目|分值|分数)", message)
        )

    @staticmethod
    def successful_observation(trace_calls: list[dict[str, Any]], tool_name: str) -> bool:
        return any(
            call["tool_name"] == tool_name and (call.get("result") or {}).get("ok")
            for call in trace_calls
        )

    @staticmethod
    def requires_post_inspection_recheck(
        *, has_current_paper: bool, pending: bool, pending_adjustment: bool,
        pending_generation: bool, already_rechecked: bool, trace_calls: list[dict[str, Any]],
        environment_tool_names: list[str], design_tool_names: list[str],
        is_teaching_design_tool: Any,
    ) -> bool:
        return (
            not has_current_paper and not pending and not pending_adjustment
            and not pending_generation and not already_rechecked
            and any(call["tool_name"] in environment_tool_names for call in trace_calls)
            and not any(call["tool_name"] == "prepare_generation_plan" for call in trace_calls)
            and not any(call["tool_name"] in design_tool_names for call in trace_calls)
            and not any(is_teaching_design_tool(call["tool_name"]) for call in trace_calls)
        )
