"""Deterministic acceptance and status normalization for final responses."""

from dataclasses import dataclass
from typing import Any, Literal


Status = Literal["completed", "needs_clarification", "waiting_confirmation", "failed"]


@dataclass
class FinalizationInput:
    status: Status
    final_text: str
    result_values: dict[str, Any]
    turn_error: dict[str, Any] | None
    current_stage: str
    trace_calls: list[dict[str, Any]]
    pending_query_possible: bool
    pending_action_in_store: bool
    teaching_design_artifact_requested: bool
    active_design: dict[str, Any] | None


@dataclass(frozen=True)
class FinalizationDecision:
    status: Status
    final_text: str


def _successful_pending_confirmation_tool_observed(trace_calls: list[dict[str, Any]]) -> bool:
    return any(
        call.get("tool_name") in {
            "prepare_generation_plan", "preview_paper_changes",
            "create_teaching_design", "revise_teaching_design",
        }
        and isinstance(call.get("result"), dict)
        and call["result"].get("ok") is True
        for call in trace_calls
    )


def _recoverable_post_tool_narration_failure(data: FinalizationInput) -> bool:
    return (
        data.current_stage in {"llm_call", "response_parse"}
        and data.pending_action_in_store
        and _successful_pending_confirmation_tool_observed(data.trace_calls)
    )


def normalize_finalization(*, data: FinalizationInput, runtime_policy: Any) -> FinalizationDecision:
    """Apply false-success guards and normalize the final runtime status.

    This function intentionally does not perform state reads, retries, Tool
    execution, or lifecycle mutations.  Callers provide the final state snapshot
    and the accumulated observations.
    """
    status = data.status
    final_text = data.final_text
    errors = data.result_values["blocking_errors"]
    warnings = data.result_values["warnings"]

    if data.turn_error is not None:
        if _recoverable_post_tool_narration_failure(data):
            status = "waiting_confirmation"
            error_code = data.turn_error.get("error_code")
            if error_code:
                data.result_values["blocking_errors"] = [
                    code for code in errors if code != error_code
                ]
                errors = data.result_values["blocking_errors"]
            if "post_tool_narration_failed" not in warnings:
                warnings.append("post_tool_narration_failed")
            final_text = (
                "方案已成功生成并保存，当前等待确认。"
                "本轮说明文字生成超时，但不影响已保存的方案；"
                "你可以查看方案后确认，或继续修改。"
            )
    elif any(runtime_policy.preserves_pending_state(code) for code in errors):
        status = "waiting_confirmation"
    elif any(runtime_policy.is_clarification_error(code) for code in errors):
        status = "needs_clarification"
    elif data.pending_action_in_store:
        status = "waiting_confirmation"
    elif (
        data.pending_query_possible
        and status == "waiting_confirmation"
        # A create/revise Tool call is an authoritative confirmation boundary;
        # retain it even if the state snapshot is temporarily stale.
        and not any(
            call.get("tool_name") in {"create_teaching_design", "revise_teaching_design"}
            for call in data.trace_calls
        )
    ):
        status = "completed"

    design_tool_succeeded = any(
        item.get("tool_name") in {"create_teaching_design", "revise_teaching_design"}
        and isinstance(item.get("result"), dict)
        and item["result"].get("ok") is True
        for item in data.trace_calls
    )
    if (
        status == "completed" and design_tool_succeeded
        and (not data.active_design or data.active_design.get("status") != "confirmed")
    ):
        status = "waiting_confirmation"

    if data.teaching_design_artifact_requested and status == "completed":
        design_tool_succeeded = any(
            item.get("tool_name") in {"create_teaching_design", "revise_teaching_design"}
            and isinstance(item.get("result"), dict)
            and item["result"].get("ok") is True
            for item in data.trace_calls
        )
        design_persisted = bool(
            data.active_design
            and data.active_design.get("version_id")
            and data.active_design.get("status") in {"draft", "awaiting_confirmation", "confirmed"}
        )
        if not (design_tool_succeeded and design_persisted):
            status = "failed"
            if "teaching_design_not_created" not in errors:
                errors.append("teaching_design_not_created")
            final_text = "本轮没有通过 TeachingDesign 工具完成并保存教学设计，因此不能声明方案已经创建。请重试或补充教学范围。"
        elif data.active_design and data.active_design.get("status") != "confirmed":
            # A freshly created/revised design remains a confirmation boundary.
            status = "waiting_confirmation"

    if "avoid_previous_paper_questions_unsupported" in warnings:
        final_text = (
            "我已记住你希望新试卷不要与上一套重复，并保留了其他组卷条件。"
            "但当前 generate Tool 尚未支持跨试卷排重，因此本方案不能保证题目不重复；"
            "在排重能力接入前，我不会把这项偏好描述为已经执行。你仍可以检查并确认其他方案参数。"
        )
    return FinalizationDecision(status=status, final_text=final_text)
