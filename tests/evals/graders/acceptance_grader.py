"""Business-facing acceptance checks for Teacher Agent eval cases."""

from __future__ import annotations

from typing import Any


def _tool_calls(actual: dict[str, Any]) -> list[dict[str, Any]]:
    calls = (actual.get("trace") or {}).get("tool_calls") or []
    return [item for item in calls if isinstance(item, dict)]


def _tool_name(item: dict[str, Any]) -> str | None:
    value = item.get("tool_name") or item.get("name") or item.get("tool")
    return value if isinstance(value, str) else None


def grade_acceptance(
    case,
    actual: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Check observable business behavior without judging prompt wording."""
    errors: list[str] = []
    calls = _tool_calls(actual)
    names = [_tool_name(item) for item in calls]
    names = [name for name in names if name]

    # Tool calls are diagnostic evidence only.  Acceptance is outcome-first:
    # internal orchestration may add, remove, or reorder observations without
    # changing the business contract.  Only explicitly forbidden tools remain
    # hard assertions.
    required = config.get("required_tools") or []
    any_required = config.get("must_include_any") or []
    forbidden = config.get("forbidden_tools") or []
    present_forbidden = [name for name in forbidden if name in names]
    if present_forbidden:
        errors.append(f"forbidden tools called: {present_forbidden!r}")

    allowed_statuses = config.get("statuses") or []
    if allowed_statuses and actual.get("status") not in allowed_statuses:
        errors.append(
            f"unexpected final status: expected one of {allowed_statuses!r}, "
            f"actual={actual.get('status')!r}"
        )

    if config.get("require_trace") and not (actual.get("trace") or {}).get("run_id"):
        errors.append("local run trace is missing")

    if config.get("require_failed_tool"):
        failures = []
        for item in calls:
            result = item.get("result") or item.get("output_json") or {}
            if isinstance(result, dict) and result.get("ok") is False:
                failures.append(item)
        if not failures:
            errors.append("no failed Tool Observation found in trace")

    if config.get("paper_unchanged"):
        before = (actual.get("before") or {}).get("paper_db")
        after = actual.get("paper_db")
        if before != after:
            errors.append(
                f"paper changed before confirmation: before={before!r}, after={after!r}"
            )

    if config.get("require_confirmation"):
        if "confirm_generation" not in names and "confirm_paper_changes" not in names:
            errors.append("confirmation tool was not called")

    return {
        "grader": "acceptance",
        "passed": not errors,
        "errors": errors,
        "details": {
            "expected_tools": required,
            "expected_tools_any": any_required,
            "actual_tools": names,
            "status": actual.get("status"),
        },
    }
