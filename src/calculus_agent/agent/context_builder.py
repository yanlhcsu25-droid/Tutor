"""Construction of the context sent to the Teacher Agent model.

This module intentionally does not fetch or persist state.  The runtime supplies
all state snapshots and prompt parts; the builder only serializes and assembles
the model-visible messages.
"""

import json
from typing import Any, Iterable

from .context_metrics import ContextMetrics, measure_context


class AgentContextBuilder:
    """Build the initial LLM context for one agent turn."""

    def build(
        self,
        *,
        message: str,
        recent_messages: Iterable[dict[str, Any]],
        dynamic_context: dict[str, Any],
        system_parts: Iterable[str],
        tool_definitions: Iterable[dict[str, Any]] = (),
    ) -> tuple[list[dict[str, Any]], str, ContextMetrics]:
        """Return ``(messages, serialized_context, metrics)`` without changing inputs.

        Serialization and the placement of workspace context are deliberately
        kept identical to the runtime's previous inline implementation.
        """
        serialized_context = json.dumps(dynamic_context, ensure_ascii=False)
        assembled_system_parts = [*system_parts, "当前工作区上下文：" + serialized_context]
        system_content = "\n\n".join(assembled_system_parts)
        current_user_content = (
            message
            + "\n\n<current_workspace_state>"
            + serialized_context
            + "</current_workspace_state>"
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_content},
            *list(recent_messages),
            {"role": "user", "content": current_user_content},
        ]
        metrics = measure_context(
            messages=messages,
            tool_definitions=tool_definitions,
            serialized_context=serialized_context,
        )
        return messages, serialized_context, metrics
