from __future__ import annotations

from copy import deepcopy
from typing import Any


def _normalize_requirements(
    value: Any,
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        return {}

    result = {}

    for item in value:
        if not isinstance(item, dict):
            continue

        question_type = item.get("question_type")

        if question_type:
            result[question_type] = item

    return result


def _expected_modified_question_types(
    case,
) -> dict[str, dict[str, Any]]:
    generation = (
        case.expected.get("generation", {})
        if case.expected
        else {}
    )

    return _normalize_requirements(
        generation.get("question_type_requirements")
    )


def _compare_simple_field(
    field: str,
    before_generation: dict[str, Any],
    after_generation: dict[str, Any],
    errors: list[str],
) -> None:
    before_value = before_generation.get(field)
    after_value = after_generation.get(field)

    if before_value != after_value:
        errors.append(
            f"constraint not preserved: {field}; "
            f"before={before_value!r}, "
            f"after={after_value!r}"
        )


def _check_unspecified_question_types(
    case,
    before_generation: dict[str, Any],
    after_generation: dict[str, Any],
    errors: list[str],
) -> None:
    before_requirements = _normalize_requirements(
        before_generation.get(
            "question_type_requirements"
        )
    )

    after_requirements = _normalize_requirements(
        after_generation.get(
            "question_type_requirements"
        )
    )

    modified = _expected_modified_question_types(case)

    for question_type, before_item in before_requirements.items():

        after_item = after_requirements.get(question_type)

        if after_item is None:
            errors.append(
                f"question type disappeared: {question_type}"
            )
            continue

        # 整个题型没有被修改
        if question_type not in modified:
            if before_item != after_item:
                errors.append(
                    f"unspecified question type changed: "
                    f"{question_type}; "
                    f"before={before_item!r}, "
                    f"after={after_item!r}"
                )

            continue

        # 这个题型有部分字段被明确修改
        modified_fields = set(
            modified[question_type].keys()
        )

        modified_fields.discard("question_type")

        for key, before_value in before_item.items():
            if key == "question_type":
                continue

            if key in modified_fields:
                continue

            after_value = after_item.get(key)

            if before_value != after_value:
                errors.append(
                    f"{question_type}.{key} should be preserved; "
                    f"before={before_value!r}, "
                    f"after={after_value!r}"
                )


def grade_constraints(
    case,
    actual: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []

    preserve = case.preserve or []

    if not preserve:
        return {
            "grader": "constraint_preservation",
            "passed": True,
            "errors": [],
        }

    before = actual.get("before", {})

    before_generation = (
        before.get("generation") or {}
    )

    after_generation = (
        actual.get("generation") or {}
    )

    if not before_generation:
        return {
            "grader": "constraint_preservation",
            "passed": False,
            "errors": [
                "baseline generation state unavailable"
            ],
        }

    for rule in preserve:

        if rule == "paper_type":
            _compare_simple_field(
                "paper_type",
                before_generation,
                after_generation,
                errors,
            )

        elif rule == "scope_names":
            _compare_simple_field(
                "scope_names",
                before_generation,
                after_generation,
                errors,
            )

        elif rule == "total_score":
            _compare_simple_field(
                "total_score",
                before_generation,
                after_generation,
                errors,
            )

        elif rule in (
            "unspecified_question_types",
            "unspecified_constraints",
        ):
            _check_unspecified_question_types(
                case,
                before_generation,
                after_generation,
                errors,
            )

    return {
        "grader": "constraint_preservation",
        "passed": not errors,
        "errors": errors,
    }