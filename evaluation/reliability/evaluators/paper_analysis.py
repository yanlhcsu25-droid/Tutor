"""Deterministic grading for the existing analyze_paper Tool output."""

from __future__ import annotations

from typing import Any


def _analysis_payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if isinstance(value.get("knowledge_distribution"), dict):
            return value
        for child in value.values():
            found = _analysis_payload(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _analysis_payload(child)
            if found is not None:
                return found
    return None


def grade_paper_analysis(case, actual: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    expected = config.get("knowledge_distribution") or {}
    observation = None
    for call in (actual.get("trace") or {}).get("tool_calls") or []:
        name = call.get("tool_name") or call.get("name") or call.get("tool")
        if name == "analyze_paper":
            observation = _analysis_payload(
                call.get("result") or call.get("output_json") or {}
            )
    if observation is None:
        errors.append("successful analyze_paper observation missing")
        actual_distribution: dict[str, int] = {}
    else:
        actual_distribution = observation.get("knowledge_distribution") or {}
        if observation.get("ok") is not True:
            errors.append("analyze_paper observation was not successful")
        if actual_distribution != expected:
            errors.append(
                "analyze_paper observation does not match the expected distribution"
            )

    expected_associations = config.get("association_count", sum(expected.values()))
    actual_associations = sum(actual_distribution.values())
    if actual_associations != expected_associations:
        errors.append(
            f"knowledge association count mismatch: expected={expected_associations}, "
            f"actual={actual_associations}"
        )

    dominant = config.get("dominant_knowledge")
    message = str(actual.get("message") or "")
    if dominant and dominant not in message:
        errors.append(f"response did not identify dominant knowledge: {dominant}")

    for turn in actual.get("evaluated_turns") or []:
        if turn.get("paper_before") != turn.get("paper_after"):
            errors.append(f"turn {turn.get('turn')}: paper changed during read-only analysis")

    return {
        "grader": "paper_analysis",
        "passed": not errors,
        "errors": errors,
        "details": {
            "expected_distribution": expected,
            "actual_distribution": actual_distribution,
            "association_count": actual_associations,
            "dominant_knowledge": dominant,
        },
    }
