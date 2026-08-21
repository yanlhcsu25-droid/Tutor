"""LLM-facing projections of Tool observations.

The full Tool payload remains the authoritative result for state handling and
tracing. This module only removes runtime/audit internals from the copy placed
in the next LLM message.
"""

from __future__ import annotations

from typing import Any


_HIDDEN_OBSERVATION_KEYS = frozenset({
    "constraint_provenance",
    "curriculum_semantic_matches",
    "resolver_trace",
    "workflow_trace",
    "audit_fields",
    "metadata",
    "debug",
})


def project_tool_observation(
    tool_name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Return a behavior-neutral, JSON-safe LLM observation view."""
    del tool_name  # Reserved for future per-Tool projections.

    def project(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: project(item)
                for key, item in value.items()
                if key not in _HIDDEN_OBSERVATION_KEYS
            }
        if isinstance(value, list):
            return [project(item) for item in value]
        if isinstance(value, tuple):
            return [project(item) for item in value]
        return value

    result = project(payload)
    return result if isinstance(result, dict) else {"value": result}
