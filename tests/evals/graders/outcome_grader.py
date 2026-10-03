"""Cross-check final claims against Tool observations and persisted artifacts."""

from __future__ import annotations

import re
from typing import Any

_SUCCESS_CLAIM = re.compile(r"已(?:完成|生成|创建|修改|确认|删除|替换|应用|保存)|操作成功|生成成功")
_PAPER_CLAIM = re.compile(r"(?:试卷|成卷).*(?:已生成|生成成功)|已生成.*(?:试卷|成卷)")
_DESIGN_CLAIM = re.compile(r"教学设计.*(?:已创建|已保存|已完成)|已创建.*教学设计")
_PENDING_ARTIFACT_CONTEXT = re.compile(r"方案|预览|待确认|请确认")


def _calls(turn: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        call for call in ((turn.get("state") or {}).get("trace") or {}).get("tool_calls") or []
        if isinstance(call, dict)
    ]


def _result(call: dict[str, Any]) -> dict[str, Any]:
    value = call.get("result") or call.get("output_json") or {}
    return value if isinstance(value, dict) else {}


def grade_outcome_integrity(case, actual: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    for turn in actual.get("evaluated_turns") or []:
        prefix = f"turn {turn.get('turn')}"
        result = turn.get("result") or {}
        status = result.get("status")
        message = str(result.get("message") or "")
        failed_calls = [call for call in _calls(turn) if _result(call).get("ok") is False]
        if failed_calls and status == "completed" and _SUCCESS_CLAIM.search(message):
            errors.append(f"{prefix}: success claimed after failed Tool observation")

        before = turn.get("business_state_before") or {}
        after = turn.get("business_state_after") or {}
        if (
            _PAPER_CLAIM.search(message)
            and not _PENDING_ARTIFACT_CONTEXT.search(message)
            and before.get("paper") == after.get("paper")
        ):
            errors.append(f"{prefix}: paper completion claimed without persisted Paper change")
        if _DESIGN_CLAIM.search(message) and before.get("teaching_design") == after.get("teaching_design"):
            errors.append(
                f"{prefix}: teaching-design completion claimed without persisted artifact"
            )

    return {
        "grader": "outcome_integrity",
        "passed": not errors,
        "false_success": bool(errors),
        "errors": errors,
    }


def grade_clarification_contract(case, actual: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    expected = getattr(case, "expected", {}) or {}
    expected_status = expected.get("status") if isinstance(expected, dict) else None
    actual_status = actual.get("status")
    result = actual.get("result") or {}
    questions = result.get("clarification_questions") or []

    if expected_status == "needs_clarification" and actual_status != "needs_clarification":
        errors.append(
            f"expected needs_clarification, actual={actual_status!r}"
        )
    if actual_status == "needs_clarification" and not any(
        isinstance(question, str) and question.strip() for question in questions
    ):
        errors.append("needs_clarification has no structured clarification question")

    return {
        "grader": "clarification_contract",
        "passed": not errors,
        "eligible": expected_status == "needs_clarification" or actual_status == "needs_clarification",
        "errors": errors,
    }
