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

    # Required lifecycle tools are observable acceptance boundaries. Additional
    # internal reads/retries may vary, but a write/confirm contract cannot pass
    # when its authoritative Tool observation is absent.
    required = config.get("required_tools") or []
    any_required = config.get("must_include_any") or []
    forbidden = config.get("forbidden_tools") or []
    missing_required = [name for name in required if name not in names]
    if missing_required:
        errors.append(f"required tools not called: {missing_required!r}")
    if any_required and not any(name in names for name in any_required):
        errors.append(f"none of required tools called: {any_required!r}")
    present_forbidden = [name for name in forbidden if name in names]
    if present_forbidden:
        errors.append(f"forbidden tools called: {present_forbidden!r}")

    expected_order = config.get("tool_order") or []
    cursor = 0
    for name in names:
        if cursor < len(expected_order) and name == expected_order[cursor]:
            cursor += 1
    if cursor != len(expected_order):
        errors.append(
            f"tool order not observed: expected subsequence={expected_order!r}, "
            f"actual={names!r}"
        )

    for name, expected_count in (config.get("tool_counts") or {}).items():
        actual_count = names.count(name)
        if actual_count != expected_count:
            errors.append(
                f"tool count mismatch for {name}: "
                f"expected={expected_count}, actual={actual_count}"
            )

    observed_codes = {
        result.get("code")
        for item in calls
        for result in [item.get("result") or item.get("output_json") or {}]
        if isinstance(result, dict) and result.get("code")
    }
    missing_codes = [
        code for code in (config.get("required_error_codes") or [])
        if code not in observed_codes
    ]
    if missing_codes:
        errors.append(f"required Tool error codes not observed: {missing_codes!r}")

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

    max_backend_calls = config.get("max_backend_calls")
    if max_backend_calls is not None and actual.get("backend_calls", 0) > max_backend_calls:
        errors.append(
            f"backend call limit exceeded: max={max_backend_calls}, "
            f"actual={actual.get('backend_calls')}"
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
