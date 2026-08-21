"""Low-level Tool Loop boundary for the Teacher Agent runtime.

This module owns model/tool interaction primitives.  Policy guards and state
transitions remain supplied by the runtime so this extraction does not alter
Agent behaviour.
"""

from __future__ import annotations

import json
from typing import Any


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
        arguments = json.loads(raw) if isinstance(raw, str) else raw
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
            # Tool failures may carry exception objects from validation or
            # adapter boundaries. Keep the normal JSON payload unchanged while
            # making the observation transport total.
            "content": json.dumps(payload, ensure_ascii=False, default=str),
        })
