from __future__ import annotations

from typing import Any


def _get_total_score(
    obj: dict[str, Any] | None,
) -> int | float | None:
    if not isinstance(obj, dict):
        return None

    value = obj.get("total_score")

    if isinstance(value, (int, float)):
        return value

    return None


def _find_expected_score(case) -> int | float | None:
    expected = case.expected or {}

    for key in (
        "generation",
        "pending_generation",
        "paper",
    ):
        score = _get_total_score(expected.get(key))

        if score is not None:
            return score

    return None


def _find_actual_score(
    actual: dict[str, Any],
) -> int | float | None:
    for key in (
        "paper",
        "pending_generation",
        "generation",
    ):
        score = _get_total_score(actual.get(key))

        if score is not None:
            return score

    return None


def _check_question_type_math(
    generation: dict[str, Any] | None,
) -> list[str]:
    if not isinstance(generation, dict):
        return []

    requirements = generation.get(
        "question_type_requirements",
        [],
    )

    if not isinstance(requirements, list):
        return []

    errors: list[str] = []

    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue

        count = requirement.get("count")
        score_each = requirement.get("score_each")
        total_score = requirement.get("total_score")

        if (
            isinstance(count, (int, float))
            and isinstance(score_each, (int, float))
            and isinstance(total_score, (int, float))
        ):
            calculated = count * score_each

            if calculated != total_score:
                question_type = requirement.get(
                    "question_type",
                    "unknown",
                )

                errors.append(
                    f"{question_type}: "
                    f"count({count}) * score_each({score_each}) "
                    f"= {calculated}, "
                    f"but total_score={total_score}"
                )

    return errors


def grade_score(
    case,
    actual: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []

    expected_score = _find_expected_score(case)
    actual_score = _find_actual_score(actual)

    if expected_score is not None:
        if actual_score is None:
            errors.append(
                f"expected total_score={expected_score}, "
                "but actual total_score is unavailable"
            )

        elif actual_score != expected_score:
            errors.append(
                f"expected total_score={expected_score}, "
                f"actual={actual_score}"
            )

    errors.extend(
        _check_question_type_math(
            actual.get("generation")
        )
    )

    errors.extend(
        _check_question_type_math(
            actual.get("pending_generation")
        )
    )

    return {
        "grader": "score",
        "passed": not errors,
        "errors": errors,
        "expected_total_score": expected_score,
        "actual_total_score": actual_score,
    }