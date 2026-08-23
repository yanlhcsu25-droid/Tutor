"""Low-level Tool Loop boundary for the Teacher Agent runtime.

This module owns model/tool interaction primitives.  Policy guards and state
transitions remain supplied by the runtime so this extraction does not alter
Agent behaviour.
"""

from __future__ import annotations

import json
from typing import Any

from .observation_projection import project_tool_observation


class ToolLoop:
    """Execute one model interaction and one validated Tool observation."""

    @staticmethod
    def run(backend: Any, messages: list[dict], definitions: list[dict]) -> dict:
        """Perform exactly one LLM call; round policy stays with the caller."""
        raw = backend.complete(messages, definitions)
        message = raw.get("message", raw)
        if not isinstance(message, dict):
            raise ValueError("agent_invalid_model_response")
        return message

    @staticmethod
    def parse_call(call: dict) -> tuple[str, dict]:
        function = call.get("function") or {}
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("agent_invalid_tool_call")
        raw = function.get("arguments", {})
        try:
            arguments = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("agent_invalid_tool_arguments") from exc
        if not isinstance(arguments, dict):
            raise ValueError("agent_invalid_tool_arguments")
        return name, arguments

    @staticmethod
    def execute(toolkit: Any, name: str, arguments: dict) -> Any:
        return toolkit.execute(name, arguments)

    @staticmethod
    def append_observation(
        messages: list[dict],
        *,
        call_id: str,
        name: str,
        payload: dict,
    ) -> None:
        messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "name": name,
            # Only the LLM-facing copy is projected; the full payload remains
            # available to the runtime and trace recorder.
            "content": json.dumps(
                project_tool_observation(name, payload),
                ensure_ascii=False,
                default=str,
            ),
        })
