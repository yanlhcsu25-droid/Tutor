from __future__ import annotations

from typing import Any


MISSING = object()


def get_nested(data: Any, path: str) -> Any:
    current = data

    for part in path.split("."):
        if not isinstance(current, dict):
            return MISSING

        if part not in current:
            return MISSING

        current = current[part]

    return current


def _compare_subset(
    expected: Any,
    actual: Any,
    *,
    path: str = "",
) -> list[str]:
    """
    expected 是 actual 的子集即可。

    例如：
    expected:
        generation:
            total_score: 85

    actual 可以包含 generation 的其他字段。
    """
    errors: list[str] = []

    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [
                f"{path or '<root>'}: expected mapping, "
                f"actual={actual!r}"
            ]

        for key, expected_value in expected.items():
            child_path = f"{path}.{key}" if path else key

            if key not in actual:
                errors.append(
                    f"{child_path}: missing in actual state"
                )
                continue

            errors.extend(
                _compare_subset(
                    expected_value,
                    actual[key],
                    path=child_path,
                )
            )

        return errors

    if isinstance(expected, list):
        if not isinstance(actual, list):
            return [
                f"{path}: expected list {expected!r}, "
                f"actual={actual!r}"
            ]

        # 对 question_type_requirements 等结构化 list，
        # 采用 subset matching，而不是强制顺序完全相等。
        if expected and all(isinstance(x, dict) for x in expected):
            for expected_item in expected:
                matched = False

                for actual_item in actual:
                    if not isinstance(actual_item, dict):
                        continue

                    if not _compare_subset(
                        expected_item,
                        actual_item,
                        path=path,
                    ):
                        matched = True
                        break

                if not matched:
                    errors.append(
                        f"{path}: expected item not found: "
                        f"{expected_item!r}"
                    )

            return errors

        if expected != actual:
            errors.append(
                f"{path}: expected={expected!r}, actual={actual!r}"
            )

        return errors

    if expected != actual:
        errors.append(
            f"{path}: expected={expected!r}, actual={actual!r}"
        )

    return errors


def grade_state(
    case,
    actual: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []

    expected = case.expected or {}

    # expected 只检查实际定义的部分
    if expected:
        errors.extend(
            _compare_subset(expected, actual)
        )

    forbidden = case.forbidden or {}

    # -----------------------------
    # forbidden statuses
    # -----------------------------

    forbidden_statuses = forbidden.get("statuses", [])

    actual_status = actual.get("status")

    if actual_status in forbidden_statuses:
        errors.append(
            f"forbidden status observed: {actual_status}"
        )

    # -----------------------------
    # forbidden active task types
    # -----------------------------

    forbidden_task_types = forbidden.get(
        "active_task_types",
        [],
    )

    active_task = actual.get("active_task") or {}

    task_type = (
        active_task.get("type")
        if isinstance(active_task, dict)
        else None
    )

    if task_type in forbidden_task_types:
        errors.append(
            f"forbidden active_task.type observed: {task_type}"
        )

    # -----------------------------
    # forbidden response claims
    # -----------------------------

    response_claims = forbidden.get(
        "response_claims",
        [],
    )

    message = actual.get("message") or ""

    for claim in response_claims:
        if claim in message:
            errors.append(
                f"forbidden response claim found: {claim!r}"
            )

    # -----------------------------
    # forbidden tools
    # -----------------------------

    forbidden_tools = forbidden.get("tools", [])

    tool_calls = (
        actual.get("trace", {}).get("tool_calls", [])
    )

    called_tool_names = []

    for item in tool_calls:
        if isinstance(item, dict):
            name = (
                item.get("name")
                or item.get("tool")
                or item.get("tool_name")
            )

            if name:
                called_tool_names.append(name)

    for tool_name in forbidden_tools:
        if tool_name in called_tool_names:
            errors.append(
                f"forbidden tool called: {tool_name}"
            )

    return {
        "grader": "state",
        "passed": not errors,
        "errors": errors,
    }