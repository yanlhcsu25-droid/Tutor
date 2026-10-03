from __future__ import annotations

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
    generation = {}
    if case.expected:
        generation = (
            case.expected.get("pending_generation")
            or case.expected.get("generation")
            or {}
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

    simple_fields = {
        "paper_type", "scope_names", "question_count", "total_score",
        "knowledge_preferences", "difficulty_level", "difficulty_ratio",
        "difficulty_preference", "diversity_preference", "question_type_requirements",
    }
    aliases = {
        "scope": "scope_names",
        "difficulty": "difficulty_level",
        "no_repeat": "diversity_preference",
    }

    for rule in preserve:
        field = aliases.get(rule, rule)
        if field in simple_fields:
            _compare_simple_field(
                field,
                before_generation,
                after_generation,
                errors,
            )
        elif rule in ("unspecified_question_types", "unspecified_constraints"):
            _check_unspecified_question_types(
                case,
                before_generation,
                after_generation,
                errors,
            )
            if rule == "unspecified_constraints":
                expected_generation = (
                    case.expected.get("pending_generation")
                    or case.expected.get("generation")
                    or {}
                )
                modified_fields = set(expected_generation)
                for candidate in simple_fields - modified_fields:
                    if candidate in before_generation:
                        _compare_simple_field(
                            candidate, before_generation, after_generation, errors,
                        )
        elif rule == "no_auto_confirm":
            calls = [
                call
                for turn in actual.get("evaluated_turns") or []
                for call in ((turn.get("state") or {}).get("trace") or {}).get("tool_calls") or []
            ]
            names = {
                call.get("tool_name") or call.get("name") or call.get("tool")
                for call in calls if isinstance(call, dict)
            }
            forbidden = {
                "confirm_generation", "confirm_paper_changes", "confirm_teaching_design",
            }
            if names & forbidden:
                errors.append(f"automatic confirmation observed: {sorted(names & forbidden)}")
        elif rule in {"no_cross_chapter", "no-cross-chapter", "no-repeat"}:
            # Evaluated by content_consistency against persisted Paper rows.
            continue
        else:
            errors.append(f"unsupported preserve rule: {rule}")

    return {
        "grader": "constraint_preservation",
        "passed": not errors,
        "errors": errors,
    }